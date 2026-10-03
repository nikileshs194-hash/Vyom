"""
GigPilot - FastAPI backend.
Wraps the SAME tested agent logic from the Streamlit MVP (data.py, agents.py)
behind a REST API, so a separate HTML/CSS/JS website can call it.
Run with:  uvicorn main:app --reload --port 8000
"""

from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
from datetime import datetime

from data import default_zones, weekly_summary_mock, HOUR_BLOCKS
from agents import master_agent

app = FastAPI(title="GigPilot API")

# Allow the frontend (served from a different port) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- in-memory state (replaces st.session_state) ----------------
STATE = {
    "started": False,
    "zones": default_zones(),
    "history": [],
    "tick": 0,
    "current_zone_name": None,
    "last_event": None,
    "goal": None,
}


class GoalIn(BaseModel):
    target_earnings: float = Field(gt=0)
    available_hours: float = Field(gt=0)
    vehicle: Literal["bike", "scooter", "car"]
    hour_block: Literal["morning", "afternoon", "evening", "night"]
    earned_so_far: float = Field(ge=0)
    hours_elapsed: float = Field(ge=0)

    @model_validator(mode="after")
    def elapsed_within_available(self):
        if self.hours_elapsed > self.available_hours:
            raise ValueError("Hours already worked cannot exceed hours available")
        return self


class ZoneEventIn(BaseModel):
    zone_id: str


@app.post("/api/goal")
def set_goal(goal: GoalIn):
    STATE["started"] = True
    STATE["goal"] = goal.model_dump()
    if STATE["current_zone_name"] is None:
        STATE["current_zone_name"] = STATE["zones"][0]["name"]
    return {"ok": True}


@app.get("/api/zones")
def get_zones():
    return [{"id": z["id"], "name": z["name"]} for z in STATE["zones"]]


@app.get("/api/recommendation")
def get_recommendation():
    if not STATE["started"]:
        return {"started": False}

    goal = STATE["goal"]
    state = {
        "target_earnings": goal["target_earnings"],
        "earned_so_far": goal["earned_so_far"],
        "available_hours": goal["available_hours"],
        "hours_elapsed": goal["hours_elapsed"],
    }

    rec = master_agent(
        state,
        STATE["zones"],
        goal["hour_block"],
        goal["vehicle"],
        STATE["current_zone_name"],
        tick=STATE["tick"],
    )

    remaining_hours = max(goal["available_hours"] - goal["hours_elapsed"], 0)
    progress_pct = min(
        100, int(100 * goal["earned_so_far"] / goal["target_earnings"])
    )

    return {
        "started": True,
        "recommendation": rec,
        "metrics": {
            "earned_so_far": goal["earned_so_far"],
            "target_earnings": goal["target_earnings"],
            "remaining_hours": round(remaining_hours, 1),
            "current_zone_name": STATE["current_zone_name"],
            "progress_pct": progress_pct,
        },
        "last_event": STATE["last_event"],
    }


def find_zone(zone_id):
    zone = next((z for z in STATE["zones"] if z["id"] == zone_id), None)
    if zone is None:
        raise HTTPException(status_code=404, detail=f"Unknown zone '{zone_id}'")
    return zone


def current_recommendation():
    data = get_recommendation()
    if not data["started"]:
        raise HTTPException(status_code=409, detail="Set a goal first")
    return data["recommendation"]


@app.post("/api/accept")
def accept_recommendation():
    rec = current_recommendation()
    STATE["current_zone_name"] = rec["target_zone_name"]
    STATE["history"].append(
        {
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": rec["action"],
            "outcome": "Accepted",
            "confidence": rec["confidence"],
        }
    )
    return {"ok": True, "current_zone_name": STATE["current_zone_name"]}


@app.post("/api/ignore")
def ignore_recommendation():
    rec = current_recommendation()
    STATE["history"].append(
        {
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": rec["action"],
            "outcome": "Ignored",
            "confidence": rec["confidence"],
        }
    )
    return {"ok": True}


@app.post("/api/simulate/traffic")
def simulate_traffic(body: ZoneEventIn):
    z = find_zone(body.zone_id)
    z["traffic_factor"] = 1.8
    STATE["last_event"] = f"Traffic spike simulated in {z['name']}"
    STATE["tick"] += 1
    return {"ok": True}


@app.post("/api/simulate/incentive")
def simulate_incentive(body: ZoneEventIn):
    z = find_zone(body.zone_id)
    z["incentive"] = {
        "description": "Complete 3 more orders for Rs 100 bonus",
        "bonus": 100,
    }
    STATE["last_event"] = f"Incentive activated in {z['name']}"
    STATE["tick"] += 1
    return {"ok": True}


@app.post("/api/simulate/reset")
def simulate_reset():
    STATE["zones"] = default_zones()
    STATE["tick"] += 1
    STATE["last_event"] = "All zone conditions reset to normal"
    return {"ok": True}


@app.get("/api/history")
def get_history():
    return STATE["history"]


@app.get("/api/weekly-summary")
def get_weekly_summary():
    return weekly_summary_mock()


@app.get("/api/hour-blocks")
def get_hour_blocks():
    return HOUR_BLOCKS
