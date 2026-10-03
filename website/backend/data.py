"""
GigPilot - city model and synthetic market data.

Zone locations are real Bengaluru localities. Everything that a delivery
platform would normally own (orders, payouts, demand history) is generated
here, because no platform exposes it for free. It is generated from one
consistent demand model, so history, live orders and forecasts agree.
"""

import math
import random
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

VEHICLE_COST_PER_KM = {
    "bike": 1.2,
    "scooter": 1.8,
    "car": 3.5,
}

HISTORY_DAYS = 28
BASE_ORDERS_PER_HOUR = 12  # market-wide orders per zone-hour at demand index 1.0

# (id, name, lat, lon, type)
_ZONE_ROWS = [
    ("KOR", "Koramangala", 12.9352, 77.6245, "nightlife"),
    ("IND", "Indiranagar", 12.9784, 77.6408, "nightlife"),
    ("HSR", "HSR Layout", 12.9116, 77.6474, "residential"),
    ("WHF", "Whitefield", 12.9698, 77.7500, "tech"),
    ("ECT", "Electronic City", 12.8452, 77.6602, "tech"),
    ("MAR", "Marathahalli", 12.9591, 77.6974, "tech"),
    ("BEL", "Bellandur", 12.9304, 77.6784, "tech"),
    ("BTM", "BTM Layout", 12.9166, 77.6101, "residential"),
    ("JAY", "Jayanagar", 12.9308, 77.5838, "residential"),
    ("JPN", "JP Nagar", 12.9063, 77.5857, "residential"),
    ("BSK", "Banashankari", 12.9255, 77.5468, "residential"),
    ("BAS", "Basavanagudi", 12.9421, 77.5754, "residential"),
    ("MGR", "MG Road", 12.9756, 77.6066, "commercial"),
    ("SHV", "Shivajinagar", 12.9857, 77.6057, "commercial"),
    ("MAL", "Malleshwaram", 13.0031, 77.5643, "residential"),
    ("RAJ", "Rajajinagar", 12.9910, 77.5550, "residential"),
    ("YPR", "Yeshwanthpur", 13.0280, 77.5400, "commercial"),
    ("HEB", "Hebbal", 13.0358, 77.5970, "residential"),
    ("YEL", "Yelahanka", 13.1007, 77.5963, "residential"),
    ("RTN", "RT Nagar", 13.0210, 77.5950, "residential"),
    ("KLN", "Kalyan Nagar", 13.0280, 77.6400, "nightlife"),
    ("KRP", "KR Puram", 13.0075, 77.6959, "residential"),
    ("DOM", "Domlur", 12.9610, 77.6387, "commercial"),
    ("SJP", "Sarjapur Road", 12.9010, 77.6860, "tech"),
    ("BGR", "Bannerghatta Road", 12.8876, 77.5970, "residential"),
    ("VIJ", "Vijayanagar", 12.9719, 77.5309, "residential"),
    ("MAJ", "Majestic", 12.9767, 77.5713, "commercial"),
    ("FRZ", "Frazer Town", 12.9982, 77.6140, "nightlife"),
    ("BRK", "Brookefield", 12.9655, 77.7185, "tech"),
    ("MNY", "Manyata Tech Park", 13.0477, 77.6210, "tech"),
    ("KEN", "Kengeri", 12.9080, 77.4850, "residential"),
    ("CVR", "CV Raman Nagar", 12.9850, 77.6630, "residential"),
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


def _popularity(zone_id):
    return round(random.Random("pop" + zone_id).uniform(0.85, 1.25), 2)


ZONES = [
    {
        "id": zid,
        "name": name,
        "lat": lat,
        "lon": lon,
        "type": ztype,
        "popularity": _popularity(zid),
    }
    for zid, name, lat, lon, ztype in _ZONE_ROWS
]
ZONE_BY_ID = {z["id"]: z for z in ZONES}


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


HISTORY = generate_history()


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
