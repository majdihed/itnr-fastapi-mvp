import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx
import datetime as dt
from .schemas import SearchBody
from .utils import count_stops, total_duration_minutes, rank_offers, to_lite

load_dotenv()

AMADEUS_HOST = os.getenv("AMADEUS_HOST", "https://test.api.amadeus.com")
CLIENT_ID = os.getenv("AMADEUS_CLIENT_ID")
CLIENT_SECRET = os.getenv("AMADEUS_CLIENT_SECRET")
CURRENCY = os.getenv("DEFAULT_CURRENCY", "EUR")

app = FastAPI(title="ITNR API")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # en local, on autorise tout
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}

async def amadeus_token(client: httpx.AsyncClient) -> str:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise HTTPException(500, "AMADEUS_CLIENT_ID / SECRET non configurés (.env)")
    data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    r = await client.post(
        f"{AMADEUS_HOST}/v1/security/oauth2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, f"Amadeus auth failed: {e.response.text}")
    return r.json().get("access_token")

async def city_to_iata(client: httpx.AsyncClient, token: str, name: str) -> str:
    r = await client.get(
        f"{AMADEUS_HOST}/v1/reference-data/locations",
        params={"subType": "CITY,AIRPORT", "keyword": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        raise HTTPException(400, f"Ville introuvable: {name}")
    for x in data:
        if x.get("subType") == "CITY":
            return x["iataCode"]
    return data[0]["iataCode"]

def resolve_dates(body: SearchBody) -> tuple[str, str]:
    if body.departureDate and body.returnDate:
        return body.departureDate, body.returnDate
    if body.period:
        d0 = dt.date.fromisoformat(body.period.start)
        d1 = d0 + dt.timedelta(days=int(body.period.durationDays))
        return d0.isoformat(), d1.isoformat()
    raise HTTPException(400, "Dates invalides (départ/retour OU period.start+durationDays requis)")

@app.post("/search")
async def search(body: SearchBody):
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await amadeus_token(client)
        origin = await city_to_iata(client, token, body.originCity)
        dest = await city_to_iata(client, token, body.destinationCity)
        dep, ret = resolve_dates(body)

        pax_total = max(1, body.passengers.adults + body.passengers.children + body.passengers.infants)

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

        offers = r.json().get("data", [])

        # Filtrage métier
        filtered = []
        for o in offers:
            if count_stops(o) > body.maxStops:
                continue
            price_per_pax = float(o["price"]["grandTotal"]) / pax_total
            if body.budgetPerPaxEUR is not None and price_per_pax > body.budgetPerPaxEUR:
                continue
            filtered.append(o)

        ranked = rank_offers(filtered)

        def lite(o):
            return to_lite(o, pax_total) if o else None

        return {
            "results": {
                "cheapest": lite(ranked.get("cheapest")),
                "recommended": lite(ranked.get("recommended")),
                "direct": lite(ranked.get("direct")),
            },
            "meta": {
                "searched": {**params, "originCity": body.originCity, "destinationCity": body.destinationCity},
                "totalCandidates": len(offers),
                "kept": len(filtered),
            },
        }
