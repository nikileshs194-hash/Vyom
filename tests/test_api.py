"""
GigPilot - API tests.
Run from the GigPilot_Submission folder with:  python -m pytest
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "website" / "backend"))

import main  # noqa: E402
from data import default_zones  # noqa: E402

GOAL = {
    "target_earnings": 1000,
    "available_hours": 5,
    "vehicle": "bike",
    "hour_block": "evening",
    "earned_so_far": 620,
    "hours_elapsed": 2.5,
}


@pytest.fixture
def client():
    main.STATE.update(
        started=False,
        zones=default_zones(),
        history=[],
        tick=0,
        current_zone_name=None,
        last_event=None,
        goal=None,
    )
    return TestClient(main.app)


def recommendation(client):
    return client.get("/api/recommendation").json()


def test_nothing_recommended_before_goal(client):
    assert recommendation(client) == {"started": False}
    assert client.post("/api/accept").status_code == 409
    assert client.post("/api/ignore").status_code == 409


def test_reference_data(client):
    assert client.get("/api/hour-blocks").json() == [
        "morning",
        "afternoon",
        "evening",
        "night",
    ]
    assert [z["id"] for z in client.get("/api/zones").json()] == ["A", "B", "C", "D"]
    assert client.get("/api/weekly-summary").json()["avg_net_rate"] == 278


def test_goal_then_recommendation(client):
    assert client.post("/api/goal", json=GOAL).status_code == 200
    data = recommendation(client)
    assert data["metrics"] == {
        "earned_so_far": 620,
        "target_earnings": 1000,
        "remaining_hours": 2.5,
        "current_zone_name": "Zone A - Koramangala",
        "progress_pct": 62,
    }
    rec = data["recommendation"]
    assert rec["action"] == "Move to Zone C - HSR Layout"
    assert 55 <= rec["confidence"] <= 95
    assert len(rec["ranked_candidates"]) == 4
    assert "Required pace to hit goal: ~Rs 152/hour" in rec["decision_trace"]


def test_accept_moves_worker_and_logs_history(client):
    client.post("/api/goal", json=GOAL)
    assert client.post("/api/accept").json()["current_zone_name"] == "Zone C - HSR Layout"
    data = recommendation(client)
    assert data["metrics"]["current_zone_name"] == "Zone C - HSR Layout"
    assert data["recommendation"]["action"] == "Stay in Zone C - HSR Layout"
    client.post("/api/ignore")
    assert [h["outcome"] for h in client.get("/api/history").json()] == [
        "Accepted",
        "Ignored",
    ]


def test_traffic_spike_triggers_replan(client):
    client.post("/api/goal", json=GOAL)
    client.post("/api/accept")
    client.post("/api/simulate/traffic", json={"zone_id": "C"})
    data = recommendation(client)
    assert data["last_event"] == "Traffic spike simulated in Zone C - HSR Layout"
    assert data["recommendation"]["target_zone_id"] != "C"


def test_incentive_raises_zone_rate_and_reset_clears_it(client):
    client.post("/api/goal", json=GOAL)

    def zone_d():
        ranked = recommendation(client)["recommendation"]["ranked_candidates"]
        return next(c for c in ranked if c["zone_id"] == "D")

    client.post("/api/simulate/incentive", json={"zone_id": "D"})
    assert zone_d()["incentive_note"] == "Complete 3 more orders for Rs 100 bonus"
    client.post("/api/simulate/reset")
    assert zone_d()["incentive_note"] is None
    assert recommendation(client)["last_event"] == "All zone conditions reset to normal"


def test_unknown_zone_is_rejected_without_side_effects(client):
    client.post("/api/goal", json=GOAL)
    before = recommendation(client)
    assert client.post("/api/simulate/traffic", json={"zone_id": "ZZ"}).status_code == 404
    assert client.post("/api/simulate/incentive", json={"zone_id": "ZZ"}).status_code == 404
    assert recommendation(client) == before


@pytest.mark.parametrize(
    "override",
    [
        {"target_earnings": 0},
        {"target_earnings": None},
        {"available_hours": 0},
        {"vehicle": "truck"},
        {"hour_block": "noon"},
        {"earned_so_far": -1},
        {"hours_elapsed": 9},
    ],
)
def test_invalid_goal_is_rejected(client, override):
    assert client.post("/api/goal", json={**GOAL, **override}).status_code == 422
    assert recommendation(client) == {"started": False}


def test_goal_already_reached(client):
    client.post("/api/goal", json={**GOAL, "earned_so_far": 1500})
    data = recommendation(client)
    assert data["metrics"]["progress_pct"] == 100
    assert (
        "Goal already reached - anything earned now is extra"
        in data["recommendation"]["decision_trace"]
    )


def test_shift_over_does_not_invent_a_pace(client):
    client.post("/api/goal", json={**GOAL, "hours_elapsed": 5})
    data = recommendation(client)
    assert data["metrics"]["remaining_hours"] == 0
    trace = data["recommendation"]["decision_trace"]
    assert "No shift time left - Rs 380 of the goal is still open" in trace
    assert data["recommendation"]["earnings_data"]["required_rate_per_hour"] is None


@pytest.mark.parametrize("hour_block", ["morning", "afternoon", "evening", "night"])
@pytest.mark.parametrize("vehicle", ["bike", "scooter", "car"])
def test_every_vehicle_and_hour_block_gives_a_recommendation(client, hour_block, vehicle):
    client.post("/api/goal", json={**GOAL, "hour_block": hour_block, "vehicle": vehicle})
    rec = recommendation(client)["recommendation"]
    assert rec["net_rate_low"] <= rec["net_rate_high"]
    assert rec["action"].endswith(rec["target_zone_name"])
