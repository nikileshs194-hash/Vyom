"""
GigPilot - partner apps (Swiggy, Zomato, Zepto, Blinkit).

In production every completed order would arrive from the delivery
platforms' own systems. None of them offers that feed publicly, so this
module imagines it: which app an order came from, which merchant, and a
back-history of orders "synced" into a rider's account the first time
GigPilot learns where they work. All of it is generated data.
"""

import random
from datetime import datetime, timedelta

from data import (
    IST,
    VEHICLE_COST_PER_KM,
    ZONE_BY_ID,
    ZONES,
    haversine_km,
    order_distance_km,
    order_payout,
    poisson,
    true_demand,
)

# (name, kind, share of orders)
PLATFORMS = [
    ("Swiggy", "food", 0.36),
    ("Zomato", "food", 0.36),
    ("Zepto", "grocery", 0.16),
    ("Blinkit", "grocery", 0.12),
]

RESTAURANTS = [
    "Paradise Biryani", "Bawarchi", "Shah Ghouse", "Cafe Bahar", "Pista House",
    "Mehfil", "Chutneys", "Minerva Coffee Shop", "Kritunga", "Rayalaseema Ruchulu",
    "Subbayya Gari Hotel", "Ram ki Bandi", "Shadab", "Hotel Nayaab", "Karachi Bakery",
    "Almond House", "Cream Stone", "Santosh Dhaba", "Ulavacharu", "Absolute Barbecues",
    "Behrouz Biryani", "Faasos", "Burger King", "KFC", "McDonald's", "Domino's Pizza",
    "Pizza Hut", "Subway", "Sri Kanya Comfort", "Taaza Kitchen", "Hotel Suprabhat",
    "Niloufer Cafe", "Grand Hotel", "Cafe 555", "Sarvi", "Astoria", "Kamat Hotel",
    "Udupi Upahar", "Dadu's Sweets", "Gokul Chat",
]  # fmt: skip

HOME_ZONE_WEIGHTS = [5, 3, 2, 1]  # most orders in the home zone, the rest in its neighbours
ORDERS_PER_HOUR_AT_PEAK = 1.9
DAY_OFF_CHANCE = 0.2


def pick_partner(rnd, zone_name):
    """Which app an order came through, and the merchant it was picked up from."""
    name, kind, _ = rnd.choices(PLATFORMS, weights=[p[2] for p in PLATFORMS])[0]
    merchant = rnd.choice(RESTAURANTS) if kind == "food" else f"{name} store, {zone_name}"
    return name, merchant


def net_earning(payout, distance_km, vehicle="bike"):
    fuel = distance_km * VEHICLE_COST_PER_KM.get(vehicle, 1.5)
    return round(payout - fuel - payout * 0.03, 1)


def past_orders(user_id, home_zone_id, first_day, now):
    """Orders this rider 'completed' on the partner apps from `first_day` up
    to yesterday, as rows for the earnings table."""
    rnd = random.Random(f"partner-sync-{user_id}")
    home = ZONE_BY_ID[home_zone_id]
    nearby = sorted(ZONES, key=lambda z: haversine_km(home, z))[: len(HOME_ZONE_WEIGHTS)]
    rows = []
    day = first_day
    while day < now.date():
        if rnd.random() >= DAY_OFF_CHANCE:
            effort = rnd.uniform(0.6, 1.2)  # some days are longer shifts than others
            for hour in range(8, 24):
                when = datetime(day.year, day.month, day.day, hour, tzinfo=IST)
                rate = ORDERS_PER_HOUR_AT_PEAK * true_demand(home, when) * effort
                for _ in range(poisson(rnd, rate)):
                    zone = rnd.choices(nearby, weights=HOME_ZONE_WEIGHTS)[0]
                    km = order_distance_km(rnd)
                    payout = order_payout(rnd, km, true_demand(zone, when))
                    platform, merchant = pick_partner(rnd, zone["name"])
                    rows.append(
                        {
                            "ts": when + timedelta(minutes=rnd.randint(0, 59)),
                            "amount": net_earning(payout, km),
                            "zone_id": zone["id"],
                            "platform": platform,
                            "merchant": merchant,
                            "distance_km": km,
                        }
                    )
        day += timedelta(days=1)
    return sorted(rows, key=lambda r: r["ts"])
