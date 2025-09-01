from app.utils import count_stops, to_hhmm, total_duration_minutes


def test_to_hhmm():
    assert to_hhmm(0) == "0h00"
    assert to_hhmm(75) == "1h15"


def test_count_stops_direct():
    offer = {"itineraries": [{"segments": [{}]}]}
    assert count_stops(offer) == 0


def test_count_stops_one_stop():
    offer = {"itineraries": [{"segments": [{}, {}]}]}
    assert count_stops(offer) == 1


def test_total_duration_minutes():
    offer = {"itineraries": [{"duration": "PT2H30M"}]}
    assert total_duration_minutes(offer) == 150
