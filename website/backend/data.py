"""
GigPilot - city model and synthetic market data.

Zone locations are real Hyderabad localities. Everything that a delivery
platform would normally own (orders, payouts, demand history) is generated
here, because no platform exposes it for free. It is generated from one
consistent demand model, so history, live orders and forecasts agree.
"""

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from places import CATEGORY_PROFILE, PLACES

IST = timezone(timedelta(hours=5, minutes=30))

VEHICLE_COST_PER_KM = {
    "bike": 1.2,
    "scooter": 1.8,
    "car": 3.5,
}

HISTORY_DAYS = 60
BASE_ORDERS_PER_HOUR = 12  # market-wide orders per zone-hour at demand index 1.0

# (id, name, lat, lon, type)
CITY = "Hyderabad"

_ZONE_ROWS = [
    ("MDP", "Madhapur", 17.4483, 78.3915, "tech"),
    ("HTC", "HITEC City", 17.4435, 78.3772, "tech"),
    ("GCB", "Gachibowli", 17.4401, 78.3489, "tech"),
    ("KDP", "Kondapur", 17.4622, 78.3568, "tech"),
    ("FND", "Financial District", 17.4144, 78.3426, "tech"),
    ("KKP", "Kukatpally", 17.4849, 78.4138, "residential"),
    ("MYP", "Miyapur", 17.4968, 78.3614, "residential"),
    ("JBH", "Jubilee Hills", 17.4326, 78.4071, "nightlife"),
    ("BJH", "Banjara Hills", 17.4156, 78.4347, "nightlife"),
    ("AMP", "Ameerpet", 17.4375, 78.4483, "commercial"),
    ("BGP", "Begumpet", 17.4447, 78.4664, "commercial"),
    ("SMG", "Somajiguda", 17.4239, 78.4587, "commercial"),
    ("SEC", "Secunderabad", 17.4399, 78.4983, "commercial"),
    ("ABD", "Abids", 17.3930, 78.4730, "commercial"),
    ("CHM", "Charminar", 17.3616, 78.4747, "commercial"),
    ("HMN", "Himayatnagar", 17.4010, 78.4880, "nightlife"),
    ("DSN", "Dilsukhnagar", 17.3688, 78.5247, "residential"),
    ("LBN", "LB Nagar", 17.3457, 78.5522, "residential"),
    ("UPL", "Uppal", 17.4058, 78.5591, "residential"),
    ("TRN", "Tarnaka", 17.4284, 78.5386, "residential"),
    ("MKG", "Malkajgiri", 17.4474, 78.5265, "residential"),
    ("ALW", "Alwal", 17.5020, 78.5090, "residential"),
    ("BWP", "Bowenpally", 17.4660, 78.4760, "residential"),
    ("KMP", "Kompally", 17.5350, 78.4850, "residential"),
    ("MDC", "Medchal", 17.6290, 78.4810, "residential"),
    ("MNK", "Manikonda", 17.4020, 78.3870, "residential"),
    ("MHP", "Mehdipatnam", 17.3950, 78.4400, "residential"),
    ("TLC", "Tolichowki", 17.3990, 78.4150, "nightlife"),
    ("ATP", "Attapur", 17.3660, 78.4290, "residential"),
    ("ASR", "AS Rao Nagar", 17.4810, 78.5560, "residential"),
    ("BCP", "Bachupally", 17.5440, 78.3650, "residential"),
    ("SMB", "Shamshabad", 17.2603, 78.3969, "commercial"),
]

# Demand shape per zone type: a base level plus (peak hour, width, height) bumps.
_BASE_DEMAND = 0.18
_DEMAND_BUMPS = {
    "tech": [(8.5, 1.0, 0.35), (13.0, 1.2, 1.1), (20.0, 1.5, 0.75)],
    "residential": [(8.5, 1.2, 0.5), (13.0, 1.3, 0.6), (20.5, 1.6, 1.15)],
    "nightlife": [(13.5, 1.3, 0.55), (21.0, 1.8, 1.2), (23.5, 1.5, 0.6)],
    "commercial": [(9.0, 1.0, 0.4), (13.0, 1.3, 1.0), (19.5, 1.5, 0.7)],
}
_WEEKEND_DEMAND = {"tech": 0.6, "residential": 1.2, "nightlife": 1.4, "commercial": 0.85}
_CONGESTION = {"tech": 1.2, "commercial": 1.1, "nightlife": 1.0, "residential": 0.8}


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(h))


ZONES = [
    {"id": zid, "name": name, "lat": lat, "lon": lon, "type": ztype}
    for zid, name, lat, lon, ztype in _ZONE_ROWS
]
ZONE_BY_ID = {z["id"]: z for z in ZONES}

# every busy place belongs to its nearest zone
for _place in PLACES:
    _place["zone_id"] = min(ZONES, key=lambda z: haversine_km(_place, z))["id"]
PLACE_BY_ID = {p["id"]: p for p in PLACES}

# a zone is as popular as the busy places inside it
for _zone in ZONES:
    _busy = sum(p["busy"] for p in PLACES if p["zone_id"] == _zone["id"])
    _zone["busy_total"] = _busy
_busiest = max(z["busy_total"] for z in ZONES)
for _zone in ZONES:  # 0.85 for a zone with nothing in it, up to 1.3 for the busiest
    _zone["popularity"] = round(0.85 + 0.45 * _zone.pop("busy_total") / _busiest, 2)


def _bump(hour, peak, width):
    d = abs(hour - peak)
    d = min(d, 24 - d)  # the day wraps around midnight
    return math.exp(-0.5 * (d / width) ** 2)


def hour_of(when):
    return when.hour + when.minute / 60


def is_weekend(when):
    return when.weekday() >= 5


def true_demand(zone, when):
    """The underlying demand index the synthetic market is generated from."""
    hour = hour_of(when)
    level = _BASE_DEMAND + sum(
        height * _bump(hour, peak, width)
        for peak, width, height in _DEMAND_BUMPS[zone["type"]]
    )
    if is_weekend(when):
        level *= _WEEKEND_DEMAND[zone["type"]]
    return level * zone["popularity"]


def place_busy_now(place, when):
    """How busy a place is right now, 0-5: its own busy score shaped by the
    time-of-day pattern for that kind of place."""
    profile = {"type": CATEGORY_PROFILE[place["category"]], "popularity": 1.0}
    return round(min(5.0, place["busy"] * true_demand(profile, when) / 1.4), 1)


def typical_traffic(zone, when):
    """Travel-time multiplier from rush hours (1.0 = free-flowing roads)."""
    hour = hour_of(when)
    rush = 0.45 * _bump(hour, 9.5, 1.4) + 0.65 * _bump(hour, 18.75, 1.7)
    if is_weekend(when):
        rush *= 0.6
    return 1 + _CONGESTION[zone["type"]] * rush


def poisson(rnd, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rnd.gauss(lam, math.sqrt(lam)))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rnd.random()
        if p <= limit:
            return k
        k += 1


def order_distance_km(rnd):
    return round(min(max(rnd.lognormvariate(1.15, 0.45), 0.8), 12.0), 1)


def order_payout(rnd, distance_km, demand):
    surge = 1 + max(0.0, demand - 1.0) * 0.35
    return round((28 + 11.5 * distance_km) * surge * rnd.uniform(0.92, 1.1))


def order_eta_min(rnd, distance_km, traffic):
    """Pickup wait plus ride time, in minutes."""
    return max(6, round(rnd.uniform(5, 9) + distance_km * 2.7 * traffic))


def generate_history(today=None, days=HISTORY_DAYS, seed=2026):
    """Generate `days` of orders for every zone and keep the aggregates the
    agents need: an hourly demand profile, per-zone averages and daily totals."""
    today = today or datetime.now(IST).date()
    rnd = random.Random(seed)
    # profile[zone_id][0=weekday, 1=weekend][hour] -> [order count, hours observed]
    profile = {z["id"]: [[[0, 0] for _ in range(24)] for _ in range(2)] for z in ZONES}
    zone_totals = {z["id"]: {"orders": 0, "payout": 0.0, "km": 0.0, "min": 0.0} for z in ZONES}
    daily = []
    total_orders = 0

    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        day_orders, day_payout = 0, 0.0
        hour_orders = [0] * 24
        zone_payout = {}
        for zone in ZONES:
            day_factor = rnd.uniform(0.85, 1.15)
            totals = zone_totals[zone["id"]]
            for hour in range(24):
                when = datetime(day.year, day.month, day.day, hour, 30, tzinfo=IST)
                demand = true_demand(zone, when)
                traffic = typical_traffic(zone, when)
                n = poisson(rnd, BASE_ORDERS_PER_HOUR * demand * day_factor)
                cell = profile[zone["id"]][1 if is_weekend(when) else 0][hour]
                cell[0] += n
                cell[1] += 1
                for _ in range(n):
                    km = order_distance_km(rnd)
                    payout = order_payout(rnd, km, demand)
                    totals["payout"] += payout
                    totals["km"] += km
                    totals["min"] += order_eta_min(rnd, km, traffic)
                    day_payout += payout
                    zone_payout[zone["id"]] = zone_payout.get(zone["id"], 0) + payout
                totals["orders"] += n
                hour_orders[hour] += n
                day_orders += n
        total_orders += day_orders
        daily.append(
            {
                "date": day.isoformat(),
                "orders": day_orders,
                "payout": round(day_payout),
                "hour_orders": hour_orders,
                "zone_payout": zone_payout,
            }
        )

    return {
        "days": days,
        "total_orders": total_orders,
        "zone_hours": days * len(ZONES) * 24,
        "profile": {
            zid: [[(c[0] / c[1] if c[1] else 0.0) for c in part] for part in parts]
            for zid, parts in profile.items()
        },
        "zone_avg": {
            zid: {
                "payout": t["payout"] / max(t["orders"], 1),
                "km": t["km"] / max(t["orders"], 1),
                "min": t["min"] / max(t["orders"], 1),
            }
            for zid, t in zone_totals.items()
        },
        "daily": daily,
    }


def load_history():
    """The market history takes several seconds to generate, so it is built once a day
    and kept on disk; a change to the zones, places or settings rebuilds it."""
    today = datetime.now(IST).date()
    hash_of = sum(len(z["name"]) + int(z["popularity"] * 100) for z in ZONES)
    name = f"history_{today}_{HISTORY_DAYS}d_{len(ZONES)}z_{len(PLACES)}p_{hash_of}.json"
    folder = Path(__file__).parent / "cache"
    path = folder / name
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    history = generate_history(today)
    try:
        folder.mkdir(exist_ok=True)
        for old in folder.glob("history_*.json"):
            old.unlink()
        path.write_text(json.dumps(history))
    except OSError:
        pass  # a read-only folder just means it is rebuilt next time
    return history


HISTORY = load_history()


def forecast_demand(zone_id, when):
    """Demand index learned from the order history: average orders in this
    zone at this hour on this kind of day, interpolated between hours."""
    part = HISTORY["profile"][zone_id][1 if is_weekend(when) else 0]
    frac = when.minute / 60
    now_rate, next_rate = part[when.hour], part[(when.hour + 1) % 24]
    return (now_rate * (1 - frac) + next_rate * frac) / BASE_ORDERS_PER_HOUR


def weekly_summary():
    """Last 7 days of the market order history: busiest window and zone."""
    week = HISTORY["daily"][-7:]
    hour_orders = [sum(d["hour_orders"][h] for d in week) for h in range(24)]
    start = max(range(22), key=lambda h: sum(hour_orders[h : h + 3]))
    zone_payout = {}
    for d in week:
        for zid, payout in d["zone_payout"].items():
            zone_payout[zid] = zone_payout.get(zid, 0) + payout
    best_zone = max(zone_payout, key=zone_payout.get)

    def clock(h):
        return datetime(2000, 1, 1, h % 24).strftime("%I %p").lstrip("0")

    return {
        "best_window": f"{clock(start)} - {clock(start + 3)}",
        "best_zone": ZONE_BY_ID[best_zone]["name"],
        "market_orders": sum(d["orders"] for d in week),
    }
