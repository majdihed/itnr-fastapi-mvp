# app/main.py
import datetime as dt

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .core import AMADEUS_HOST, CURRENCY, amadeus_token, city_to_iata
from .schemas import SearchBody
from .utils import count_stops, rank_offers

load_dotenv()

app = FastAPI(title="ITNR API")


@app.get("/health")
def health():
    return {"status": "ok"}


def resolve_dates(body: SearchBody) -> tuple[str, str]:
    if body.departureDate and body.returnDate:
        return body.departureDate, body.returnDate
    if body.period:
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
        dest = await city_to_iata(client, token, body.destinationCity)
        dep, ret = resolve_dates(body)

        pax_total = max(
            1,
            body.passengers.adults + body.passengers.children + body.passengers.infants,
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
        try:
            r = await client.get(
                f"{AMADEUS_HOST}/v2/shopping/flight-offers",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            return JSONResponse(
                status_code=e.response.status_code,
                content={"error": e.response.text},
            )

        offers = r.json().get("data", [])

        filtered = []
        for o in offers:
            if count_stops(o) > body.maxStops:
                continue
            price_per_pax = float(o["price"]["grandTotal"]) / pax_total
            if (
                body.budgetPerPaxEUR is not None
                and price_per_pax > body.budgetPerPaxEUR
            ):
                continue
            filtered.append(o)

        ranked = rank_offers(filtered)
        return {
            "results": ranked,
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


# Import au top-level pour éviter E402 et ne plus créer de boucle
from .chat_router import router as chat_router  # noqa: E402  (si tu veux vraiment éviter E402)
app.include_router(chat_router)
