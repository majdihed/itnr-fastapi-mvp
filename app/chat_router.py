from __future__ import annotations

import datetime as dt
import re
from typing import Any

import dateparser
import httpx
from fastapi import APIRouter, HTTPException

from .core import AMADEUS_HOST, CURRENCY, amadeus_token, city_to_iata
from .discover_router import discover as discover_fn
from .utils import count_stops, rank_offers, to_lite

router = APIRouter(prefix="/chat", tags=["chat"])

MONTHS_FR = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}


def _parse_budget(text: str) -> float | None:
    m = re.search(r"(\d+[.,]?\d*)\s*€", text)
    if m:
        return float(m.group(1).replace(",", "."))
    m = re.search(r"budget\s*(?:max|maximum)?\D*?(\d+[.,]?\d*)", text, re.I)
    return float(m.group(1).replace(",", ".")) if m else None


def _parse_pax(text: str) -> dict[str, int]:
    def _n(pat: str, default: int) -> int:
        m = re.search(pat, text, re.I)
        return int(m.group(1)) if m else default

    adults = max(1, _n(r"(\d+)\s*adulte?", 1))
    children = _n(r"(\d+)\s*enfant", 0)
    infants = _n(r"(\d+)\s*(?:bébé|bebe|infant)s?", 0)
    return {"adults": adults, "children": children, "infants": infants}


def _parse_stops(text: str) -> int:
    if re.search(r"\bdirect\b", text, re.I):
        return 0
    m = re.search(r"(\d+)\s*escales?", text, re.I)
    if m:
        return max(0, min(2, int(m.group(1))))
    return 1


def _parse_flex(text: str) -> int | None:
    m = re.search(r"(?:\+|-|±)?\s*(\d+)\s*jours?\s*(?:de\s*)?flex", text, re.I)
    if m:
        return int(m.group(1))
    if re.search(r"dates?\s*flex", text, re.I):
        return 3
    return None


def _parse_cities(text: str) -> tuple[str | None, str | None]:
    pat1 = (
        r"(?:AR|aller[ -]?retour)\s+([A-ZÉÈÊÎÔÛÂ][\wé\-']+)"
        r"\s+(?:vers|->|→|à|a)?\s*([A-ZÉÈÊÎÔÛÂ][\wé\-']+)"
    )
    m = re.search(pat1, text, re.I)
    if m:
        return m.group(1), m.group(2)

    pat2 = (
        r"\b([A-ZÉÈÊÎÔÛÂ][\wé\-']+)\s+(?:vers|->|→|à|a)\s+"
        r"([A-ZÉÈÊÎÔÛÂ][\wé\-']+)\b"
    )
    m = re.search(pat2, text)
    if m:
        return m.group(1), m.group(2)

    caps = re.findall(r"\b([A-Z][a-zA-Zéèêàîôûäëïöü\-']{2,})\b", text)
    return (caps[0], caps[1]) if len(caps) >= 2 else (None, None)


def _parse_dates(text: str) -> dict[str, Any]:
    m = re.search(r"du\s+(\d{4}-\d{2}-\d{2})\s+au\s+(\d{4}-\d{2}-\d{2})", text)
    if m:
        return {"departureDate": m.group(1), "returnDate": m.group(2)}

    m = re.search(r"du\s+(.+?)\s+au\s+(.+?)(?:[,\.]|$)", text, re.I)
    if m:
        d1 = dateparser.parse(m.group(1), languages=["fr"])
        d2 = dateparser.parse(m.group(2), languages=["fr"])
        if d1 and d2:
            return {
                "departureDate": d1.date().isoformat(),
                "returnDate": d2.date().isoformat(),
            }

    pat3 = r"entre\s+([a-zéèêàîôûç]+)\s+et\s+([a-zéèêàîôûç]+)\s+(\d{4})"
    m = re.search(pat3, text, re.I)
    if m:
        m1, m2, year = m.group(1).lower(), m.group(2).lower(), int(m.group(3))
        if m1 in MONTHS_FR and m2 in MONTHS_FR:
            start = dt.date(year, MONTHS_FR[m1], 1)
            m_dur = re.search(r"(\d+)\s*(?:semaines?|jours?)", text, re.I)
            if m_dur:
                raw = m_dur.group(1)
                if "semaine" in m_dur.group(0).lower():
                    dur = int(raw) * 7
                else:
                    dur = int(raw)
            else:
                dur = 21
            return {"period": {"start": start.isoformat(), "durationDays": dur}}

    iso = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    if len(iso) >= 2:
        return {"departureDate": iso[0], "returnDate": iso[1]}
    return {}


def _complete_dates(parsed: dict) -> None:
    has_exact = parsed.get("departureDate") and parsed.get("returnDate")
    if "period" in parsed and not has_exact:
        d0 = dt.date.fromisoformat(parsed["period"]["start"])
        dur = int(parsed["period"]["durationDays"])
        parsed["departureDate"] = d0.isoformat()
        parsed["returnDate"] = (d0 + dt.timedelta(days=dur)).isoformat()


def _heuristic_parse(text: str) -> dict[str, Any]:
    origin, dest = _parse_cities(text)
    out: dict[str, Any] = {
        "originCity": origin or "",
        "destinationCity": dest or "",
        "passengers": _parse_pax(text),
        "maxStops": _parse_stops(text),
    }
    b = _parse_budget(text)
    if b is not None:
        out["budgetPerPaxEUR"] = b
    f = _parse_flex(text)
    if f is not None:
        out["flexDays"] = f
    out |= _parse_dates(text)
    _complete_dates(out)
    return out


@router.post("")
async def chat_query(payload: dict):
    user_msg = (payload.get("message") or "").strip()
    if not user_msg:
        raise HTTPException(400, "message manquant")

    parsed = _heuristic_parse(user_msg)

    # Mode découverte si pas de destination et intention soleil/budget/mois
    sun_rx = r"\b(?:soleil|plage|ensoleill|chaud)"
    wants_sun = bool(re.search(sun_rx, user_msg, re.I))
    has_dest = bool(parsed.get("destinationCity"))
    if not has_dest and (wants_sun or parsed.get("budgetPerPaxEUR") or parsed.get("period")):
        need: list[str] = []
        if not parsed.get("originCity"):
            need.append("originCity")
        has_dates = parsed.get("departureDate") and parsed.get("returnDate")
        if not has_dates and not parsed.get("period"):
            need.append("dates (aller/retour) ou period.start + durationDays")
        if need:
            return {
                "ask": (
                    "Pour te proposer des destinations soleil, "
                    "j’ai besoin de :"
                ),
                "need": need,
                "mode": "discover",
            }

        body: dict[str, Any] = {
            "originCity": parsed.get("originCity") or "Paris",
            "passengers": parsed.get("passengers")
            or {"adults": 1, "children": 0, "infants": 0},
            "maxStops": parsed.get("maxStops", 1),
            "budgetPerPaxEUR": parsed.get("budgetPerPaxEUR"),
        }
        if parsed.get("period"):
            body["period"] = parsed["period"]
        else:
            body["departureDate"] = parsed["departureDate"]
            body["returnDate"] = parsed["returnDate"]

        # appelle directement /discover sans HTTP
        return {"mode": "discover", **(await discover_fn(body))}

    # Sinon: recherche destination connue
    pax_total = max(
        1,
        parsed.get("passengers", {}).get("adults", 1)
        + parsed.get("passengers", {}).get("children", 0)
        + parsed.get("passengers", {}).get("infants", 0),
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await amadeus_token(client)
        origin = await city_to_iata(
            client,
            token,
            parsed.get("originCity") or "Paris",
        )
        dest = await city_to_iata(
            client,
            token,
            parsed.get("destinationCity") or "Bangkok",
        )
        params = {
            "originLocationCode": origin,
            "destinationLocationCode": dest,
            "departureDate": parsed.get("departureDate"),
            "returnDate": parsed.get("returnDate"),
            "adults": parsed.get("passengers", {}).get("adults", 1),
            "children": parsed.get("passengers", {}).get("children", 0),
            "infants": parsed.get("passengers", {}).get("infants", 0),
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
        except httpx.HTTPStatusError as exc:
            msg = f"Amadeus error: {exc.response.text[:200]}"
            raise HTTPException(exc.response.status_code, msg) from exc

        offers = r.json().get("data", []) or []

    filtered: list[dict[str, Any]] = []
    for o in offers:
        if count_stops(o) > parsed.get("maxStops", 1):
            continue
        price_per_pax = float(o["price"]["grandTotal"]) / pax_total
        b = parsed.get("budgetPerPaxEUR")
        if b is not None and price_per_pax > float(b):
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
    meta = {
        "parsed": parsed,
        "kept": len(filtered),
        "total": len(offers),
        "llm": "heuristic",
    }
    return {"results": results, "meta": meta}
