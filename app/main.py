from __future__ import annotations

import os
import datetime as dt

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .core import AMADEUS_HOST, CURRENCY, amadeus_token, city_to_iata
from .schemas import SearchBody
from .utils import count_stops, rank_offers, to_lite
from .chat_router import router as chat_router
from .discover_router import router as discover_router

app = FastAPI(title="ITNR API")

# ---- CORS (autoriser ton front + dev local) ----
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://itnr-front.onrender.com,http://localhost:5500,http://127.0.0.1:5500",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED_ORIGINS if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=86400,
)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---- Autocomplétion villes/aéroports ----
@app.get("/locations")
async def locations(q: str = Query(..., min_length=2), subtype: str = "CITY,AIRPORT", limit: int = 7):
    async with httpx.AsyncClient(timeout=15.0) as client:
        token = await amadeus_token(client)
        r = await client.get(
            f"{AMADEUS_HOST}/v1/reference-data/locations",
            params={"subType": subtype, "keyword": q, "page[limit]": limit, "sort": "analytics.travelers.score"},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        data = r.json().get("data", []) or []

    out = []
    for x in data:
        out.append(
            {
                "subType": x.get("subType"),
                "iataCode": x.get("iataCode"),
                "name": x.get("name"),
                "cityCode": x.get("address", {}).get("cityCode"),
                "cityName": x.get("address", {}).get("cityName"),
                "countryCode": x.get("address", {}).get("countryCode"),
                "label": (
                    f"{x.get('name')} ({x.get('iataCode')})"
                    if x.get("subType") == "AIRPORT"
                    else f"{x.get('name')} — ville ({x.get('iataCode')})"
                ),
            }
        )
    return {"items": out[:limit]}


def resolve_dates(body: SearchBody) -> tuple[str, str]:
    if body.departureDate and body.returnDate:
        return body.departureDate, body.returnDate
    if body.period:
        d0 = dt.date.fromisoformat(body.period.start)
        d1 = d0 + dt.timedelta(days=int(body.period.durationDays))
        return d0.isoformat(), d1.isoformat()
    raise HTTPException(400, "Dates invalides (départ/retour OU period.start+durationDays requis)")


# ---- Recherche AR classique, réponse "lite" pour le front ----
@app.post("/search")
async def search(body: SearchBody):
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await amadeus_token(client)
        origin = await city_to_iata(client, token, body.originCity)
        dest = await city_to_iata(client, token, body.destinationCity or "")
        dep, ret = resolve_dates(body)

        pax_total = max(
            1, body.passengers.adults + body.passengers.children + body.passengers.infants
        )

        params = {
            "originLocationCode": origin,
            "destinationLocationCode": dest,
            "departureDate": dep,
            "returnDate": ret,
            "adults": body.passengers.adults or 1,
            "children": body.passengers.children or 0,
            "infants": body.passengers.infants or 0,
            "currencyCode": CURRENCY,
            "nonStop": "false",
            "max": 50,
            "travelClass": "ECONOMY",
        }
        r = await client.get(
            f"{AMADEUS_HOST}/v2/shopping/flight-offers",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return JSONResponse(status_code=e.response.status_code, content={"error": e.response.text})

        offers = r.json().get("data", []) or []

        filtered = []
        for o in offers:
            if count_stops(o) > body.maxStops:
                continue
            price_per_pax = float(o["price"]["grandTotal"]) / pax_total
            if body.budgetPerPaxEUR is not None and price_per_pax > float(body.budgetPerPaxEUR):
                continue
            filtered.append(o)

        ranked = rank_offers(filtered)

        def _lite(x):
            return to_lite(x, pax_total) if x else None

        results = {
            "cheapest": _lite(ranked.get("cheapest")),
            "recommended": _lite(ranked.get("recommended")),
            "direct": _lite(ranked.get("direct")),
        }
        return {
            "results": results,
            "meta": {
                "searched": {
                    **params,
                    "originCity": body.originCity,
                    "destinationCity": body.destinationCity,
                },
                "totalCandidates": len(offers),
                "kept": len(filtered),
            },
        }


# ---- routeurs ----
app.include_router(chat_router)
app.include_router(discover_router)
