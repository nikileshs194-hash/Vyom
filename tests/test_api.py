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
from data import HISTORY, IST, ZONES  # noqa: E402

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
WHITEFIELD = {"lat": 12.9698, "lon": 77.7500, "accuracy": 15}
BROOKEFIELD = {"lat": 12.9655, "lon": 77.7185, "accuracy": 15}
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
    asha.post("/api/location", WHITEFIELD)
    assert asha.post("/api/goal", GOAL).status_code == 200
    return asha


# ---------------------------------------------------------------- dataset


def test_dataset_is_large_and_covers_every_zone():
    assert len(ZONES) == 32
    assert HISTORY["total_orders"] > 100_000
    assert set(HISTORY["profile"]) == {z["id"] for z in ZONES}


# --------------------------------------------------------------- accounts


def test_everything_needs_a_login(client):
    for path in ("/api/state", "/api/activity", "/api/history", "/api/me", "/api/route?b=KOR"):
        assert client.get(path).status_code == 401
    for path in ("/api/goal", "/api/accept", "/api/location", "/api/earnings",
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
    on_shift.post("/api/earnings", {"amount": 40, "note": "tip"})

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
    pos = asha.post("/api/location", WHITEFIELD).json()
    assert (pos["zone_name"], pos["in_service_area"], pos["manual"]) == ("Whitefield", True, False)
    asha.post("/api/goal", GOAL)
    assert asha.state()["metrics"]["current_zone_name"] == "Whitefield"

    asha.post("/api/location", BROOKEFIELD)
    data = asha.state()
    assert data["metrics"]["current_zone_name"] == "Brookefield"
    assert data["trail"] == [[12.9698, 77.75], [12.9655, 77.7185]]
    assert any("Brookefield" in e["text"] for e in data["my_events"])


def test_tiny_movements_do_not_flood_the_trail(asha):
    for _ in range(5):
        asha.post("/api/location", WHITEFIELD)
    assert len(asha.state()["trail"]) == 1


def test_position_outside_the_city_is_flagged(asha):
    pos = asha.post("/api/location", MUMBAI).json()
    assert pos["in_service_area"] is False
    assert pos["distance_km"] > 500


def test_manual_zone_fallback(asha):
    pos = asha.post("/api/location/zone", {"zone_id": "IND"}).json()
    assert (pos["zone_name"], pos["manual"]) == ("Indiranagar", True)
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
    assert activity["zones"][0]["zone_name"] == "Whitefield"


def test_incentive_triggers_move_and_arrival_is_by_real_position(on_shift):
    on_shift.post("/api/simulate/incentive", {"zone_id": "BRK"})
    data = on_shift.state()
    assert data["last_event"] == "Incentive activated in Brookefield (demo)"
    assert on_shift.candidate("BRK")["incentive_note"] == "Complete 3 orders for Rs 100 bonus"
    assert data["recommendation"]["action"] == "Move to Brookefield"

    assert on_shift.post("/api/accept").json()["target_zone_name"] == "Brookefield"
    main.WORLD.advance(60)
    data = on_shift.state()
    # accepting does not move anyone: the rider is still where their GPS says
    assert data["metrics"]["current_zone_name"] == "Whitefield"
    assert data["rider"]["status"] == "heading"
    assert data["recommendation"]["action"] == "Continue to Brookefield"
    paused = data["metrics"]["earned_so_far"]
    main.WORLD.advance(30)
    assert on_shift.state()["metrics"]["earned_so_far"] == paused

    on_shift.post("/api/location", BROOKEFIELD)
    main.WORLD.advance(60)
    data = on_shift.state()
    assert data["metrics"]["current_zone_name"] == "Brookefield"
    assert data["rider"]["status"] != "heading"
    assert data["metrics"]["earned_so_far"] > paused
    assert [h["outcome"] for h in on_shift.get("/api/history").json()] == ["Accepted"]


def test_cancel_move_resumes_orders(on_shift):
    on_shift.post("/api/simulate/incentive", {"zone_id": "BRK"})
    on_shift.post("/api/accept")
    main.WORLD.advance(45)
    assert on_shift.state()["rider"]["status"] == "heading"
    on_shift.post("/api/cancel-move")
    assert on_shift.state()["rider"]["heading_to"] is None
    assert on_shift.state()["rider"]["status"] != "heading"


def test_ignore_snoozes_the_suggested_zone(on_shift):
    on_shift.post("/api/simulate/incentive", {"zone_id": "BRK"})
    assert on_shift.state()["recommendation"]["target_zone_id"] == "BRK"
    on_shift.post("/api/ignore")
    assert on_shift.state()["recommendation"]["target_zone_id"] != "BRK"
    assert on_shift.get("/api/history").json()[0]["outcome"] == "Ignored"


def test_traffic_and_rain_events_and_reset(on_shift):
    before = on_shift.candidate("IND")
    on_shift.post("/api/simulate/traffic", {"zone_id": "IND"})
    during = on_shift.candidate("IND")
    assert during["traffic_factor"] > before["traffic_factor"]
    assert during["expected_rate"] < before["expected_rate"]

    dry = on_shift.candidate("JAY")["demand_index"]
    on_shift.post("/api/simulate/rain", {"zone_id": "JAY"})
    assert on_shift.candidate("JAY")["demand_index"] > dry
    assert on_shift.state()["city"]["rain_zones"] == 1

    on_shift.post("/api/simulate/reset")
    assert on_shift.candidate("IND")["traffic_factor"] < during["traffic_factor"]
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


# ------------------------------------------------------ earnings tracking


def test_activity_grids(on_shift):
    main.WORLD.advance(150)
    a = on_shift.get("/api/activity").json()
    assert len(a["days"]) == 17 * 7 + 5  # 18 weeks of boxes, ending on Friday
    assert a["days"][0]["label"].startswith("Mon")
    assert a["days"][-1]["date"] == "2026-10-02"
    assert a["active_days"] == 1

    assert [len(row["hours"]) for row in a["hourly"]] == [24] * 7
    today = a["hourly"][-1]
    assert today["label"] == "Today"
    assert sum(today["hours"]) == a["today_total"] == a["week_total"]
    assert all(amount == 0 for amount in today["hours"][:19])  # nothing before 7 PM
    assert a["best_hour"]["amount"] >= a["slowest_hour"]["amount"] > 0
    assert a["best_hour"]["label"] in ("7 PM", "8 PM", "9 PM")


def test_manual_earning_is_logged(asha):
    assert asha.post("/api/earnings", {"amount": 75, "note": "cash tip"}).status_code == 200
    a = asha.get("/api/activity").json()
    assert a["today_total"] == 75
    assert (a["recent"][0]["kind"], a["recent"][0]["note"]) == ("manual", "cash tip")
    assert asha.post("/api/earnings", {"amount": 0}).status_code == 422
    assert asha.post("/api/earnings", {"amount": -5}).status_code == 422


def test_shift_survives_a_server_restart(client, on_shift, tmp_path):
    """Open shifts and their earnings are reloaded from the database."""
    path = str(tmp_path / "gigpilot.db")
    main.reset_world(now=FRIDAY_7PM, seed=1, db_path=path)
    asha = Session(client, ASHA)
    asha.post("/api/location", BROOKEFIELD)
    asha.post("/api/goal", GOAL)
    main.WORLD.advance(120)
    before = asha.state()["metrics"]

    main.reset_world(now=main.WORLD.now, seed=5, db_path=path)
    after = asha.state()
    assert after["started"] is True
    assert after["metrics"]["earned_so_far"] == before["earned_so_far"]
    assert after["metrics"]["current_zone_name"] == "Brookefield"
    assert after["rider"]["orders_done"] >= 2
    main.db.init(":memory:")  # release the file before pytest removes tmp_path


def test_route_falls_back_to_straight_line_offline(on_shift):
    route = on_shift.get("/api/route?b=IND").json()
    assert route["source"] == "straight line"
    assert route["line"][0] == [12.9698, 77.75]  # starts at the rider's real position


@pytest.mark.parametrize("vehicle", ["bike", "scooter", "car"])
def test_every_vehicle_gives_a_recommendation(asha, vehicle):
    asha.post("/api/goal", {**GOAL, "vehicle": vehicle})
    rec = asha.state()["recommendation"]
    assert rec["net_rate_low"] <= rec["net_rate_high"]
    assert rec["action"].endswith(rec["target_zone_name"])
