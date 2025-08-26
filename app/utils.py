import re

def total_duration_minutes(offer: dict) -> int:
    def iso_to_min(s: str) -> int:
        h = m = 0
        mH = re.search(r'(\d+)H', s or '')
        mM = re.search(r'(\d+)M', s or '')
        if mH: h = int(mH.group(1))
        if mM: m = int(mM.group(1))
        return h*60 + m
    return sum(iso_to_min(itin.get('duration')) for itin in offer.get('itineraries', []))

def count_stops(offer: dict) -> int:
    max_stops = 0
    for itin in offer.get('itineraries', []):
        segs = itin.get('segments', [])
        max_stops = max(max_stops, max(0, len(segs)-1))
    return max_stops

def rank_offers(offers: list[dict]) -> dict:
    if not offers:
        return {}
    cheapest = min(offers, key=lambda o: float(o['price']['grandTotal']))
    directs = [o for o in offers if count_stops(o) == 0]
    direct = None
    if directs:
        direct = sorted(directs, key=lambda o: (total_duration_minutes(o), float(o['price']['grandTotal'])))[0]
    prices = [float(o['price']['grandTotal']) for o in offers]
    durs = [total_duration_minutes(o) for o in offers]
    pmin,pmax = min(prices), max(prices)
    dmin,dmax = min(durs), max(durs)
    def norm(x, lo, hi): return 0 if hi==lo else (x-lo)/(hi-lo)
    def score(o):
        return 0.6*norm(float(o['price']['grandTotal']), pmin,pmax) + 0.4*norm(total_duration_minutes(o), dmin,dmax)
    recommended = min(offers, key=score)
    return {'cheapest': cheapest, 'recommended': recommended, 'direct': direct}

def hhmm(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}"

def carriers(offer: dict) -> list[str]:
    return sorted({seg["carrierCode"]
                   for itin in offer.get("itineraries", [])
                   for seg in itin.get("segments", [])})

def leg_summary(offer: dict) -> list[dict]:
    legs = []
    for itin in offer.get("itineraries", []):
        segs = itin.get("segments", [])
        if not segs:
            continue
        first, last = segs[0], segs[-1]
        legs.append({
            "from": first["departure"]["iataCode"],
            "to": last["arrival"]["iataCode"],
            "dep": first["departure"]["at"],
            "arr": last["arrival"]["at"],
            "stops": max(0, len(segs) - 1),
            "duration": itin.get("duration")
        })
    return legs

def to_lite(offer: dict, pax_total: int) -> dict:
    total = float(offer["price"]["grandTotal"])
    dur_min = total_duration_minutes(offer)
    return {
        "price_total_eur": total,
        "price_per_pax_eur": round(total / max(1, pax_total), 2),
        "stops_max": count_stops(offer),
        "duration_total_min": dur_min,
        "duration_total_hhmm": hhmm(dur_min),
        "carriers": carriers(offer),
        "legs": leg_summary(offer),
        "raw_id": offer.get("id"),
    }

