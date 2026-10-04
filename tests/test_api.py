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

import agent  # noqa: E402
import assistant  # noqa: E402
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
    monkeypatch.setattr(main, "AGENT", agent.Agent())
    main.AGENT.key = None  # tests never call the real Gemini service
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
    for path in ("/api/goal", "/api/accept", "/api/location", "/api/shift/end", "/api/busy-place", "/api/assistant",
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


def test_vague_network_fix_does_not_move_the_rider(asha):
    """A desktop without GPS reports a position good to ~50 km that jumps around."""
    first = asha.post("/api/location", {**GACHIBOWLI, "accuracy": 50000}).json()
    assert first["zone_name"] == "Gachibowli"  # the first fix is used: it is all we have
    asha.post("/api/goal", GOAL)

    far = asha.post("/api/location", {"lat": 17.725, "lon": 78.255, "accuracy": 50000}).json()
    assert (far["zone_name"], far["in_service_area"]) == ("Gachibowli", True)
    assert asha.state()["metrics"]["current_zone_name"] == "Gachibowli"
    assert len(asha.state()["trail"]) == 1

    precise = asha.post("/api/location", KONDAPUR).json()  # a real GPS fix still moves them
    assert precise["zone_name"] == "Kondapur"


def test_order_under_way_is_shown_before_it_pays(on_shift):
    m = on_shift.state()["metrics"]
    assert m["earned_so_far"] == 250 and m["current_pace"] is None
    coming = m["order_under_way"]
    assert coming and coming["net"] > 0 and coming["platform"] in {"Swiggy", "Zomato", "Zepto", "Blinkit"}
    main.WORLD.advance(60)
    assert on_shift.state()["metrics"]["earned_so_far"] >= 250 + coming["net"]


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


def test_changing_the_goal_keeps_the_shift_going(on_shift):
    main.WORLD.advance(120)
    before = on_shift.state()
    assert before["rider"]["orders_done"] >= 2 and before["metrics"]["current_pace"] > 0

    res = on_shift.post("/api/goal", {**GOAL, "target_earnings": 3000, "available_hours": 9,
                                      "vehicle": "scooter"})  # fmt: skip
    assert res.json() == {"ok": True, "updated": True}
    after = on_shift.state()
    m = after["metrics"]
    # every place that shows the goal reflects the change...
    assert (m["target_earnings"], m["available_hours"]) == (3000, 9)
    assert m["remaining_hours"] == before["metrics"]["remaining_hours"] + 3
    assert m["progress_pct"] == int(100 * m["earned_so_far"] / 3000)
    assert m["required_pace"] == pytest.approx((3000 - m["earned_so_far"]) / m["remaining_hours"], abs=1)
    assert "Goal: Rs 3000" in after["recommendation"]["decision_trace"][0]
    # ...and nothing about the shift so far is lost
    assert m["earned_so_far"] == before["metrics"]["earned_so_far"]
    assert m["hours_elapsed"] == before["metrics"]["hours_elapsed"]
    assert m["current_pace"] == before["metrics"]["current_pace"]
    assert after["rider"]["orders_done"] == before["rider"]["orders_done"]
    assert len(main.db.active_shifts()) == 1
    assert main.db.active_shifts()[0]["target_earnings"] == 3000


def test_goal_cannot_be_shorter_than_the_time_already_worked(on_shift):
    main.WORLD.advance(120)  # 1.5 h before the shift + 2 h in it
    res = on_shift.post("/api/goal", {**GOAL, "available_hours": 3})
    assert res.status_code == 422
    assert "3.5 hours already worked" in res.json()["detail"]
    assert on_shift.state()["metrics"]["available_hours"] == 6


def test_hours_in_an_instruction_count_from_now(on_shift):
    """ "Set my goal to 564 in 1 hour", said 3.5 hours into a shift, means one more hour."""
    main.WORLD.advance(120)
    assert on_shift.state()["metrics"]["hours_elapsed"] == 3.5

    reply = ask(on_shift, "set my goal to 564 in 1 hour")
    assert reply["reply"].startswith("Goal set: Rs 564 in 1 hour on a bike.")
    m = on_shift.state()["metrics"]
    assert (m["target_earnings"], m["remaining_hours"]) == (564, 1.0)
    assert m["available_hours"] == 4.5  # 3.5 worked + 1 to go
    assert on_shift.state()["rider"]["orders_done"] >= 2  # the shift was not restarted

    main.WORLD.advance(30)
    assert on_shift.state()["metrics"]["remaining_hours"] == 0.5  # and it counts down

    ask(on_shift, "change my target to 900")  # no hours mentioned: the end time stays put
    m = on_shift.state()["metrics"]
    assert (m["target_earnings"], m["remaining_hours"], m["available_hours"]) == (900, 0.5, 4.5)

    # the goal form sends the same thing when a shift is running
    on_shift.post("/api/goal", {**GOAL, "target_earnings": 700, "hours_left": 3})
    m = on_shift.state()["metrics"]
    assert (m["target_earnings"], m["remaining_hours"], m["available_hours"]) == (700, 3.0, 7.0)
    assert on_shift.post("/api/goal", {**GOAL, "hours_left": 0}).status_code == 422


def test_adding_hours_to_a_finished_shift_resumes_it(asha):
    asha.post("/api/location", GACHIBOWLI)
    asha.post("/api/goal", {**GOAL, "hours_elapsed": 5.5})
    main.WORLD.advance(90)
    assert asha.state()["rider"]["status"] == "shift_over"
    earned = asha.state()["metrics"]["earned_so_far"]
    asha.post("/api/goal", {**GOAL, "available_hours": 9})
    main.WORLD.advance(60)
    data = asha.state()
    assert data["rider"]["status"] != "shift_over"
    assert data["metrics"]["earned_so_far"] > earned
    assert data["metrics"]["remaining_hours"] == pytest.approx(1.0, abs=0.1)


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
    for _ in range(40):  # make sure the restart happens in the middle of an order
        if asha.state()["metrics"]["order_under_way"]:
            break
        main.WORLD.advance(1)
    before = asha.state()["metrics"]
    before_status = asha.state()["rider"]["status_text"]

    order_before = asha.state()["metrics"]["order_under_way"]
    assert order_before, "expected an order to be under way after two hours"

    main.reset_world(now=main.WORLD.now, seed=5, db_path=path)
    after = asha.state()
    assert after["metrics"]["order_under_way"] == order_before  # the same order carries on
    assert after["rider"]["status_text"] == before_status
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


# -------------------------------------------------------------- assistant


def ask(session, text):
    return session.post("/api/assistant", {"text": text}).json()


@pytest.mark.parametrize(
    "text, intent",
    [
        ("set my goal to 1500 in 6 hours", "set_goal"),
        ("I want to earn 1200 in five hours on a scooter", "set_goal"),
        ("start my shift", "set_goal"),
        ("accept", "accept"),
        ("ok go ahead", "accept"),
        ("ignore that", "ignore"),
        ("no", "ignore"),
        ("cancel the move", "cancel_move"),
        ("where should I go?", "recommendation"),
        ("how much have I earned", "earnings"),
        ("how much did I make this week", "week"),
        ("what's my best hour", "best_hour"),
        ("which app pays me most", "apps"),
        ("Charminar is busy", "set_busy_place"),
        ("there is a rush at Inorbit Mall", "set_busy_place"),
        ("clear the busy place", "clear_busy_place"),
        ("show busy places", "show"),
        ("show me the map", "show"),
        ("directions to Kompally", "navigate"),
        ("take me there", "navigate"),
        ("traffic in Medchal", "traffic"),
        ("is it raining", "weather"),
        ("how is Gachibowli", "zone_info"),
        ("where am I", "where_am_i"),
        ("end my shift", "end_shift"),
        ("log out", "logout"),
        ("hello", "greeting"),
        ("sing me a song", "unknown"),
        ("", "help"),
    ],
)
def test_assistant_understands(text, intent):
    assert assistant.parse(text)["intent"] == intent


def test_assistant_pulls_out_the_details():
    goal = assistant.parse("I want to earn 1,200 in five hours on a scooter")
    assert (goal["target_earnings"], goal["available_hours"], goal["vehicle"]) == (1200, 5, "scooter")
    assert assistant.parse("change my target to 2k")["target_earnings"] == 2000
    assert assistant.parse("paradise biriyani is crowded")["place"]["id"] == "paradise"  # misspelt
    assert assistant.parse("directions to Kompally")["zone"]["name"] == "Kompally"
    assert assistant.parse("I always take the long road")["intent"] == "unknown"  # not "Alwal"


def test_assistant_runs_a_whole_shift_by_instruction(asha):
    asha.post("/api/location", GACHIBOWLI)
    assert "Set a goal first" in ask(asha, "accept")["reply"]

    reply = ask(asha, "set my goal to 1500 in 5 hours on a scooter")
    assert reply["reply"].startswith("Goal set: Rs 1,500 in 5 hours on a scooter.")
    assert {"type": "refresh"} in reply["actions"]
    metrics = asha.state()["metrics"]
    assert (metrics["target_earnings"], metrics["available_hours"]) == (1500, 5)

    reply = ask(asha, "Charminar is busy")
    assert "Charminar is now the busy place. Move to Charminar." in reply["reply"]
    assert asha.state()["busy_place"]["name"] == "Charminar"

    reply = ask(asha, "accept")
    assert reply["reply"] == "Accepted: Move to Charminar."
    link = next(a for a in reply["actions"] if a["type"] == "open_url")
    assert link["url"].startswith("https://www.google.com/maps/dir/?api=1&destination=17.3616,78.4747")
    assert "origin=17.4401,78.3489" in link["url"]  # from the rider's real position
    state = asha.state()
    assert state["rider"]["heading_to"] == "CHM"
    assert state["recommendation"]["decision"]["outcome"] == "Accepted"
    assert asha.get("/api/history").json()[0]["outcome"] == "Accepted"

    assert ask(asha, "cancel")["reply"].startswith("Move cancelled")
    assert asha.state()["rider"]["heading_to"] is None
    ask(asha, "clear the busy place")
    assert asha.state()["busy_place"] is None

    ask(asha, "change my target to 2000")
    assert asha.state()["metrics"]["target_earnings"] == 2000
    assert asha.state()["metrics"]["available_hours"] == 5  # unchanged details are kept

    main.WORLD.advance(60)
    assert "of your Rs 2,000 goal" in ask(asha, "how much have I earned")["reply"]
    assert ask(asha, "end my shift")["reply"].startswith("Shift ended.")
    assert asha.state()["started"] is False


def test_assistant_answers_questions(on_shift):
    assert "Gachibowli" in ask(on_shift, "how is Gachibowli")["reply"]
    assert ask(on_shift, "time left")["reply"] == "You have 4.5 hours left in this shift."
    assert "last 7 days" in ask(on_shift, "what did I make this week")["reply"]
    assert "Swiggy" in ask(on_shift, "which app pays me most")["reply"]
    assert ask(on_shift, "what's my best hour")["reply"].startswith("Your best hour")
    assert ask(on_shift, "top zones")["reply"].startswith("Best zones right now:")
    assert "Gachibowli" in ask(on_shift, "where am I")["reply"]
    assert ask(on_shift, "show me the map")["actions"] == [{"type": "scroll", "section": "map"}]
    assert ask(on_shift, "log out")["actions"] == [{"type": "logout"}]


def test_assistant_reports_problems_instead_of_failing(on_shift):
    reply = ask(on_shift, "set my goal to 50000 in 30 hours")
    assert reply["reply"].startswith("I could not set that goal. hours left:")
    assert on_shift.state()["metrics"]["target_earnings"] == 1000  # nothing changed
    assert "did not catch that" in ask(on_shift, "sing me a song")["reply"]
    assert ask(on_shift, "something is busy")["reply"].startswith("Which place is busy?")
    assert on_shift.post("/api/assistant", {"text": "x" * 301}).status_code == 422


# --------------------------------------------------------------- AI agent
# The real model is replaced by a script of canned answers, so these check
# GigPilot's side of the conversation: running tools, feeding results back,
# keeping history, and falling back when Gemini is unavailable.


def call(name, call_id="c1", **arguments):
    return {"type": "function_call", "name": name, "id": call_id, "arguments": arguments}


def model_says(text):
    """A model turn that only speaks - the shape Gemini really returns."""
    return {"status": "completed",
            "steps": [{"type": "thought", "signature": "sig"},
                      {"type": "model_output", "content": [{"type": "text", "text": text}]}]}  # fmt: skip


@pytest.fixture
def scripted(monkeypatch):
    """Give the agent a key and a fake model that replays `turns` in order."""

    def install(*turns):
        queue, seen = list(turns), []

        def fake_call(items):
            seen.append([dict(i) for i in items])
            turn = queue.pop(0)
            if isinstance(turn, Exception):
                raise turn
            return turn

        main.AGENT.key = "test-only-not-a-real-key"
        monkeypatch.setattr(main.AGENT, "call", fake_call)
        return seen

    return install


def test_agent_is_off_without_a_key(asha):
    assert asha.get("/api/assistant/status").json() == {"engine": "rules", "problem": None}
    assert ask(asha, "hello")["engine"] == "rules"


def test_agent_chains_several_tools_for_one_instruction(asha, scripted):
    asha.post("/api/location", GACHIBOWLI)
    seen = scripted(
        {"steps": [{"type": "thought", "signature": "abc"},
                   call("set_goal", "c1", target_earnings=1500, available_hours=6)]},
        {"steps": [call("set_busy_place", "c2", place="Charminar")]},
        {"steps": [call("answer_recommendation", "c3", decision="accept"),
                   call("open_directions", "c4")]},
        model_says("Goal set, Charminar marked busy, and I accepted the move. Maps is open."),
    )  # fmt: skip
    reply = ask(asha, "set goal 1500 for 6 hours, charminar is packed, take me there")

    assert reply["engine"] == "gemini"
    assert reply["reply"].startswith("Goal set, Charminar marked busy")
    state = asha.state()
    assert state["metrics"]["target_earnings"] == 1500
    assert state["busy_place"]["name"] == "Charminar"
    assert state["rider"]["heading_to"] == "CHM"
    assert state["recommendation"]["decision"]["outcome"] == "Accepted"
    kinds = [a["type"] for a in reply["actions"]]
    assert kinds.count("refresh") == 1 and "open_url" in kinds  # duplicates are merged
    # the page gets every step in order, to act it out on screen
    assert [t["tool"] for t in reply["trace"]] == [
        "set_goal", "set_busy_place", "answer_recommendation", "open_directions"
    ]
    assert reply["trace"][0]["args"] == {"target_earnings": 1500, "available_hours": 6}
    assert all(t["ok"] for t in reply["trace"])

    # each tool result went back to the model, linked to its call, before the next turn
    second_turn = seen[1]
    words = "set goal 1500 for 6 hours, charminar is packed, take me there"
    assert second_turn[0]["type"] == "user_input"
    # the rider's words go out with a status snapshot, so the model need not ask for one
    assert second_turn[0]["content"].startswith(words + "\n\n[GigPilot status right now: {")
    assert '"nearest_zone": "Gachibowli"' in second_turn[0]["content"]
    assert second_turn[1] == {"type": "thought", "signature": "abc"}  # model state passed back
    assert second_turn[-1]["type"] == "function_result"
    assert (second_turn[-1]["name"], second_turn[-1]["call_id"]) == ("set_goal", "c1")
    assert "Goal set: Rs 1,500 in 6 hours" in second_turn[-1]["result"][0]["text"]
    assert [i["call_id"] for i in seen[3] if i["type"] == "function_result"] == ["c1", "c2", "c3", "c4"]


def test_agent_remembers_the_conversation(on_shift, scripted):
    seen = scripted(model_says("Which place is busy?"), model_says("Done."))
    ask(on_shift, "somewhere is busy")
    ask(on_shift, "charminar")
    contents = [i.get("content") for i in seen[1] if i["type"] == "user_input"]
    assert contents[0] == "somewhere is busy"  # earlier turns are kept without their snapshot
    assert contents[1].startswith("charminar\n\n[GigPilot status right now:")

    on_shift.post("/api/assistant/reset")
    seen = scripted(model_says("Hello."))
    ask(on_shift, "hi")
    assert len(seen[0]) == 1  # a new chat starts with no history


def test_agent_tools_report_problems_to_the_model(on_shift, scripted):
    seen = scripted(
        {"steps": [call("set_busy_place", "c1", place="Atlantis"),
                   call("zone_info", "c2", zone="Narnia"),
                   call("no_such_tool", "c3")]},
        model_says("I could not find those."),
    )  # fmt: skip
    ask(on_shift, "is atlantis busy")
    results = [i["result"][0]["text"] for i in seen[1] if i["type"] == "function_result"]
    assert "No busy place by that name" in results[0] and "Charminar" in results[0]
    assert "No zone by that name" in results[1]
    assert "Unknown tool" in results[2]
    assert on_shift.state()["busy_place"] is None


def test_rule_based_assistant_also_describes_its_step(on_shift):
    assert ask(on_shift, "accept")["trace"] == [
        {"tool": "answer_recommendation", "args": {"decision": "accept"}, "ok": True}
    ]
    goal = ask(on_shift, "change my target to 2000")["trace"][0]
    assert (goal["tool"], goal["args"]) == ("set_goal", {"target_earnings": 2000})
    busy = ask(on_shift, "charminar is busy")["trace"][0]
    assert (busy["tool"], busy["args"]) == ("set_busy_place", {"place": "Charminar"})
    assert ask(on_shift, "sing me a song")["trace"] == []


def test_agent_status_tool_describes_the_rider(on_shift):
    status, actions = main.execute_tool({"id": 1, "name": "Asha"}, "get_status", {})
    assert actions == []
    assert status["city"] == "Hyderabad"
    assert status["location"]["nearest_zone"] == "Gachibowli"
    assert status["shift"]["target_earnings"] == 1000
    assert status["recommendation"]["confidence_pct"] >= 55
    summary, _ = main.execute_tool({"id": 1, "name": "Asha"}, "earnings_summary", {})
    assert summary["total_orders"] > 1000
    places, _ = main.execute_tool({"id": 1, "name": "Asha"}, "list_busy_places", {"limit": 3})
    assert len(places["places"]) == 3


def test_agent_stops_a_model_that_never_finishes(on_shift, scripted):
    scripted(*[{"steps": [call("get_status", f"c{i}")]} for i in range(agent.MAX_STEPS)])
    reply = ask(on_shift, "loop forever")
    assert reply["engine"] == "gemini"
    assert reply["reply"].startswith("I did part of that but could not finish")


def test_falls_back_to_rules_when_gemini_fails(on_shift, scripted):
    scripted(agent.AgentError("Gemini returned 429 for gemini-3.8-flash: quota exceeded"))
    reply = ask(on_shift, "how much have I earned")
    assert reply["engine"] == "rules"
    assert reply["reply"].startswith("You have earned Rs 250")
    assert "429" in reply["problem"]
    assert "429" in on_shift.get("/api/assistant/status").json()["problem"]


def test_agent_tool_definitions_are_well_formed(asha):
    names = [tool["name"] for tool in agent.TOOLS]
    assert len(names) == len(set(names)) == 17
    for tool in agent.TOOLS:
        assert tool["type"] == "function" and tool["description"]
        assert set(tool["parameters"]["required"]) <= set(tool["parameters"]["properties"])
    for name in names:  # every declared tool is actually implemented
        result, _ = main.execute_tool({"id": 1, "name": "Asha"}, name, {})
        assert "Unknown tool" not in str(result)
    assert agent.Agent._trim([{"type": "function_result"}, {"type": "user_input", "content": "a"}]) == [
        {"type": "user_input", "content": "a"}
    ]
