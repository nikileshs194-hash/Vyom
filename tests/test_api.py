"""
GigPilot - API tests.
Run from the GigPilot_Submission folder with:  python -m pytest

The backend runs offline here (no weather/road calls, no background clock),
on a fixed date and random seed with an in-memory database, so every run
sees the same city and starts with no accounts.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["GIGPILOT_OFFLINE"] = "1"
os.environ["GIGPILOT_DB"] = ":memory:"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "website" / "backend"))

import main  # noqa: E402
from data import HISTORY, IST, PLACES, ZONE_BY_ID, ZONES  # noqa: E402

FRIDAY_7PM = datetime(2026, 10, 2, 19, 0, tzinfo=IST)
GOAL = {
    "target_earnings": 1000,
    "available_hours": 6,
    "vehicle": "bike",
    "earned_so_far": 250,
    "hours_elapsed": 1.5,
}
# test-only accounts: they exist in the in-memory database for one test
ASHA = {"username": "asha_test", "name": "Asha", "password": "test-only-asha-9f2k"}
RAVI = {"username": "ravi_test", "name": "Ravi", "password": "test-only-ravi-4m7q"}
GACHIBOWLI = {"lat": 17.4401, "lon": 78.3489, "accuracy": 15}
KONDAPUR = {"lat": 17.4622, "lon": 78.3568, "accuracy": 15}
MUMBAI = {"lat": 19.0760, "lon": 72.8777, "accuracy": 30}


class Session:
    """A logged-in client for one user."""

    def __init__(self, client, account):
        self.client = client
        res = client.post("/api/register", json=account)
        assert res.status_code == 200
        self.headers = {"Authorization": "Bearer " + res.json()["token"]}

    def get(self, path):
        return self.client.get(path, headers=self.headers)

    def post(self, path, json=None):
        return self.client.post(path, json=json, headers=self.headers)

    def state(self):
        return self.get("/api/state").json()

    def candidate(self, zone_id):
        ranked = self.state()["recommendation"]["ranked_candidates"]
        return next(c for c in ranked if c["zone_id"] == zone_id)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "real_now", lambda: FRIDAY_7PM)
    main.reset_world(now=FRIDAY_7PM, seed=1, db_path=":memory:")
    return TestClient(main.app)


@pytest.fixture
def asha(client):
    return Session(client, ASHA)


@pytest.fixture
def on_shift(asha):
    asha.post("/api/location", GACHIBOWLI)
    assert asha.post("/api/goal", GOAL).status_code == 200
    return asha


# ---------------------------------------------------------------- dataset


def test_dataset_is_large_and_covers_every_zone():
    assert len(ZONES) == 32
    assert HISTORY["total_orders"] > 100_000
    assert set(HISTORY["profile"]) == {z["id"] for z in ZONES}


# --------------------------------------------------------------- accounts


def test_everything_needs_a_login(client):
    for path in ("/api/state", "/api/activity", "/api/history", "/api/me", "/api/places",
                 "/api/route?b=MDP"):  # fmt: skip
        assert client.get(path).status_code == 401
    for path in ("/api/goal", "/api/accept", "/api/location", "/api/shift/end", "/api/busy-place",
                 "/api/simulate/reset"):  # fmt: skip
        assert client.post(path, json={}).status_code == 401
    bad = {"Authorization": "Bearer not-a-real-token"}
    assert client.get("/api/state", headers=bad).status_code == 401
    assert client.get("/").status_code == 200  # the site itself is public
    assert len(client.get("/api/zones").json()) == 32


def test_register_login_logout(client):
    res = client.post("/api/register", json=ASHA)
    assert res.json()["user"]["username"] == "asha_test"
    assert "password" not in str(res.json())
    assert client.post("/api/register", json=ASHA).status_code == 409

    wrong = {"username": ASHA["username"], "password": "not-the-password"}
    assert client.post("/api/login", json=wrong).status_code == 401
    unknown = {"username": "nobody_here", "password": ASHA["password"]}
    assert client.post("/api/login", json=unknown).status_code == 401

    login = client.post("/api/login", json={"username": "ASHA_TEST", "password": ASHA["password"]})
    headers = {"Authorization": "Bearer " + login.json()["token"]}
    assert client.get("/api/me", headers=headers).json()["name"] == "Asha"
    client.post("/api/logout", headers=headers)
    assert client.get("/api/me", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "override",
    [{"username": "ab"}, {"username": "has space"}, {"password": "short"}, {"name": ""}],
)
def test_bad_registration_is_rejected(client, override):
    assert client.post("/api/register", json={**ASHA, **override}).status_code == 422


def test_passwords_are_not_stored_in_plain_text(asha):
    row = main.db._one("SELECT * FROM users")
    assert ASHA["password"] not in str(row)
    assert len(row["password_hash"]) == 128


def test_users_only_see_their_own_data(client, on_shift):
    ravi = Session(client, RAVI)
    main.WORLD.advance(120)
    on_shift.post("/api/accept")

    mine, theirs = on_shift.state(), ravi.state()
    assert mine["user"]["name"] == "Asha" and theirs["user"]["name"] == "Ravi"
    assert mine["started"] is True and theirs["started"] is False
    assert theirs["position"] is None and theirs["trail"] == []
    assert on_shift.get("/api/activity").json()["week_total"] > 0
    assert ravi.get("/api/activity").json()["week_total"] == 0
    assert len(on_shift.get("/api/history").json()) == 1
    assert ravi.get("/api/history").json() == []
    assert ravi.post("/api/accept").status_code == 409

    ravi.post("/api/goal", {**GOAL, "earned_so_far": 0, "hours_elapsed": 0})
    assert ravi.state()["metrics"]["earned_so_far"] == 0
    assert on_shift.state()["metrics"]["earned_so_far"] > 250


# --------------------------------------------------------------- location


def test_real_position_sets_the_zone_and_is_tracked(asha):
    pos = asha.post("/api/location", GACHIBOWLI).json()
    assert (pos["zone_name"], pos["in_service_area"], pos["manual"]) == ("Gachibowli", True, False)
    asha.post("/api/goal", GOAL)
    assert asha.state()["metrics"]["current_zone_name"] == "Gachibowli"

    asha.post("/api/location", KONDAPUR)
    data = asha.state()
    assert data["metrics"]["current_zone_name"] == "Kondapur"
    assert data["trail"] == [[17.4401, 78.3489], [17.4622, 78.3568]]
    assert any("Kondapur" in e["text"] for e in data["my_events"])


def test_tiny_movements_do_not_flood_the_trail(asha):
    for _ in range(5):
        asha.post("/api/location", GACHIBOWLI)
    assert len(asha.state()["trail"]) == 1


def test_position_outside_the_city_is_flagged(asha):
    pos = asha.post("/api/location", MUMBAI).json()
    assert pos["in_service_area"] is False
    assert pos["distance_km"] > 500


def test_no_orders_while_outside_the_service_area(asha):
    asha.post("/api/location", MUMBAI)
    asha.post("/api/goal", GOAL)
    main.WORLD.advance(120)
    data = asha.state()
    assert data["metrics"]["earned_so_far"] == 250
    assert data["rider"]["orders_done"] == 0
    assert "Outside the Hyderabad service area" in data["rider"]["status_text"]

    asha.post("/api/location", GACHIBOWLI)  # back in a zone: orders resume
    main.WORLD.advance(120)
    assert asha.state()["metrics"]["earned_so_far"] > 250


def test_manual_zone_fallback(asha):
    pos = asha.post("/api/location/zone", {"zone_id": "JBH"}).json()
    assert (pos["zone_name"], pos["manual"]) == ("Jubilee Hills", True)
    assert asha.state()["trail"] == []  # a hand-picked zone is not a tracked position
    assert asha.post("/api/location/zone", {"zone_id": "ZZ"}).status_code == 404


@pytest.mark.parametrize("body", [{"lat": 91, "lon": 77}, {"lat": 12, "lon": 181}, {"lat": 12}])
def test_invalid_position_is_rejected(asha, body):
    assert asha.post("/api/location", body).status_code == 422


# ------------------------------------------------------------------ shift


def test_no_shift_before_a_goal(asha):
    data = asha.state()
    assert data["started"] is False
    assert "recommendation" not in data
    assert len(data["zones"]) == 32
    assert data["city"]["open_orders"] > 0
    assert asha.post("/api/accept").status_code == 409
    assert asha.post("/api/ignore").status_code == 409


def test_goal_then_recommendation(on_shift):
    data = on_shift.state()
    m = data["metrics"]
    assert (m["earned_so_far"], m["remaining_hours"], m["progress_pct"]) == (250, 4.5, 25)
    assert m["required_pace"] == pytest.approx(166.7)
    rec = data["recommendation"]
    assert rec["action"].endswith(rec["target_zone_name"])
    assert 55 <= rec["confidence"] <= 95
    assert len(rec["ranked_candidates"]) == 32
    assert "Required pace to hit goal: ~Rs 167/hour" in rec["decision_trace"]
    assert [len(row["points"]) for row in data["outlook"]] == [12, 12]


def test_earnings_accrue_and_are_stored(on_shift):
    main.WORLD.advance(120)
    data = on_shift.state()
    assert data["metrics"]["earned_so_far"] > 250
    assert data["metrics"]["remaining_hours"] == 2.5
    assert data["rider"]["orders_done"] >= 2
    activity = on_shift.get("/api/activity").json()
    assert activity["today_total"] == data["metrics"]["earned_so_far"] - 250
    assert activity["zones"][0]["zone_name"] == "Gachibowli"


def test_incentive_triggers_move_and_arrival_is_by_real_position(on_shift):
    on_shift.post("/api/simulate/incentive", {"zone_id": "KDP"})
    data = on_shift.state()
    assert data["last_event"] == "Incentive activated in Kondapur (demo)"
    assert on_shift.candidate("KDP")["incentive_note"] == "Complete 3 orders for Rs 100 bonus"
    assert data["recommendation"]["action"] == "Move to Kondapur"

    assert on_shift.post("/api/accept").json()["target_zone_name"] == "Kondapur"
    main.WORLD.advance(60)
    data = on_shift.state()
    # accepting does not move anyone: the rider is still where their GPS says
    assert data["metrics"]["current_zone_name"] == "Gachibowli"
    assert data["rider"]["status"] == "heading"
    assert data["recommendation"]["action"] == "Continue to Kondapur"
    paused = data["metrics"]["earned_so_far"]
    main.WORLD.advance(30)
    assert on_shift.state()["metrics"]["earned_so_far"] == paused

    on_shift.post("/api/location", KONDAPUR)
    main.WORLD.advance(60)
    data = on_shift.state()
    assert data["metrics"]["current_zone_name"] == "Kondapur"
    assert data["rider"]["status"] != "heading"
    assert data["metrics"]["earned_so_far"] > paused
    assert [h["outcome"] for h in on_shift.get("/api/history").json()] == ["Accepted"]


def test_cancel_move_resumes_orders(on_shift):
    on_shift.post("/api/simulate/incentive", {"zone_id": "KDP"})
    on_shift.post("/api/accept")
    main.WORLD.advance(45)
    assert on_shift.state()["rider"]["status"] == "heading"
    on_shift.post("/api/cancel-move")
    assert on_shift.state()["rider"]["heading_to"] is None
    assert on_shift.state()["rider"]["status"] != "heading"


def test_ignore_snoozes_the_suggested_zone(on_shift):
    on_shift.post("/api/simulate/incentive", {"zone_id": "KDP"})
    assert on_shift.state()["recommendation"]["target_zone_id"] == "KDP"
    on_shift.post("/api/ignore")
    assert on_shift.state()["recommendation"]["target_zone_id"] != "KDP"
    assert on_shift.get("/api/history").json()[0]["outcome"] == "Ignored"


def test_traffic_and_rain_events_and_reset(on_shift):
    before = on_shift.candidate("JBH")
    on_shift.post("/api/simulate/traffic", {"zone_id": "JBH"})
    during = on_shift.candidate("JBH")
    assert during["traffic_factor"] > before["traffic_factor"]
    assert during["expected_rate"] < before["expected_rate"]

    dry = on_shift.candidate("DSN")["demand_index"]
    on_shift.post("/api/simulate/rain", {"zone_id": "DSN"})
    assert on_shift.candidate("DSN")["demand_index"] > dry
    assert on_shift.state()["city"]["rain_zones"] == 1

    on_shift.post("/api/simulate/reset")
    assert on_shift.candidate("JBH")["traffic_factor"] < during["traffic_factor"]
    assert on_shift.state()["city"]["rain_zones"] == 0


def test_unknown_zone_is_rejected(on_shift):
    for event in ("traffic", "incentive", "rain"):
        assert on_shift.post(f"/api/simulate/{event}", {"zone_id": "ZZ"}).status_code == 404
    assert on_shift.get("/api/route?b=ZZ").status_code == 404


@pytest.mark.parametrize(
    "override",
    [
        {"target_earnings": 0},
        {"target_earnings": None},
        {"available_hours": 0},
        {"vehicle": "truck"},
        {"earned_so_far": -1},
        {"hours_elapsed": 9},
    ],
)
def test_invalid_goal_is_rejected(asha, override):
    assert asha.post("/api/goal", {**GOAL, **override}).status_code == 422
    assert asha.state()["started"] is False


def test_goal_already_reached(asha):
    asha.post("/api/goal", {**GOAL, "earned_so_far": 1500})
    data = asha.state()
    assert data["metrics"]["progress_pct"] == 100
    assert (
        "Goal already reached - anything earned now is extra"
        in data["recommendation"]["decision_trace"]
    )


def test_shift_ends_when_hours_run_out(asha):
    asha.post("/api/goal", {**GOAL, "hours_elapsed": 5.5})
    main.WORLD.advance(90)
    data = asha.state()
    assert data["rider"]["status"] == "shift_over"
    assert data["metrics"]["required_pace"] is None
    earned = data["metrics"]["earned_so_far"]
    main.WORLD.advance(60)
    assert asha.state()["metrics"]["earned_so_far"] == earned


def test_end_shift_keeps_the_history(on_shift):
    main.WORLD.advance(90)
    total = on_shift.get("/api/activity").json()["today_total"]
    assert total > 0
    on_shift.post("/api/shift/end")
    assert on_shift.state()["started"] is False
    assert on_shift.get("/api/activity").json()["today_total"] == total


# -------------------------------------------------- answering a suggestion


def test_accept_is_not_asked_again_until_the_suggestion_changes(on_shift):
    first = on_shift.state()["recommendation"]
    assert first["decision"] is None
    on_shift.post("/api/accept")
    rec = on_shift.state()["recommendation"]
    assert rec["target_zone_id"] == first["target_zone_id"]
    assert rec["decision"] == {"outcome": "Accepted", "time": "19:00"}

    on_shift.post("/api/busy-place", {"place_id": "charminar"})  # a new suggestion
    rec = on_shift.state()["recommendation"]
    assert rec["target_zone_name"] == "Charminar"
    assert rec["decision"] is None


def test_ignore_is_not_asked_again_until_the_suggestion_changes(on_shift):
    on_shift.post("/api/busy-place", {"place_id": "charminar"})
    on_shift.post("/api/ignore")
    rec = on_shift.state()["recommendation"]
    assert rec["target_zone_name"] != "Charminar"
    assert rec["decision"] == {"outcome": "Ignored", "time": "19:00"}

    on_shift.post("/api/busy-place", {"place_id": "airport"})
    rec = on_shift.state()["recommendation"]
    assert rec["target_zone_name"] == "Shamshabad"
    assert rec["decision"] is None


# ------------------------------------------------------------ busy places


def test_busy_places_dataset(on_shift):
    places = on_shift.get("/api/places").json()
    assert len(places) == len(PLACES) >= 40
    assert all(0 <= p["busy_now"] <= 5 for p in places)
    assert all(p["zone_name"] for p in places)
    assert not any(p["active"] for p in places)
    assert {p["zone_id"] for p in PLACES} <= set(ZONE_BY_ID)


@pytest.mark.parametrize("place", PLACES, ids=lambda p: p["id"])
def test_setting_a_busy_place_makes_it_the_suggestion(on_shift, place):
    on_shift.post("/api/busy-place", {"place_id": place["id"]})
    data = on_shift.state()
    rec = data["recommendation"]
    assert data["busy_place"]["name"] == place["name"]
    assert rec["target_zone_id"] == place["zone_id"]
    assert rec["busy_place"] == place["name"]
    assert rec["destination"] == {"name": place["name"], "lat": place["lat"], "lon": place["lon"]}
    assert place["name"] in rec["reason"]


def test_changing_and_clearing_the_busy_place(on_shift):
    usual = on_shift.state()["recommendation"]["target_zone_id"]
    on_shift.post("/api/busy-place", {"place_id": "charminar"})
    assert on_shift.state()["recommendation"]["target_zone_name"] == "Charminar"

    on_shift.post("/api/busy-place", {"place_id": "paradise"})  # only one at a time
    data = on_shift.state()
    assert data["recommendation"]["target_zone_name"] == "Secunderabad"
    active = [p["name"] for p in on_shift.get("/api/places").json() if p["active"]]
    assert active == ["Paradise Biryani, Secunderabad"]

    on_shift.post("/api/busy-place", {"place_id": None})
    data = on_shift.state()
    assert data["busy_place"] is None
    assert data["recommendation"]["target_zone_id"] == usual
    assert data["recommendation"]["destination"]["name"] == ZONE_BY_ID[usual]["name"]
    assert on_shift.post("/api/busy-place", {"place_id": "nowhere"}).status_code == 404


def test_busy_place_wears_off(on_shift):
    on_shift.post("/api/busy-place", {"place_id": "charminar"})
    main.WORLD.advance(241)
    assert on_shift.state()["busy_place"] is None


# ------------------------------------------------------ earnings tracking


def test_activity_grids(on_shift):
    main.WORLD.advance(150)
    a = on_shift.get("/api/activity").json()
    assert len(a["days"]) == 17 * 7 + 5  # 18 weeks of boxes, ending on Friday
    assert a["days"][0]["label"].startswith("Mon")
    assert a["days"][-1]["date"] == "2026-10-02"
    assert a["days"][-1]["amount"] == a["today_total"] > 0

    assert [len(row["hours"]) for row in a["hourly"]] == [24] * 7
    for row in a["hourly"]:  # an hour box has orders behind it exactly when it has money
        assert [n > 0 for n in row["entries"]] == [amount > 0 for amount in row["hours"]]
    today = a["hourly"][-1]
    assert today["label"] == "Today"
    # each hour box is rounded on its own, so the row can be a rupee or two off
    assert sum(today["hours"]) == pytest.approx(a["today_total"], abs=2)
    assert all(amount == 0 for amount in today["hours"][:19])  # nothing before 7 PM today
    assert a["week_total"] > a["today_total"]
    assert a["best_hour"]["amount"] >= a["slowest_hour"]["amount"] > 0


# ------------------------------------------------------------ partner apps


def test_order_history_is_synced_from_partner_apps(asha):
    assert asha.get("/api/activity").json()["total_orders"] == 0  # nothing known yet
    asha.post("/api/location", GACHIBOWLI)
    a = asha.get("/api/activity").json()
    assert a["synced"] is True
    assert a["total_orders"] > 1000
    assert a["active_days"] > 80
    assert a["today_total"] == 0  # history stops yesterday; today comes from the live feed
    assert {p["name"] for p in a["platforms"]} == {"Swiggy", "Zomato", "Zepto", "Blinkit"}
    assert sum(p["share_pct"] for p in a["platforms"]) == pytest.approx(100, abs=2)
    assert a["zones"][0]["zone_name"] == "Gachibowli"  # mostly the home zone
    newest = a["recent"][0]
    assert newest["platform"] and newest["merchant"] and newest["distance_km"] > 0
    assert [e["date"] + e["time"] for e in a["recent"]] == sorted(
        (e["date"] + e["time"] for e in a["recent"]), reverse=True
    )


def test_history_is_synced_only_once(asha):
    asha.post("/api/location", GACHIBOWLI)
    first = asha.get("/api/activity").json()["total_orders"]
    asha.post("/api/location", KONDAPUR)
    asha.post("/api/location/zone", {"zone_id": "JBH"})
    assert asha.get("/api/activity").json()["total_orders"] == first


def test_no_history_is_invented_outside_the_service_area(asha):
    asha.post("/api/location", MUMBAI)
    a = asha.get("/api/activity").json()
    assert (a["synced"], a["total_orders"]) == (False, 0)


def test_each_rider_has_their_own_synced_history(client, asha):
    ravi = Session(client, RAVI)
    asha.post("/api/location", GACHIBOWLI)
    ravi.post("/api/location", {"lat": 17.3616, "lon": 78.4747})
    mine, theirs = asha.get("/api/activity").json(), ravi.get("/api/activity").json()
    assert mine["zones"][0]["zone_name"] == "Gachibowli"
    assert theirs["zones"][0]["zone_name"] == "Charminar"
    assert mine["week_total"] != theirs["week_total"]


def test_live_orders_come_from_a_partner_app(on_shift):
    main.WORLD.advance(120)
    a = on_shift.get("/api/activity").json()
    today = [e for e in a["recent"] if e["date"] == "2026-10-02"]
    assert today and all(e["platform"] in {"Swiggy", "Zomato", "Zepto", "Blinkit"} for e in today)
    events = [e["text"] for e in on_shift.state()["my_events"] if e["kind"] == "order"]
    assert events and all(e.split()[0] in {"Swiggy", "Zomato", "Zepto", "Blinkit"} for e in events)


def test_there_is_no_manual_entry(client, asha):
    assert asha.post("/api/earnings", {"amount": 75}).status_code in (404, 405)
    page = client.get("/")
    assert "Log an earning" not in page.text
    assert page.headers["cache-control"] == "no-cache"  # browsers must not show a stale page


def test_older_database_is_upgraded(tmp_path):
    """A database made before partner sync gains the new columns on open."""
    import sqlite3

    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, "
        "name TEXT NOT NULL, password_hash TEXT NOT NULL, salt TEXT NOT NULL, "
        "created_at TEXT NOT NULL);"
        "CREATE TABLE earnings (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
        "shift_id INTEGER, ts TEXT NOT NULL, amount REAL NOT NULL, zone_id TEXT, "
        "kind TEXT NOT NULL, note TEXT);"
        "INSERT INTO users VALUES (1, 'old_user', 'Old', 'x', '00', '2026-10-01T10:00:00');"
        "INSERT INTO earnings VALUES (1, 1, NULL, '2026-10-01T10:00:00', 55, 'YEL', 'order', "
        "'1.9 km order');"
    )
    old.commit()
    old.close()
    main.db.init(path)
    row = main.db.recent_earnings(1)[0]
    assert (row["amount"], row["platform"]) == (55, None)
    assert main.db.is_synced(1) is False
    main.db.init(":memory:")  # release the file before pytest removes tmp_path


def test_shift_survives_a_server_restart(client, on_shift, tmp_path):
    """Open shifts and their earnings are reloaded from the database."""
    path = str(tmp_path / "gigpilot.db")
    main.reset_world(now=FRIDAY_7PM, seed=1, db_path=path)
    asha = Session(client, ASHA)
    asha.post("/api/location", KONDAPUR)
    asha.post("/api/goal", GOAL)
    main.WORLD.advance(120)
    before = asha.state()["metrics"]

    main.reset_world(now=main.WORLD.now, seed=5, db_path=path)
    after = asha.state()
    assert after["started"] is True
    assert after["metrics"]["earned_so_far"] == before["earned_so_far"]
    assert after["metrics"]["current_zone_name"] == "Kondapur"
    assert after["rider"]["orders_done"] >= 2
    main.db.init(":memory:")  # release the file before pytest removes tmp_path


def test_route_falls_back_to_straight_line_offline(on_shift):
    route = on_shift.get("/api/route?b=JBH").json()
    assert route["source"] == "straight line"
    assert route["line"][0] == [17.4401, 78.3489]  # starts at the rider's real position


@pytest.mark.parametrize("vehicle", ["bike", "scooter", "car"])
def test_every_vehicle_gives_a_recommendation(asha, vehicle):
    asha.post("/api/goal", {**GOAL, "vehicle": vehicle})
    rec = asha.state()["recommendation"]
    assert rec["net_rate_low"] <= rec["net_rate_high"]
    assert rec["action"].endswith(rec["target_zone_name"])
