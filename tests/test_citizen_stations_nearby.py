from tests.live_app import reload_live_app


def test_citizen_nearby_stations_api():
    ers = reload_live_app()
    c = ers.app.test_client()
    users = (ers.load_users() or {}).get("users") or []
    citizen = next(u for u in users if u.get("role") == "citizen" and u.get("status") == "active")
    with c.session_transaction() as s:
        s["user_id"] = citizen["id"]
        s["role"] = "citizen"
        s["logged_in"] = True
        s["name"] = citizen.get("name") or "Citizen"

    for kind in ("police", "fire"):
        r = c.get(f"/api/stations?kind={kind}&lat=2.05&lng=45.32")
        assert r.status_code == 200, r.get_json()
        body = r.get_json() or {}
        assert "stations" in body
        assert body.get("kind") == kind

    html = c.get("/").get_data(as_text=True)
    assert 'data-map-cat="police"' in html
    assert 'data-map-cat="fire"' in html
    assert 'data-map-cat="hospital"' in html
