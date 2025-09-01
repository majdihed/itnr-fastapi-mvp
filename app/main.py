from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .core import AMADEUS_HOST, CURRENCY, amadeus_token, city_to_iata
from .schemas import SearchBody
from .utils import count_stops, rank_offers, to_lite
from .chat_router import router as chat_router
from .discover_router import router as discover_router

load_dotenv()

app = FastAPI(title="ITNR API")

# --- CORS ---
_allowed = os.getenv("ALLOWED_ORIGINS", "")
origins = [o.strip() for o in _allowed.split(",") if o.strip()]
if not origins:
    # Par défaut : autorise tout en dev; pense à configurer ALLOWED_ORIGINS en prod
    origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routers (chat et discover) ---
app.include_router(chat_router)
app.include_router(discover_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/locations")
async def locations(q: str):
    """
    Autocomplétion villes/aéroports via Amadeus.
    q : mot-clé saisi (ex: "par").
    """
    if not q or len(q) < 2:
        return {"data": []}

    async with httpx.AsyncClient(timeout=10.0) as client:
        token = await amadeus_token(client)
        r = await client.get(
            f"{AMADEUS_HOST}/v1/reference-data/locations",
            params={
                "subType": "CITY,AIRPORT",
                "keyword": q,
                "sort": "analytics.travelers.score",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            msg = f"Amadeus locations error: {e.response.text[:200]}"
            raise HTTPException(e.response.status_code, msg) from e

        data = r.json().get("data", []) or []
        out = []
        for x in data[:10]:
            out.append(
                {
                    "iataCode": x.get("iataCode"),
                    "name": x.get("name"),
                    "subType": x.get("subType"),
                    "cityName": (x.get("address") or {}).get("cityName"),
                    "country": (x.get("address") or {}).get("countryName"),
                }
            )
        return {"data": out}


def _resolve_dates(body: SearchBody) -> tuple[str, str]:
    if body.departureDate and body.returnDate:
        return body.departureDate, body.returnDate
    if body.period:
        import datetime as dt

        d0 = dt.date.fromisoformat(body.period.start)
        d1 = d0 + dt.timedelta(days=int(body.period.durationDays))
        return d0.isoformat(), d1.isoformat()
    raise HTTPException(
        400,
        "Dates invalides (départ/retour OU period.start+durationDays requis)",
    )


@app.post("/search")
async def search(body: SearchBody):
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await amadeus_token(client)
        origin = await city_to_iata(client, token, body.originCity)
        dest_city = body.destinationCity or ""
        if not dest_city:
            raise HTTPException(400, "destinationCity manquante")
        dest = await city_to_iata(client, token, dest_city)
        dep, ret = _resolve_dates(body)

        pax_total = max(
            1,
            body.passengers.adults
            + body.passengers.children
            + body.passengers.infants,
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
            return JSONResponse(
                status_code=e.response.status_code,
                content={"error": e.response.text},
            )

        offers = r.json().get("data", []) or []

        # Filtrage
        filtered = []
        for o in offers:
            if count_stops(o) > body.maxStops:
                continue
            price_total = float(o.get("price", {}).get("grandTotal", "0") or 0)
            price_per_pax = price_total / pax_total
            max_budget = body.budgetPerPaxEUR
            if max_budget is not None and price_per_pax > float(max_budget):
                continue
            filtered.append(o)

        ranked = rank_offers(filtered)

        def _lite(x):
            return to_lite(x, pax_total) if x else None

        return {
            "results": {
                "cheapest": _lite(ranked.get("cheapest")),
                "recommended": _lite(ranked.get("recommended")),
                "direct": _lite(ranked.get("direct")),
            },
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
