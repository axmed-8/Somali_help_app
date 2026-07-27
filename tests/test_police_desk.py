"""Police desk: station-scoped queue + accept/dispatch."""
import pytest


def test_police_logic_visibility():
    import police_logic as pl

    em_open = {"assigned_station_id": None, "assigned_to": "police", "type": "security"}
    em_mine = {"assigned_station_id": 5, "assigned_to": "police", "type": "accident"}
    em_other = {"assigned_station_id": 9, "assigned_to": "police", "type": "security"}
    em_fire = {"assigned_station_id": None, "assigned_to": "fire", "type": "fire"}

    assert pl.emergency_visible_to_station(em_open, 5, "police") is True
    assert pl.emergency_visible_to_station(em_mine, 5, "police") is True
    assert pl.emergency_visible_to_station(em_other, 5, "police") is False
    assert pl.emergency_visible_to_station(em_fire, 5, "police") is False


def test_police_claim_and_release():
    import police_logic as pl

    em = {"id": 1, "status": "pending", "assigned_station_id": None, "assigned_to": "police"}
    station = {"id": 5, "name": "Saldhigga Hodan", "latitude": 2.05, "longitude": 45.32, "phone": "061"}
    pl.claim_station(em, station, "police")
    assert em["assigned_station_id"] == 5
    assert em["assigned_team_label"] == "Saldhigga Hodan"
    assert em["responder_latitude"] == 2.05

    with pytest.raises(ValueError):
        pl.claim_station(em, {"id": 9, "name": "Other"}, "police")

    pl.release_station(em, 5)
    assert em["assigned_station_id"] is None
