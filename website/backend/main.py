"""
GigPilot - FastAPI backend.
Keeps the live city state (world.py) in step with the real clock, feeds it
real weather and road data (live.py), stores every user's own data in
SQLite (db.py) and exposes the six agents (agents.py) as a REST API for
the HTML/CSS/JS website. Every endpoint except register/login needs a
login token, and only ever returns that user's data.
Run with:  uvicorn main:app --port 8000
"""

import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, model_validator

import assistant
import db
import partners
from agents import demand_level, master_agent
from data import (
    CITY,
    HISTORY,
    IST,
    PLACE_BY_ID,
    PLACES,
    ZONE_BY_ID,
    ZONES,
    place_busy_now,
    weekly_summary,
)
from live import OFFLINE, WEATHER_CODES, Places, Roads, Traffic, Weather, haversine_km
from world import World

SNOOZE_MINUTES = 20  # how long an ignored zone stays out of the recommendations
WEATHER_REFRESH_SECONDS = 600
SERVICE_RADIUS_KM = 8  # further than this from every zone = outside the service area
TRAIL_MIN_METRES = 30  # store a new location point only after moving this far...
TRAIL_MAX_SECONDS = 120  # ...or after this long
ACTIVITY_WEEKS = 18
DEFAULT_ZONE = "MDP"

WEATHER, ROADS, TRAFFIC, GEOCODER = Weather(), Roads(), Traffic(), Places()
LOCK = threading.RLock()
STOP = threading.Event()

WORLD = None
POSITIONS = {}  # user_id -> last reported position (kept even with no shift running)


def real_now():
    return datetime.now(IST)


def record_earning(rider, amount, zone_id, kind, **details):
    db.add_earning(rider["user_id"], rider["shift_id"], WORLD.now, amount, zone_id, kind,
                   **details)  # fmt: skip


def activity_start(now):
    """Monday of the first week shown in the activity grid."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=now.weekday() + 7 * (ACTIVITY_WEEKS - 1))


def sync_partner_history(user_id, zone_id):
    """First time we know where a rider works: pull in their order history
    from the partner apps (generated - see partners.py). Happens once."""
    if db.is_synced(user_id):
        return
    now = WORLD.now
    orders = partners.past_orders(user_id, zone_id, activity_start(now).date(), now)
    db.add_synced_orders(user_id, orders)


def reset_world(now=None, seed=None, db_path=None):
    """(Re)build the city and reload every shift still open in the database."""
    global WORLD
    with LOCK:
        db.init(db_path)
        POSITIONS.clear()
        WORLD = World(WEATHER, ROADS, TRAFFIC, now=now, seed=seed, on_earning=record_earning)
        for shift in db.active_shifts():
            shift["started_at"] = datetime.fromisoformat(shift["started_at"]).replace(tzinfo=IST)
            earned, orders = db.shift_totals(shift["id"])
            last = db.last_location(shift["user_id"])
            known = last and last["zone_id"] in ZONE_BY_ID
            WORLD.add_rider(
                shift["user_id"],
                shift,
                last["zone_id"] if known else DEFAULT_ZONE,
                earned=shift["base_earned"] + earned,
                orders_done=orders,
            )


reset_world()


def ticker():
    """Background clock: keeps the city on real time and refreshes live feeds."""
    ROADS.refresh()
    WEATHER.refresh()
    TRAFFIC.refresh()
    last_feeds = time.time()
    while not STOP.wait(2.0):
        with LOCK:
            WORLD.advance_to(real_now())
        if time.time() - last_feeds > WEATHER_REFRESH_SECONDS:
            last_feeds = time.time()
            WEATHER.refresh()
            TRAFFIC.refresh()


@asynccontextmanager
async def lifespan(app):
    if not OFFLINE:
        threading.Thread(target=ticker, daemon=True).start()
    yield
    STOP.set()


app = FastAPI(title="GigPilot API", lifespan=lifespan)

# Allow the frontend (if served from a different port) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def always_serve_the_latest_page(request, call_next):
    """Make browsers re-check the site's files on every load, so an old copy
    of the page is never shown after an update."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------- request bodies ----------------


class RegisterIn(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,30}$")
    name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    username: str = Field(max_length=30)
    password: str = Field(max_length=128)


class GoalIn(BaseModel):
    target_earnings: float = Field(gt=0)
    available_hours: float = Field(gt=0, le=24)
    vehicle: Literal["bike", "scooter", "car"]
    earned_so_far: float = Field(ge=0)
    hours_elapsed: float = Field(ge=0)

    @model_validator(mode="after")
    def elapsed_within_available(self):
        if self.hours_elapsed > self.available_hours:
            raise ValueError("Hours already worked cannot exceed hours available")
        return self


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy: Optional[float] = Field(default=None, ge=0)


class ZoneEventIn(BaseModel):
    zone_id: str


class BusyPlaceIn(BaseModel):
    place_id: Optional[str] = None  # None clears the busy place


# ---------------- helpers ----------------


def current_user(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    user = db.user_for_token(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Please log in")
    return user


def bearer_token(authorization: str = Header(default="")):
    return authorization.removeprefix("Bearer ").strip()


def find_zone(zone_id):
    if zone_id not in ZONE_BY_ID:
        raise HTTPException(status_code=404, detail=f"Unknown zone '{zone_id}'")
    return ZONE_BY_ID[zone_id]


def rider_for(user):
    rider = WORLD.riders.get(user["id"])
    if not rider:
        raise HTTPException(status_code=409, detail="Set a goal first")
    return rider


def nearest_zone(lat, lon):
    here = {"lat": lat, "lon": lon}
    zone = min(ZONES, key=lambda z: haversine_km(here, z))
    return zone, haversine_km(here, zone)


def rider_location(rider):
    """The zone the rider is in, or has already agreed to ride to."""
    return rider["heading_to"] or rider["zone_id"]


def build_recommendation(rider):
    now = WORLD.now
    rider["snoozed"] = {z: t for z, t in rider["snoozed"].items() if t > now}
    state = {
        "target_earnings": rider["target_earnings"],
        "earned_so_far": max(rider["earned"], 0),
        "available_hours": rider["available_hours"],
        "hours_elapsed": WORLD.hours_elapsed(rider),
    }
    location = rider_location(rider)
    rec = master_agent(
        state,
        WORLD.zone_views(rider["user_id"]),
        rider["vehicle"],
        location,
        WORLD.travel,
        snoozed=set(rider["snoozed"]),
    )
    if rec["target_zone_id"] == location != rider["zone_id"]:
        # already agreed to go to this zone but not there yet
        rec["action"] = f"Continue to {rec['target_zone_name']}"

    # where exactly to go: the busy place itself if that is the draw, else the zone centre
    surge = WORLD.busy_place
    spot = surge["place"] if surge and rec["busy_place"] else ZONE_BY_ID[rec["target_zone_id"]]
    rec["destination"] = {"name": spot["name"], "lat": spot["lat"], "lon": spot["lon"]}

    # an answer stands until GigPilot suggests a different zone
    decision = rider["decision"]
    if decision and decision["target_zone_id"] != rec["target_zone_id"]:
        decision = rider["decision"] = None
    rec["decision"] = decision and {"outcome": decision["outcome"], "time": decision["time"]}
    return rec


def decide(user, rider, rec, outcome):
    """Record the rider's answer; it stays in force while the suggestion is unchanged."""
    db.add_decision(user["id"], WORLD.now, rec["action"], outcome, rec["confidence"])
    rider["last_event"] = f"{outcome}: {rec['action']}"
    rider["decision"] = None
    after = build_recommendation(rider)
    rider["decision"] = {
        "outcome": outcome,
        "time": WORLD.now.strftime("%H:%M"),
        "target_zone_id": after["target_zone_id"],
    }


def rider_view(rider):
    zone = ZONE_BY_ID[rider["zone_id"]]
    view = {
        "zone_id": rider["zone_id"],
        "status": rider["status"],
        "heading_to": rider["heading_to"],
        "orders_done": rider["orders_done"],
    }
    if rider["status"] == "on_order":
        o = rider["order"]
        view["status_text"] = (
            f"Delivering an order (Rs {o['payout']}, {o['distance_km']} km) - "
            f"done {rider['busy_until'].strftime('%H:%M')}"
        )
        if rider["heading_to"]:
            view["status_text"] += (
                f", then heading to {ZONE_BY_ID[rider['heading_to']]['name']}"
            )
    elif rider["status"] == "heading":
        view["status_text"] = (
            f"On the way to {ZONE_BY_ID[rider['heading_to']]['name']} - "
            f"orders resume when you arrive"
        )
    elif rider["status"] == "shift_over":
        view["status_text"] = "Shift over"
    elif not rider["in_area"]:
        view["status_text"] = (
            f"Outside the {CITY} service area - no orders until you are back in a zone"
        )
    else:
        view["status_text"] = f"Waiting for an order in {zone['name']}"
    return view


def metrics_view(rider, rec):
    elapsed = WORLD.hours_elapsed(rider)
    remaining = max(rider["available_hours"] - elapsed, 0)
    worked = elapsed - rider["base_hours"]
    earned = rider["earned"]
    return {
        "earned_so_far": round(earned),
        "target_earnings": rider["target_earnings"],
        "available_hours": rider["available_hours"],
        "hours_elapsed": round(elapsed, 2),
        "remaining_hours": round(remaining, 1),
        "current_zone_name": ZONE_BY_ID[rider["zone_id"]]["name"],
        "progress_pct": min(100, max(0, int(100 * earned / rider["target_earnings"]))),
        "current_pace": (
            round((earned - rider["base_earned"]) / worked) if worked >= 0.1 else None
        ),
        "required_pace": rec["earnings_data"]["required_rate_per_hour"],
        "projected_earnings": round(earned + rec["expected_rate"] * remaining),
    }


def position_view(user_id):
    pos = POSITIONS.get(user_id)
    if not pos:
        return None
    return {
        "lat": pos["lat"],
        "lon": pos["lon"],
        "accuracy": pos["accuracy"],
        "manual": pos["manual"],
        "place": pos["place"],
        "zone_id": pos["zone_id"],
        "zone_name": ZONE_BY_ID[pos["zone_id"]]["name"],
        "distance_km": round(pos["distance_km"], 1),
        "in_service_area": pos["distance_km"] <= SERVICE_RADIUS_KM,
        "updated": pos["at"].strftime("%H:%M:%S"),
    }


def update_position(user, lat, lon, accuracy, manual):
    zone, distance = nearest_zone(lat, lon)
    now = real_now()
    previous = POSITIONS.get(user["id"])
    saved = previous["saved"] if previous else None
    if not manual:
        moved = saved is None or haversine_km({"lat": lat, "lon": lon}, saved) * 1000
        stale = saved is None or (now - saved["at"]).total_seconds() >= TRAIL_MAX_SECONDS
        if saved is None or moved >= TRAIL_MIN_METRES or stale:
            db.add_location(user["id"], now, lat, lon, zone["id"])
            saved = {"lat": lat, "lon": lon, "at": now}
    POSITIONS[user["id"]] = {
        "lat": lat, "lon": lon, "accuracy": accuracy, "manual": manual,
        "zone_id": zone["id"], "distance_km": distance, "at": now, "saved": saved,
        "place": zone["name"] if manual else (previous or {}).get("place"),
    }  # fmt: skip
    rider = WORLD.riders.get(user["id"])
    if rider:
        rider["in_area"] = distance <= SERVICE_RADIUS_KM
        WORLD.set_zone(rider, zone["id"])
    if distance <= SERVICE_RADIUS_KM:
        sync_partner_history(user["id"], zone["id"])


# ---------------- accounts ----------------


@app.post("/api/register")
def register(body: RegisterIn):
    user = db.create_user(body.username, body.name.strip() or body.username, body.password,
                          real_now())  # fmt: skip
    if not user:
        raise HTTPException(status_code=409, detail="That username is already taken")
    return {"token": db.create_session(user["id"], real_now()), "user": user}


@app.post("/api/login")
def login(body: LoginIn):
    user = db.verify_user(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Wrong username or password")
    return {"token": db.create_session(user["id"], real_now()), "user": user}


@app.post("/api/logout")
def logout(token: str = Depends(bearer_token)):
    if token:
        db.delete_session(token)
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return user


# ---------------- reference data ----------------


@app.get("/api/zones")
def get_zones():
    return [{k: z[k] for k in ("id", "name", "lat", "lon", "type")} for z in ZONES]


@app.get("/api/route")
def get_route(b: str, user=Depends(current_user)):
    """Road route from the user's position to zone b, for drawing on the map."""
    find_zone(b)
    pos = POSITIONS.get(user["id"])
    if pos:
        return ROADS.route_from(pos["lat"], pos["lon"], b)
    rider = WORLD.riders.get(user["id"])
    return ROADS.route(rider["zone_id"] if rider else DEFAULT_ZONE, b)


# ---------------- location ----------------


@app.post("/api/location")
def set_location(body: LocationIn, user=Depends(current_user)):
    with LOCK:
        update_position(user, body.lat, body.lon, body.accuracy, manual=False)
    place = GEOCODER.name(body.lat, body.lon)  # network call: keep it outside the lock
    with LOCK:
        if place and user["id"] in POSITIONS:
            POSITIONS[user["id"]]["place"] = place
        return position_view(user["id"])


@app.post("/api/location/zone")
def set_location_zone(body: ZoneEventIn, user=Depends(current_user)):
    """Fallback when the browser cannot share a GPS position."""
    zone = find_zone(body.zone_id)
    with LOCK:
        update_position(user, zone["lat"], zone["lon"], None, manual=True)
        return position_view(user["id"])


# ---------------- the rider's shift ----------------


@app.post("/api/goal")
def set_goal(goal: GoalIn, user=Depends(current_user)):
    with LOCK:
        shift = goal.model_dump()
        shift["id"] = db.start_shift(user["id"], shift, WORLD.now)
        shift.update(
            started_at=WORLD.now,
            base_earned=goal.earned_so_far,
            base_hours=goal.hours_elapsed,
        )
        pos = POSITIONS.get(user["id"])
        rider = WORLD.add_rider(user["id"], shift, pos["zone_id"] if pos else DEFAULT_ZONE)
        if pos and pos["distance_km"] > SERVICE_RADIUS_KM:
            rider["in_area"] = False
            if rider["status"] == "on_order":  # taken before the flag was set: hand it back
                rider["status"], rider["order"] = "idle", None
    return {"ok": True}


@app.post("/api/shift/end")
def end_shift(user=Depends(current_user)):
    with LOCK:
        db.end_shift(user["id"], WORLD.now)
        WORLD.remove_rider(user["id"])
    return {"ok": True}


@app.get("/api/state")
def get_state(user=Depends(current_user)):
    with LOCK:
        rider = WORLD.riders.get(user["id"])
        views = WORLD.zone_views(user["id"])
        now = WORLD.now
        codes = [WEATHER.at(z["id"], now)["code"] for z in ZONES]
        raining = sum(1 for v in views if v["rain"] >= 0.5)
        data = {
            "user": user,
            "city_name": CITY,
            "started": rider is not None,
            "clock": {"time": now.strftime("%H:%M"), "date": now.strftime("%a %d %b")},
            "position": position_view(user["id"]),
            "sources": {
                "weather": WEATHER.status(),
                "roads": ROADS.status(),
                "traffic": TRAFFIC.status(),
                "orders": {
                    "mode": "simulated",
                    "source": "stand-in for partner data",
                    "detail": f"{HISTORY['total_orders']:,} generated orders, "
                    f"{HISTORY['days']} days, {len(ZONES)} zones",
                },
            },
            "city": {
                "temp": round(sum(v["temp"] for v in views) / len(views), 1),
                "condition": "Rain" if raining else WEATHER_CODES.get(
                    max(set(codes), key=codes.count), "Clear"
                ),
                "rain_zones": raining,
                "open_orders": sum(len(v["open_orders"]) for v in views),
                "active_incentives": sum(1 for v in views if v["incentive"]),
                "avg_traffic": round(sum(v["traffic"] for v in views) / len(views), 2),
            },
            "zones": [
                {
                    "id": v["id"],
                    "name": v["name"],
                    "lat": v["lat"],
                    "lon": v["lon"],
                    "demand": v["demand"],
                    "level": demand_level(v["demand"]),
                    "traffic": v["traffic"],
                    "rain": v["rain"],
                    "temp": v["temp"],
                    "open_orders": len(v["open_orders"]),
                    "incentive": v["incentive"] and v["incentive"]["description"],
                }
                for v in views
            ],
            "busy_place": WORLD.busy_place and {
                **{k: WORLD.busy_place["place"][k] for k in ("id", "name", "lat", "lon")},
                "zone_name": ZONE_BY_ID[WORLD.busy_place["place"]["zone_id"]]["name"],
                "until": WORLD.busy_place["until"].strftime("%H:%M"),
            },
            "city_events": list(WORLD.events)[:12],
            "trail": db.trail(user["id"], now.replace(hour=0, minute=0, second=0)),
        }
        if rider:
            rec = build_recommendation(rider)
            location = rider_location(rider)
            compare = rec["target_zone_id"]
            if compare == location:  # staying: show the strongest alternative instead
                compare = next(
                    c["zone_id"] for c in rec["ranked_candidates"] if c["zone_id"] != location
                )
            data.update(
                recommendation=rec,
                metrics=metrics_view(rider, rec),
                rider=rider_view(rider),
                my_events=list(rider["events"]),
                last_event=rider["last_event"],
                outlook=[
                    {
                        "zone_name": ZONE_BY_ID[zid]["name"],
                        "points": WORLD.demand_forecast(zid),
                    }
                    for zid in (location, compare)
                ],
            )
        return data


@app.post("/api/accept")
def accept_recommendation(user=Depends(current_user)):
    with LOCK:
        rider = rider_for(user)
        rec = build_recommendation(rider)
        WORLD.head_to(rider, rec["target_zone_id"])
        decide(user, rider, rec, "Accepted")
        zone = ZONE_BY_ID[rec["target_zone_id"]]
        return {"ok": True, "target_zone_name": zone["name"], "lat": zone["lat"],
                "lon": zone["lon"]}  # fmt: skip


@app.post("/api/ignore")
def ignore_recommendation(user=Depends(current_user)):
    with LOCK:
        rider = rider_for(user)
        rec = build_recommendation(rider)
        if rec["target_zone_id"] != rider_location(rider):
            rider["snoozed"][rec["target_zone_id"]] = WORLD.now + timedelta(
                minutes=SNOOZE_MINUTES
            )
        decide(user, rider, rec, "Ignored")
        return {"ok": True}


@app.post("/api/cancel-move")
def cancel_move(user=Depends(current_user)):
    with LOCK:
        rider = rider_for(user)
        WORLD.head_to(rider, rider["zone_id"])
        rider["decision"] = None
        rider["last_event"] = "Move cancelled - taking orders here again"
        return {"ok": True}


@app.get("/api/history")
def get_history(user=Depends(current_user)):
    return [
        {**d, "time": d["ts"][11:16], "date": d["ts"][:10]} for d in db.decisions(user["id"])
    ]


# ---------------- earnings tracking ----------------


@app.get("/api/activity")
def get_activity(user=Depends(current_user)):
    """Earnings by day (activity grid) and by hour of day (when you earn)."""
    now = WORLD.now
    today = now.date()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    grid_start = activity_start(now)
    week_start = midnight - timedelta(days=6)
    with LOCK:
        rider = WORLD.riders.get(user["id"])
        if rider and rider["in_area"]:
            sync_partner_history(user["id"], rider["zone_id"])

    by_day = {r["day"]: r for r in db.earnings_by_day(user["id"], grid_start)}
    days = []
    for i in range((today - grid_start.date()).days + 1):
        day = (grid_start + timedelta(days=i)).date()
        row = by_day.get(day.isoformat())
        days.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%a %d %b"),
                "amount": round(row["amount"]) if row else 0,
                "entries": row["entries"] if row else 0,
            }
        )

    by_hour = db.earnings_by_day_hour(user["id"], week_start)
    cells = {(r["day"], r["hour"]): r["amount"] for r in by_hour}
    counts = {(r["day"], r["hour"]): r["entries"] for r in by_hour}
    hourly = []
    hour_totals = [0.0] * 24  # summed before rounding, so totals match the daily grid
    for i in range(7):
        day = (week_start + timedelta(days=i)).date()
        for h in range(24):
            hour_totals[h] += cells.get((day.isoformat(), h), 0)
        hourly.append(
            {
                "date": day.isoformat(),
                "label": "Today" if day == today else day.strftime("%a %d"),
                "hours": [round(cells.get((day.isoformat(), h), 0)) for h in range(24)],
                "entries": [counts.get((day.isoformat(), h), 0) for h in range(24)],
            }
        )
    worked = [h for h in range(24) if hour_totals[h] > 0]

    def hour_label(h):
        return datetime(2000, 1, 1, h).strftime("%I %p").lstrip("0")

    def hour_stat(h):
        return {"hour": h, "label": hour_label(h), "amount": round(hour_totals[h])}

    week_total = round(sum(hour_totals))
    zones = [
        {
            "zone_name": ZONE_BY_ID[z["zone_id"]]["name"] if z["zone_id"] in ZONE_BY_ID else "-",
            "amount": round(z["amount"]),
            "entries": z["entries"],
            "share_pct": round(100 * z["amount"] / week_total) if week_total else 0,
        }
        for z in db.earnings_by_zone(user["id"], week_start)
    ]
    market = weekly_summary()
    return {
        "days": days,
        "hourly": hourly,
        "today_total": days[-1]["amount"],
        "week_total": week_total,
        "active_days": sum(1 for d in days if d["amount"] > 0),
        "best_hour": hour_stat(max(worked, key=lambda h: hour_totals[h])) if worked else None,
        "slowest_hour": (
            hour_stat(min(worked, key=lambda h: hour_totals[h])) if len(worked) > 1 else None
        ),
        "zones": zones,
        "synced": db.is_synced(user["id"]),
        "total_orders": db.order_count(user["id"]),
        "platforms": [
            {
                "name": p["platform"],
                "amount": round(p["amount"]),
                "orders": p["orders"],
                "share_pct": round(100 * p["amount"] / week_total) if week_total else 0,
            }
            for p in db.earnings_by_platform(user["id"], week_start)
        ],
        "recent": [
            {
                "time": e["ts"][11:16],
                "date": e["ts"][:10],
                "amount": round(e["amount"]),
                "kind": e["kind"],
                "zone_name": ZONE_BY_ID[e["zone_id"]]["name"] if e["zone_id"] in ZONE_BY_ID else None,
                "platform": e["platform"],
                "merchant": e["merchant"],
                "distance_km": e["distance_km"],
                "note": e["note"],
            }
            for e in db.recent_earnings(user["id"], 20)
        ],
        "market": {"best_window": market["best_window"], "best_zone": market["best_zone"]},
    }


# ---------------- busy places ----------------


@app.get("/api/places")
def get_places(user=Depends(current_user)):
    """The busy-places dataset, with how busy each one is at this hour."""
    with LOCK:
        now, surge = WORLD.now, WORLD.busy_place
        active = surge["place"]["id"] if surge else None
    rows = [
        {
            "id": p["id"],
            "name": p["name"],
            "category": p["category"],
            "zone_name": ZONE_BY_ID[p["zone_id"]]["name"],
            "lat": p["lat"],
            "lon": p["lon"],
            "busy": p["busy"],
            "busy_now": 5.0 if p["id"] == active else place_busy_now(p, now),
            "active": p["id"] == active,
        }
        for p in PLACES
    ]
    return sorted(rows, key=lambda r: (not r["active"], -r["busy_now"], r["name"]))


@app.post("/api/busy-place")
def set_busy_place(body: BusyPlaceIn, user=Depends(current_user)):
    """Set (or change, or clear) the place that is busy right now."""
    if body.place_id is not None and body.place_id not in PLACE_BY_ID:
        raise HTTPException(status_code=404, detail=f"Unknown place '{body.place_id}'")
    with LOCK:
        place = PLACE_BY_ID[body.place_id] if body.place_id else None
        WORLD.set_busy_place(place)
        rider = WORLD.riders.get(user["id"])
        if rider:
            rider["snoozed"] = {}
            rider["last_event"] = (
                f"Busy place set: {place['name']}" if place else "Busy place cleared"
            )
    return {"ok": True}


# ---------------- demo tools (simulated events) ----------------


def demo_event(user, zone_id, action, text):
    z = find_zone(zone_id)
    with LOCK:
        action(z["id"])
        rider = WORLD.riders.get(user["id"])
        if rider:
            rider["last_event"] = f"{text} in {z['name']} (demo)"
    return {"ok": True}


@app.post("/api/simulate/traffic")
def simulate_traffic(body: ZoneEventIn, user=Depends(current_user)):
    return demo_event(user, body.zone_id, WORLD.spike_traffic, "Traffic spike")


@app.post("/api/simulate/incentive")
def simulate_incentive(body: ZoneEventIn, user=Depends(current_user)):
    return demo_event(user, body.zone_id, WORLD.start_incentive, "Incentive activated")


@app.post("/api/simulate/rain")
def simulate_rain(body: ZoneEventIn, user=Depends(current_user)):
    return demo_event(user, body.zone_id, WORLD.start_rain, "Heavy rain")


@app.post("/api/simulate/reset")
def simulate_reset(user=Depends(current_user)):
    with LOCK:
        WORLD.reset_conditions()
        rider = WORLD.riders.get(user["id"])
        if rider:
            rider["snoozed"] = {}
            rider["last_event"] = "All demo zone events cleared"
    return {"ok": True}


# ---------------- assistant (text and voice commands) ----------------


class AssistantIn(BaseModel):
    text: str = Field(max_length=300)


def money(amount):
    return f"Rs {amount:,.0f}"


def directions_url(user_id, lat, lon):
    """Google Maps directions from the rider's real position to a point."""
    rider = WORLD.riders.get(user_id)
    mode = "driving" if rider and rider["vehicle"] == "car" else "two-wheeler"
    url = f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}&travelmode={mode}"
    pos = POSITIONS.get(user_id)
    if pos and not pos["manual"]:
        url += f"&origin={pos['lat']},{pos['lon']}"
    return url


def suggestion_text(rec):
    return f"{rec['action']}. Expected {money(rec['net_rate_low'])} to {money(rec['net_rate_high'])} an hour."


def run_intent(user, intent):
    """Carry out one assistant intent. Returns (reply, actions for the page)."""
    kind = intent["intent"]
    uid = user["id"]
    rider = WORLD.riders.get(uid)
    refresh = [{"type": "refresh"}]
    needs_shift = "Set a goal first - for example, say: set my goal to 1000 in 6 hours."

    if kind == "help":
        return assistant.HELP, []
    if kind == "greeting":
        return f"Hi {user['name']}. Tell me what to do, or say help to hear what I can do.", []
    if kind == "unknown":
        return "Sorry, I did not catch that. " + assistant.HELP, []
    if kind == "logout":
        return "Logging you out.", [{"type": "logout"}]
    if kind == "show":
        return f"Showing the {intent['section']} section.", [
            {"type": "scroll", "section": intent["section"]}
        ]

    if kind == "set_goal":
        if rider:
            goal = {
                "target_earnings": rider["target_earnings"],
                "available_hours": rider["available_hours"],
                "vehicle": rider["vehicle"],
                "earned_so_far": round(max(rider["earned"], 0)),
                "hours_elapsed": round(WORLD.hours_elapsed(rider), 2),
            }
        else:
            goal = {"target_earnings": 1000, "available_hours": 6, "vehicle": "bike",
                    "earned_so_far": 0, "hours_elapsed": 0}  # fmt: skip
        goal.update({k: intent[k] for k in ("target_earnings", "available_hours", "vehicle")
                     if k in intent})  # fmt: skip
        try:
            set_goal(GoalIn(**goal), user)
        except ValidationError as exc:
            error = exc.errors()[0]
            field = str(error["loc"][-1]).replace("_", " ") + ": " if error["loc"] else ""
            reason = error["msg"].replace("Value error, ", "")
            return f"I could not set that goal. {field}{reason}.", []
        rec = build_recommendation(WORLD.riders[uid])
        return (
            f"Goal set: {money(goal['target_earnings'])} in {goal['available_hours']:g} hours "
            f"on a {goal['vehicle']}. My suggestion: {suggestion_text(rec)}",
            refresh,
        )

    if kind == "set_busy_place":
        place = intent.get("place")
        if not place and intent.get("zone"):
            in_zone = [p for p in PLACES if p["zone_id"] == intent["zone"]["id"]]
            place = max(in_zone, key=lambda p: p["busy"]) if in_zone else None
        if not place:
            return "Which place is busy? Say, for example: Charminar is busy.", [
                {"type": "scroll", "section": "busy"}
            ]
        set_busy_place(BusyPlaceIn(place_id=place["id"]), user)
        reply = f"{place['name']} is now the busy place."
        if WORLD.riders.get(uid):
            reply += " " + suggestion_text(build_recommendation(WORLD.riders[uid]))
        return reply, refresh
    if kind == "clear_busy_place":
        set_busy_place(BusyPlaceIn(place_id=None), user)
        return "Busy place cleared.", refresh
    if kind in ("traffic", "rain", "incentive"):
        zone = intent["zone"]
        body = ZoneEventIn(zone_id=zone["id"])
        {"traffic": simulate_traffic, "rain": simulate_rain, "incentive": simulate_incentive}[
            kind
        ](body, user)
        label = {"traffic": "Traffic spike", "rain": "Heavy rain", "incentive": "Incentive"}[kind]
        return f"{label} started in {zone['name']} (demo event).", refresh
    if kind == "reset_events":
        simulate_reset(user)
        return "All demo events cleared.", refresh

    if kind == "set_location":
        zone = intent["zone"]
        set_location_zone(ZoneEventIn(zone_id=zone["id"]), user)
        return f"Your location is set to {zone['name']} by hand.", refresh
    if kind == "where_am_i":
        pos = position_view(uid)
        if not pos:
            return "I do not have your location yet. Allow location in the browser.", []
        if pos["manual"]:
            return f"Your location is set by hand to {pos['zone_name']}.", []
        return (
            f"You are at {pos['place'] or 'an unnamed spot'}, {pos['distance_km']} km from the "
            f"{pos['zone_name']} zone.",
            [],
        )

    if kind == "navigate":
        target = intent.get("place") or intent.get("zone")
        if not target:
            if not rider:
                return "Tell me where to - for example: directions to Kompally.", []
            target = build_recommendation(rider)["destination"]
        url = directions_url(uid, target["lat"], target["lon"])
        return f"Opening Google Maps directions to {target['name']}.", [
            {"type": "open_url", "url": url, "label": f"Directions to {target['name']}"}
        ]

    if kind == "weather":
        views = WORLD.zone_views(uid)
        temp = round(sum(v["temp"] for v in views) / len(views), 1)
        raining = [v["name"] for v in views if v["rain"] >= 0.5]
        if raining:
            return f"It is {temp} degrees, and raining in {len(raining)} zones including {raining[0]}.", []
        return f"It is {temp} degrees and dry across the city.", []
    if kind == "place_info":
        place = intent["place"]
        level = place_busy_now(place, WORLD.now)
        return (
            f"{place['name']} is in the {ZONE_BY_ID[place['zone_id']]['name']} zone. "
            f"Busy level right now: {level} out of 5.",
            [],
        )
    if kind in ("best_hour", "slowest_hour", "week", "apps"):
        activity = get_activity(user)
        if kind == "week":
            return f"You earned {money(activity['week_total'])} in the last 7 days.", []
        if kind == "apps":
            if not activity["platforms"]:
                return "No orders from the delivery apps in the last 7 days yet.", []
            parts = [f"{p['name']} {money(p['amount'])}" for p in activity["platforms"]]
            return "Last 7 days by app: " + ", ".join(parts) + ".", []
        hour = activity[kind]
        if not hour:
            return "There is not enough earnings history to tell yet.", []
        word = "best" if kind == "best_hour" else "slowest"
        return f"Your {word} hour over the last 7 days is {hour['label']}, with {money(hour['amount'])}.", []

    if kind == "zone_info" and not rider:
        zone = intent["zone"]
        view = next(v for v in WORLD.zone_views(uid) if v["id"] == zone["id"])
        return (
            f"{zone['name']}: {demand_level(view['demand'])} demand, "
            f"{len(view['open_orders'])} open orders.",
            [],
        )
    if kind == "earnings" and not rider:
        return f"You have earned {money(get_activity(user)['today_total'])} today. No shift is running.", []
    if not rider:
        return needs_shift, [{"type": "scroll", "section": "goal"}]

    # ---- everything below needs a running shift
    rec = build_recommendation(rider)
    metrics = metrics_view(rider, rec)
    if kind == "recommendation":
        return f"{suggestion_text(rec)} {rec['reason']}", [
            {"type": "scroll", "section": "recommendation"}
        ]
    if kind == "accept":
        accept_recommendation(user)
        actions = list(refresh)
        if rec["target_zone_id"] != rider["zone_id"]:
            dest = rec["destination"]
            actions.append({"type": "open_url", "url": directions_url(uid, dest["lat"], dest["lon"]),
                            "label": f"Directions to {dest['name']}"})  # fmt: skip
        return f"Accepted: {rec['action']}.", actions
    if kind == "ignore":
        ignore_recommendation(user)
        after = build_recommendation(rider)
        return f"Ignored. My suggestion is now: {suggestion_text(after)}", refresh
    if kind == "cancel_move":
        if not rider["heading_to"]:
            return "There is no move to cancel.", []
        cancel_move(user)
        return "Move cancelled. You are taking orders here again.", refresh
    if kind == "end_shift":
        end_shift(user)
        return f"Shift ended. You earned {money(metrics['earned_so_far'])}.", refresh
    if kind == "earnings":
        reply = (
            f"You have earned {money(metrics['earned_so_far'])} of your "
            f"{money(metrics['target_earnings'])} goal, {metrics['progress_pct']} percent."
        )
        if metrics["current_pace"] is not None:
            reply += f" Your pace is {money(metrics['current_pace'])} an hour."
        return reply + f" Projected by shift end: {money(metrics['projected_earnings'])}.", []
    if kind == "time_left":
        return f"You have {metrics['remaining_hours']} hours left in this shift.", []
    if kind == "status":
        return f"{rider_view(rider)['status_text']}. {suggestion_text(rec)}", []
    if kind == "top_zones":
        top = sorted(rec["ranked_candidates"], key=lambda c: -c["expected_rate"])[:3]
        parts = [f"{c['zone_name']} at {money(c['expected_rate'])} an hour" for c in top]
        return "Best zones right now: " + ", ".join(parts) + ".", [
            {"type": "scroll", "section": "zones"}
        ]
    if kind == "zone_info":
        c = next(c for c in rec["ranked_candidates"] if c["zone_id"] == intent["zone"]["id"])
        return (
            f"{c['zone_name']}: {c['demand_level']} demand, about {money(c['expected_rate'])} an "
            f"hour, {c['travel_penalty_min']} minutes from you, {c['open_orders']} open orders.",
            [],
        )
    return "Sorry, I did not catch that. " + assistant.HELP, []


@app.post("/api/assistant")
def assistant_command(body: AssistantIn, user=Depends(current_user)):
    """One typed or spoken instruction: understand it, do it, say what happened."""
    intent = assistant.parse(body.text)
    with LOCK:
        try:
            reply, actions = run_intent(user, intent)
        except HTTPException as exc:
            reply, actions = str(exc.detail), []
    return {"heard": body.text, "intent": intent["intent"], "reply": reply, "actions": actions}


# Serve the website itself too, so one server on port 8000 is enough.
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
