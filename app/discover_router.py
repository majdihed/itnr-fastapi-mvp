from __future__ import annotations

import datetime as dt
import math

import httpx
from fastapi import APIRouter, HTTPException

from .core import AMADEUS_HOST, CURRENCY, amadeus_token, city_to_iata
from .utils import count_stops, rank_offers, to_lite

router = APIRouter(prefix="/discover", tags=["discover"])


async def geocode(client: httpx.AsyncClient, city: str) -> dict | None:
    r = await client.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "fr", "format": "json"},
        timeout=10.0,
    )
    if r.status_code != 200:
        return None
    d = r.json()
    return (d.get("results") or [None])[0]


async def climate_score(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    month: int,
) -> dict:
    url = "https://climate-api.open-meteo.com/v1/climate"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_year": 1991,
        "end_year": 2020,
        "models": "ERA5",
        "month": month,
        "temperature_2m_mean": "true",
        "precipitation_sum": "true",
    }
    temp_c: float | None = None
    rain_mm: float | None = None
    try:
        r = await client.get(url, params=params, timeout=12.0)
        r.raise_for_status()
        d = r.json()
        temp_c = (d.get("monthly", {}) or d).get("temperature_2m_mean", [None])[0]
        rain_mm = (d.get("monthly", {}) or d).get("precipitation_sum", [None])[0]
    except Exception:
        pass

    def tscore(x: float | None) -> float:
        if x is None:
            return 0.5
        if x <= 18:
            return 0.2
        if 18 < x <= 22:
            return 0.2 + (x - 18) * (0.6 / 4)
        if 22 < x <= 30:
            return 0.8 + (x - 22) * (0.2 / 8)
        if 30 < x <= 34:
            return 1.0 - (x - 30) * (0.4 / 4)
        return 0.4

    def rpenalty(mm: float | None) -> float:
        if mm is None:
            return 0.0
        return min(0.6, (mm / 60.0) * 0.15)

    base = tscore(temp_c)
    sun = max(0.0, min(1.0, base - rpenalty(rain_mm)))
    return {"temp_c": temp_c, "rain_mm": rain_mm, "sun_score": sun}


def normalize(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.5
    return (x - lo) / (hi - lo)


async def inspiration_candidates(
    client: httpx.AsyncClient,
    token: str,
    origin_iata: str,
    dep: str,
    ret: str,
    limit: int = 25,
) -> list[dict]:
    """
    Amadeus Flight Inspiration Search.
    Certains comptes utilisent 'origin' plutôt que 'originLocationCode'.
    """
    params_v1 = {
        "origin": origin_iata,
        "departureDate": dep,
        "oneWay": "false",
        "viewBy": "DESTINATION",
    }
    params_v2 = {"originLocationCode": origin_iata, "departureDate": dep}

    for params in (params_v1, params_v2):
        try:
            r = await client.get(
                f"{AMADEUS_HOST}/v1/shopping/flight-destinations",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=20.0,
            )
            r.raise_for_status()
            data = r.json().get("data", []) or []
            out: list[dict] = []
            for x in data:
                dest = x.get("destination") or x.get("destinationLocationCode")
                price = (x.get("price") or {}).get("total")
                if not dest or not price:
                    continue
                out.append({"destination": dest, "price_total": float(price)})
            out.sort(key=lambda z: z["price_total"])
            return out[:limit]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403, 404):
                continue
            raise
    return []


async def location_info(client: httpx.AsyncClient, token: str, code: str) -> dict:
    r = await client.get(
        f"{AMADEUS_HOST}/v1/reference-data/locations",
        params={
            "subType": "CITY,AIRPORT",
            "keyword": code,
            "sort": "analytics.travelers.score",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    r.raise_for_status()
    data = r.json().get("data", []) or []
    if not data:
        return {"name": code, "cityName": None, "analytics": {}}
    x = data[0]
    return {
        "name": x.get("name"),
        "cityName": x.get("address", {}).get("cityName") or x.get("name"),
        "analytics": x.get("analytics", {}),
        "iataCode": x.get("iataCode"),
        "subType": x.get("subType"),
    }


@router.post("")
async def discover(payload: dict):
    origin_city = (payload.get("originCity") or "").strip()
    if not origin_city:
        return {
            "ask": "De quelle ville pars-tu ?",
            "need": ["originCity"],
            "mode": "discover",
        }

    passengers = payload.get("passengers") or {
        "adults": 1,
        "children": 0,
        "infants": 0,
    }
    max_stops = int(payload.get("maxStops", 1))
    budget = payload.get("budgetPerPaxEUR")

    dep = payload.get("departureDate")
    ret = payload.get("returnDate")
    period = payload.get("period")
    if not ((dep and ret) or period):
        return {
            "ask": "Tu préfères des dates exactes (aller/retour) ou un mois + une durée ?",
            "need": ["departureDate/returnDate OR period.start + period.durationDays"],
            "mode": "discover",
        }

    if period and not (dep and ret):
        d0 = dt.date.fromisoformat(period["start"])
        d1 = d0 + dt.timedelta(days=int(period["durationDays"]))
        dep, ret = d0.isoformat(), d1.isoformat()

    month = int(dep.split("-")[1])
    pax_total = max(
        1,
        passengers["adults"] + passengers["children"] + passengers["infants"],
    )

    async with httpx.AsyncClient() as client:
        token = await amadeus_token(client)
        origin_iata = await city_to_iata(client, token, origin_city)

        candidates = await inspiration_candidates(
            client, token, origin_iata, dep, ret, limit=25
        )
        if not candidates:
            raise HTTPException(
                502,
                "L'API Inspiration d'Amadeus n'est pas disponible sur ce compte.",
            )

        price_min = math.inf
        price_max = 0.0
        enriched: list[dict] = []

        for item in candidates:
            dest_code = item["destination"]

            info = await location_info(client, token, dest_code)
            display_name = info.get("cityName") or info.get("name") or dest_code
            pop_score = (
                info.get("analytics", {})
                .get("travelers", {})
                .get("score", 50)
                / 100.0
            )

            geo = await geocode(client, display_name)
            if geo:
                cs = await climate_score(
                    client, geo["latitude"], geo["longitude"], month
                )
            else:
                cs = {"sun_score": 0.5, "temp_c": None, "rain_mm": None}

            params = {
                "originLocationCode": origin_iata,
                "destinationLocationCode": dest_code,
                "departureDate": dep,
                "returnDate": ret,
                "adults": passengers["adults"],
                "children": passengers["children"],
                "infants": passengers["infants"],
                "currencyCode": CURRENCY,
                "nonStop": "false",
                "max": 30,
                "travelClass": "ECONOMY",
            }
            try:
                r = await client.get(
                    f"{AMADEUS_HOST}/v2/shopping/flight-offers",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=20.0,
                )
                r.raise_for_status()
                offers = r.json().get("data", []) or []
            except httpx.HTTPStatusError:
                continue

            filt: list[dict] = []
            for o in offers:
                if count_stops(o) > max_stops:
                    continue
                price = float(o["price"]["grandTotal"])
                per_pax = price / pax_total
                if budget is not None and per_pax > float(budget):
                    continue
                filt.append(o)
            if not filt:
                continue

            best = rank_offers(filt)
            pick = (
                best.get("cheapest")
                or best.get("recommended")
                or best.get("direct")
            )
            if not pick:
                continue
            lite = to_lite(pick, pax_total)

            price_min = min(price_min, lite["price_total_eur"])
            price_max = max(price_max, lite["price_total_eur"])

            enriched.append(
                {
                    "city": display_name,
                    "iataCity": dest_code,
                    "offer": lite,
                    "climate": cs,
                    "popularity": pop_score,
                }
            )

        if not enriched:
            raise HTTPException(404, "Aucune destination trouvée dans les critères.")

        out = []
        for e in enriched:
            p = e["offer"]["price_total_eur"]
            price_norm = normalize(p, price_min, price_max)
            sun = float(e["climate"]["sun_score"])
            pop = float(e["popularity"])
            score = 0.45 * (1 - price_norm) + 0.35 * sun + 0.20 * pop
            e["score"] = round(float(score), 4)
            out.append(e)

        out.sort(key=lambda x: x["score"], reverse=True)
        return {
            "query": {
                "originCity": origin_city,
                "originIATA": origin_iata,
                "departureDate": dep,
                "returnDate": ret,
                "passengers": passengers,
                "budgetPerPaxEUR": budget,
                "maxStops": max_stops,
                "month": month,
            },
            "results": out[:10],
        }
