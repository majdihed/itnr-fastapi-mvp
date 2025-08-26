from app.utils import count_stops, total_duration_minutes, rank_offers

def sample_offer(stops: int, price: float, dur_minutes: int):
    # Build a minimal Amadeus-like offer for testing
    hours, minutes = divmod(dur_minutes, 60)
    dur_iso = f"PT{hours}H{minutes}M"
    segs = [{}] * (stops + 1)
    return {
        "price": {"grandTotal": str(price)},
        "itineraries": [{"duration": dur_iso, "segments": segs}]
    }

def test_count_stops():
    assert count_stops(sample_offer(0, 100, 120)) == 0
    assert count_stops(sample_offer(1, 100, 120)) == 1

def test_total_duration_minutes():
    assert total_duration_minutes(sample_offer(0, 100, 185)) == 185

def test_rank_offers():
    a = sample_offer(1, 100, 600)   # cheap but long
    b = sample_offer(0, 120, 360)   # direct, faster
    c = sample_offer(1, 110, 420)   # middle
    ranked = rank_offers([a,b,c])
    assert ranked["cheapest"] is a
    assert ranked["direct"] is b
    assert ranked["recommended"] in [a,b,c]  # should pick the best score
