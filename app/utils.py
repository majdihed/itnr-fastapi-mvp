from __future__ import annotations

import math
import re
from typing import Any


def _parse_iso_duration(d: str) -> int:
    """PT18H5M -> minutes."""
    if not d or not d.startswith("PT"):
        return 0
    hours = 0
    mins = 0
    m = re.search(r"(\d+)H", d)
    if m:
        hours = int(m.group(1))
    m = re.search(r"(\d+)M", d)
    if m:
        mins = int(m.group(1))
    return hours * 60 + mins


def total_duration_minutes(offer: dict[str, Any]) -> int:
    total = 0
    for itin in offer.get("itineraries", []):
        total += _parse_iso_duration(itin.get("duration", "PT0M"))
    return total


def count_stops(offer: dict[str, Any]) -> int:
    """
    Nombre d'escales max par trajet (itinéraire).
    Direct = 0 (1 segment).
    """
    stops = 0
    for itin in offer.get("itineraries", []):
        segs = itin.get("segments", []) or []
        s = max(0, len(segs) - 1)
        stops = max(stops, s)
    return stops


def _price(offer: dict[str, Any]) -> float:
    try:
        return float(offer.get("price", {}).get("grandTotal", "0"))
    except Exception:
        return 0.0


def rank_offers(offers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not offers:
        return {"cheapest": None, "recommended": None, "direct": None}

    cheapest = min(offers, key=_price)

    direct_candidates = [o for o in offers if count_stops(o) == 0]
    direct = min(direct_candidates, key=_price) if direct_candidates else None

    # score recommandé: mix prix & durée
    prices = [_price(o) for o in offers]
    durs = [total_duration_minutes(o) for o in offers]
    pmin, pmax = min(prices), max(prices)
    dmin, dmax = min(durs), max(durs)

    def norm(x: float, lo: float, hi: float) -> float:
        return 0.5 if hi <= lo else (x - lo) / (hi - lo)

    best = None
    best_score = -1.0
    for o in offers:
        pn = norm(_price(o), pmin, pmax)
        dn = norm(total_duration_minutes(o), dmin, dmax)
        score = 0.6 * (1 - pn) + 0.4 * (1 - dn)
        if score > best_score:
            best = o
            best_score = score

    return {"cheapest": cheapest, "recommended": best, "direct": direct}


def to_hhmm(mins: int) -> str:
    h = mins // 60
    m = mins % 60
    return f"{h}h{m:02d}"


def to_lite(offer: dict[str, Any], pax_total: int) -> dict[str, Any]:
    price = _price(offer)
    dur = total_duration_minutes(offer)
    carriers: list[str] = []
    legs: list[dict[str, Any]] = []

    for itin in offer.get("itineraries", []):
        segs = itin.get("segments", []) or []
        if not segs:
            continue
        first = segs[0]["departure"]["iataCode"]
        last = segs[-1]["arrival"]["iataCode"]
        dep = segs[0]["departure"]["at"]
        arr = segs[-1]["arrival"]["at"]
        for s in segs:
            c = s.get("carrierCode")
            if c and c not in carriers:
                carriers.append(c)
        legs.append(
            {
                "from": first,
                "to": last,
                "dep": dep,
                "arr": arr,
                "stops": max(0, len(segs) - 1),
            }
        )

    return {
        "price_total_eur": round(price, 2),
        "price_per_pax_eur": round(price / max(1, pax_total), 2),
        "duration_total_min": dur,
        "duration_total_hhmm": to_hhmm(dur),
        "stops_max": count_stops(offer),
        "carriers": carriers,
        "legs": legs,
    }
