# app/chat_router.py
import datetime as dt
import json
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
    "Ta tâche est d'extraire des critères de vols aller-retour depuis un message en français "
    "et de retourner UNIQUEMENT un objet JSON conforme au schéma suivant. "
    "Ne renvoie pas de texte ou de commentaires, uniquement du JSON."
)

# Description du schéma attendue par le modèle (texte, pour guider la génération JSON)
SCHEMA_TEXT = """
{
  "originCity": "string (ex: Paris)",
  "destinationCity": "string (ex: Bangkok)",
  "period": {
    "start": "YYYY-MM-DD",
    "durationDays": "integer >= 1"
  },
  "departureDate": "YYYY-MM-DD",
  "returnDate": "YYYY-MM-DD",
  "passengers": {
    "adults": "integer >= 1",
    "children": "integer >= 0",
    "infants": "integer >= 0"
  },
  "maxStops": "integer 0..2",
  "budgetPerPaxEUR": "number >= 0",
  "flexDays": "integer 0..3"
}

Règles:
- Si l'utilisateur donne des dates précises, remplir departureDate et returnDate.
- Si l'utilisateur donne une période + une durée (ex. 'entre janvier et février, ~3 semaines'), remplir period.start et period.durationDays.
- Toujours inclure passengers (adults, children, infants). Par défaut: 1 adulte, 0 enfant, 0 bébé.
- Si maxStops n'est pas précisé, mettre 1.
- Ne renvoie que des champs utiles. Aucune explication hors JSON.
"""

def _client() -> Any:
    if OpenAI is None:
        raise HTTPException(500, "openai SDK non installé. pip install openai")
    return OpenAI()

def _complete_dates(parsed: dict) -> None:
    # Si l'utilisateur a fourni une période + durée, compléter departureDate/returnDate
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

    # Appel Chat Completions en JSON mode
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM + "\n\nSchéma attendu:\n" + SCHEMA_TEXT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = resp.choices[0].message.content or "{}"
        parsed = json.loads(content)
    except Exception as e:
        raise HTTPException(500, f"OpenAI parsing error: {e}") from e

    # Defaults & complétions
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

    # Appel Amadeus
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

    # Filtrage budget/escales
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
