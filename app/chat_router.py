# app/chat_router.py
import datetime as dt
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from .core import AMADEUS_HOST, CURRENCY, amadeus_token, city_to_iata
from .utils import count_stops, rank_offers, to_lite

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

router = APIRouter(prefix="/chat", tags=["chat"])

SYSTEM = (
    "Tu es un assistant de voyage. "
    "Tu reçois une requête en français et tu renvoies un JSON strict "
    "avec les critères vols aller-retour. "
    "Uniquement du JSON conforme au schéma."
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
                "additionalProperties": False,
            },
            "departureDate": {"type": "string", "format": "date"},
            "returnDate": {"type": "string", "format": "date"},
            "passengers": {
                "type": "object",
                "properties": {
                    "adults": {"type": "integer", "minimum": 1},
                    "children": {"type": "integer", "minimum": 0},
                    "infants": {"type": "integer", "minimum": 0},
                },
                "required": ["adults", "children", "infants"],
                "additionalProperties": False,
            },
            "maxStops": {"type": "integer", "minimum": 0, "maximum": 2},
            "budgetPerPaxEUR": {"type": "number", "minimum": 0},
            "flexDays": {"type": "integer", "minimum": 0, "maximum": 3},
        },
        "required": ["originCity", "destinationCity", "passengers"],
    },
    "strict": True,
}


def _client() -> Any:
    if OpenAI is None:
        raise HTTPException(500, "openai SDK non installé. pip install openai")
    return OpenAI()


def _complete_dates(parsed: dict) -> None:
    if "period" in parsed and (
        "departureDate" not in parsed or "returnDate" not in parsed
    ):
        start = dt.date.fromisoformat(parsed["period"]["start"])
        dur = int(parsed["period"]["durationDays"])
        parsed["departureDate"] = start.isoformat()
        parsed["returnDate"] = (start + dt.timedelta(days=dur)).isoformat()


def _default_passengers(parsed: dict) -> None:
    if "passengers" not in parsed:
        parsed["passengers"] = {"adults": 1, "children": 0, "infants": 0}


@router.post("")
async def chat_query(payload: dict):
    user_msg = payload.get("message", "").strip()
    if not user_msg:
        raise HTTPException(400, "message manquant")

    client = _client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_schema", "json_schema": JSON_SCHEMA},
        )
        parsed = resp.output_parsed or {}
    except Exception as e:
        raise HTTPException(500, f"OpenAI parsing error: {e}") from e

    _default_passengers(parsed)
    if "maxStops" not in parsed:
        parsed["maxStops"] = 1
    _complete_dates(parsed)

    pax_total = max(
        1,
        parsed["passengers"]["adults"]
        + parsed["passengers"]["children"]
        + parsed["passengers"]["infants"],
    )

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

        try:
            r = await client_http.get(
                f"{AMADEUS_HOST}/v2/shopping/flight-offers",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                e.response.status_code, f"Amadeus error: {e.response.text[:200]}"
            ) from e

        offers = r.json().get("data", []) or []

    filtered = []
    for o in offers:
        if count_stops(o) > parsed["maxStops"]:
            continue
        price_per_pax = float(o["price"]["grandTotal"]) / pax_total
        if (
            parsed.get("budgetPerPaxEUR") is not None
            and price_per_pax > float(parsed["budgetPerPaxEUR"])
        ):
            continue
        filtered.append(o)

    ranked = rank_offers(filtered)

    def lite(o):
        return to_lite(o, pax_total) if o else None

    results = {
        "cheapest": lite(ranked.get("cheapest")),
        "recommended": lite(ranked.get("recommended")),
        "direct": lite(ranked.get("direct")),
    }

    meta = {
        "searched": {
            "originLocationCode": origin,
            "destinationLocationCode": dest,
            "departureDate": parsed.get("departureDate"),
            "returnDate": parsed.get("returnDate"),
            **parsed,
        },
        "totalCandidates": len(offers),
        "kept": len(filtered),
    }

    return {"parsed": parsed, "results": results, "meta": meta}
