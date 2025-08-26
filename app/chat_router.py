# app/chat_router.py
import os
import datetime as dt
from typing import Any, Dict
from fastapi import APIRouter, HTTPException
import httpx

from .schemas import SearchBody
from .main import amadeus_token, city_to_iata, CURRENCY, AMADEUS_HOST  # reuse from your app

# OpenAI
try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

router = APIRouter(prefix="/chat", tags=["chat"])

SYSTEM = (
    "Tu es un assistant de voyage. "
    "Tu reçois une requête en langue naturelle (français) et tu renvoies un JSON strict "
    "avec les critères de recherche vols aller-retour. "
    "Ne propose pas de blabla, uniquement du JSON qui matche le schéma."
)

JSON_SCHEMA = {
    "name": "flight_query",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "originCity": {"type": "string"},
            "destinationCity": {"type": "string"},
            "period": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "format": "date"},
                    "durationDays": {"type": "integer", "minimum": 1},
                },
                "required": ["start", "durationDays"],
                "additionalProperties": False
            },
            "departureDate": {"type": "string", "format": "date"},
            "returnDate": {"type": "string", "format": "date"},
            "passengers": {
                "type": "object",
                "properties": {
                    "adults": {"type": "integer", "minimum": 1},
                    "children": {"type": "integer", "minimum": 0},
                    "infants": {"type": "integer", "minimum": 0}
                },
                "required": ["adults","children","infants"],
                "additionalProperties": False
            },
            "maxStops": {"type": "integer", "minimum": 0, "maximum": 2},
            "budgetPerPaxEUR": {"type": "number", "minimum": 0},
            "flexDays": {"type": "integer", "minimum": 0, "maximum": 3}
        },
        "required": ["originCity","destinationCity","passengers"],
    },
    "strict": True,
}

def _client() -> Any:
    if OpenAI is None:
        raise HTTPException(500, "openai SDK non installé. pip install openai")
    return OpenAI()

@router.post("")
async def chat_query(payload: Dict[str, str]):
    """
    payload = { "message": "Trouve moi un AR Paris Bangkok entre janvier et février, ~3 semaines..." }
    Retourne: { parsed, offers } où offers = résultat compact (cheapest/recommended/direct) dans une version à venir.
    """
    user_msg = payload.get("message", "").strip()
    if not user_msg:
        raise HTTPException(400, "message manquant")

    # 1) Appel OpenAI pour parser en JSON
    client = _client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": JSON_SCHEMA,
            },
        )
        parsed = resp.output_parsed or {}
    except Exception as e:
        raise HTTPException(500, f"OpenAI parsing error: {e}")

    # Post-traitement défauts
    if "passengers" not in parsed:
        parsed["passengers"] = {"adults": 1, "children": 0, "infants": 0}
    if "maxStops" not in parsed:
        parsed["maxStops"] = 1

    # Si l'utilisateur a donné une "période", calcule départ/retour
    if "period" in parsed and ("departureDate" not in parsed or "returnDate" not in parsed):
        start = dt.date.fromisoformat(parsed["period"]["start"])
        dur = int(parsed["period"]["durationDays"])
        parsed["departureDate"] = start.isoformat()
        parsed["returnDate"] = (start + dt.timedelta(days=dur)).isoformat()

    # 2) Requête Amadeus avec les critères parsés
    async with httpx.AsyncClient(timeout=30.0) as client_http:
        token = await amadeus_token(client_http)
        origin = await city_to_iata(client_http, token, parsed["originCity"])
        dest = await city_to_iata(client_http, token, parsed["destinationCity"])

        params = {
            "originLocationCode": origin,
            "destinationLocationCode": dest,
            "departureDate": parsed.get("departureDate"),
            "returnDate": parsed.get("returnDate"),
            "adults": parsed["passengers"]["adults"],
            "children": parsed["passengers"]["children"],
            "infants": parsed["passengers"]["infants"],
            "currencyCode": CURRENCY,
            "nonStop": "false",
            "max": 50,
            "travelClass": "ECONOMY",
        }

        r = await client_http.get(
            f"{AMADEUS_HOST}/v2/shopping/flight-offers",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        offers = r.json().get("data", [])

    return {"parsed": parsed, "rawOffersCount": len(offers), "amadeus": {"params": params, "data": offers}}
