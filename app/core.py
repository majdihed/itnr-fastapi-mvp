from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv()

AMADEUS_HOST = os.getenv("AMADEUS_HOST", "https://test.api.amadeus.com")
CLIENT_ID = os.getenv("AMADEUS_CLIENT_ID")
CLIENT_SECRET = os.getenv("AMADEUS_CLIENT_SECRET")
CURRENCY = os.getenv("DEFAULT_CURRENCY", "EUR")


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
        timeout=20.0,
    )
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        msg = f"Amadeus auth failed: {e.response.text}"
        raise HTTPException(e.response.status_code, msg) from e
    token = r.json().get("access_token")
    if not token:
        raise HTTPException(502, "Amadeus: pas de token")
    return token


async def city_to_iata(client: httpx.AsyncClient, token: str, name: str) -> str:
    """
    Résout 'Paris' -> 'PAR' (CITY) sinon renvoie l'IATA le plus pertinent.
    """
    r = await client.get(
        f"{AMADEUS_HOST}/v1/reference-data/locations",
        params={
            "subType": "CITY,AIRPORT",
            "keyword": name,
            "sort": "analytics.travelers.score",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=12.0,
    )
    r.raise_for_status()
    data = r.json().get("data", []) or []
    if not data:
        raise HTTPException(400, f"Ville introuvable: {name}")
    for x in data:
        if x.get("subType") == "CITY":
            return x["iataCode"]
    return data[0]["iataCode"]
