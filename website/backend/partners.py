"""
GigPilot - partner apps (Swiggy, Zomato, Zepto, Blinkit and others).

In production every completed order would arrive from the delivery
platforms' own systems. None of them offers that feed publicly, so this
module imagines it: which app an order came from, which merchant, and a
back-history of shifts and orders "synced" into a rider's account the
first time GigPilot learns where they work. All of it is generated data.
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
    ("Swiggy", "food", 0.30),
    ("Zomato", "food", 0.30),
    ("Zepto", "grocery", 0.13),
    ("Blinkit", "grocery", 0.11),
    ("Swiggy Instamart", "grocery", 0.10),
    ("BigBasket", "grocery", 0.06),
]
PLATFORM_NAMES = {p[0] for p in PLATFORMS}

RESTAURANTS = [
    # Hyderabadi and biryani
    "Paradise Biryani", "Bawarchi", "Shah Ghouse", "Cafe Bahar", "Pista House", "Mehfil",
    "Shadab", "Hotel Nayaab", "Sarvi", "Astoria", "Grand Hotel", "Cafe 555", "Niloufer Cafe",
    "Behrouz Biryani", "Biryani Blues", "Bahar Biryani Cafe", "Hotel Rumaan", "Alpha Hotel",
    "Meridian Restaurant", "4 Seasons", "Lucky Restaurant", "Mandi King", "Barkaas",
    "Mataam Al Arabi", "Hyderabad House", "Blue Sea Tea and Snacks", "Nimrah Cafe",
    # South Indian and Andhra
    "Chutneys", "Minerva Coffee Shop", "Kritunga", "Rayalaseema Ruchulu", "Subbayya Gari Hotel",
    "Ram ki Bandi", "Ulavacharu", "Sri Kanya Comfort", "Taaza Kitchen", "Hotel Suprabhat",
    "Kamat Hotel", "Udupi Upahar", "Govind Dosa", "Pragati Tiffin Centre", "Santosh Dhaba",
    "Kakatiya Deluxe Mess", "Spicy Venue", "Vivaha Bhojanambu", "The Spicy Venue Express",
    "Simply South", "Dakshin Kitchen", "Mylapore Tiffins", "Rajdhani Thali", "Ohri's",
    "Palle Vindu", "Babai Hotel", "Hotel Swagath", "Sri Sai Tiffins", "Varalakshmi Tiffins",
    # Bakeries, sweets, desserts
    "Karachi Bakery", "Almond House", "Cream Stone", "Dadu's Sweets", "Gokul Chat",
    "Concu", "Labonel", "Universal Bakers", "King and Cardinal", "Subhan Bakery",
    "Pulla Reddy Sweets", "Emerald Sweets", "Naturals Ice Cream", "Baskin Robbins",
    "Theobroma", "Mio Amore", "Famous Ice Cream", "Agra Sweets",
    # Cafes and chains
    "Burger King", "KFC", "McDonald's", "Domino's Pizza", "Pizza Hut", "Subway", "Faasos",
    "Absolute Barbecues", "Starbucks", "Chai Point", "Chaayos", "Third Wave Coffee",
    "Wow Momo", "Haldiram's", "Bikanervala", "Taco Bell", "La Pino'z Pizza", "Oven Story",
    "Box8", "EatFit", "Lunch Box", "Sweet Truth", "The Good Bowl", "Biggies Burger",
    "Chinese Wok", "Mainland China", "Nanking", "Hao Chi", "Punjabi Rasoi", "Kebabs and Curries",
    "Roastery Coffee House", "Autumn Leaf Cafe", "Conçu Cafe", "The Hole in the Wall Cafe",
    "Fusion 9", "Flechazo", "Sahib Sindh Sultan", "Jewel of Nizam", "Exotica", "Tatva",
]  # fmt: skip
GROCERY_BASKETS = ["groceries", "fruit and vegetables", "dairy and bread", "snacks and drinks",
                   "household items", "personal care", "baby care", "medicines"]  # fmt: skip

HOME_ZONE_WEIGHTS = [6, 3, 2, 2, 1, 1]  # most orders in the home zone, the rest in its neighbours
ORDERS_PER_HOUR_AT_PEAK = 3.7
DAY_OFF_CHANCE = 0.2
# (first hour, hours worked): the shapes a rider's day usually takes
SHIFT_PATTERNS = [(11, 6), (11, 8), (12, 9), (16, 6), (17, 6), (17, 7), (18, 5), (10, 5)]


def pick_partner(rnd, zone_name):
    """Which app an order came through, and the merchant it was picked up from."""
    name, kind, _ = rnd.choices(PLATFORMS, weights=[p[2] for p in PLATFORMS])[0]
    if kind == "food":
        return name, rnd.choice(RESTAURANTS)
    return name, f"{name} store, {zone_name} ({rnd.choice(GROCERY_BASKETS)})"


def net_earning(payout, distance_km, vehicle="bike"):
    fuel = distance_km * VEHICLE_COST_PER_KM.get(vehicle, 1.5)
    return round(payout - fuel - payout * 0.03, 1)


def past_shifts(user_id, home_zone_id, first_day, last_day):
    """The rider's past shifts on the partner apps from `first_day` up to (not including)
    `last_day`: one per day worked, each with the goal they set that day and its orders."""
    rnd = random.Random(f"partner-sync-{user_id}")
    home = ZONE_BY_ID[home_zone_id]
    nearby = sorted(ZONES, key=lambda z: haversine_km(home, z))[: len(HOME_ZONE_WEIGHTS)]
    typical_goal = rnd.choice([900, 1000, 1100, 1200])  # this rider's usual daily target
    vehicle = rnd.choices(["bike", "scooter"], weights=[2, 1])[0]
    shifts = []
    day = first_day
    while day < last_day:
        if rnd.random() >= DAY_OFF_CHANCE:
            effort = rnd.uniform(0.75, 1.2)  # some days go better than others
            first_hour, length = rnd.choice(SHIFT_PATTERNS)
            orders = []
            for hour in range(first_hour, min(first_hour + length, 24)):
                when = datetime(day.year, day.month, day.day, hour, tzinfo=IST)
                rate = ORDERS_PER_HOUR_AT_PEAK * true_demand(home, when) * effort
                for _ in range(poisson(rnd, rate)):
                    zone = rnd.choices(nearby, weights=HOME_ZONE_WEIGHTS)[0]
                    km = order_distance_km(rnd)
                    payout = order_payout(rnd, km, true_demand(zone, when))
                    tip = rnd.choice([10, 20, 20, 30, 50]) if rnd.random() < 0.22 else 0
                    platform, merchant = pick_partner(rnd, zone["name"])
                    orders.append(
                        {
                            "ts": when + timedelta(minutes=rnd.randint(0, 59)),
                            "amount": net_earning(payout, km, vehicle) + tip,
                            "zone_id": zone["id"],
                            "platform": platform,
                            "merchant": merchant,
                            "distance_km": km,
                            "note": f"includes Rs {tip} tip" if tip else None,
                        }
                    )
            if orders:
                orders.sort(key=lambda o: o["ts"])
                start = min(orders[0]["ts"] - timedelta(minutes=5),
                            datetime(day.year, day.month, day.day, first_hour, tzinfo=IST))
                end = orders[-1]["ts"] + timedelta(minutes=rnd.randint(15, 35))
                # the goal moves around the rider's usual target, a little higher at weekends
                goal = typical_goal + rnd.choice([-200, -100, 0, 0, 100, 200])
                goal += 150 if day.weekday() >= 5 else 0
                shifts.append(
                    {
                        "started_at": start,
                        "ended_at": end,
                        "target_earnings": goal,
                        "available_hours": round((end - start).total_seconds() / 3600 * 2) / 2,
                        "vehicle": vehicle,
                        "orders": orders,
                    }
                )
        day += timedelta(days=1)
    return shifts


def goal_for_day(user_id, day):
    """A plausible goal for a past day that only has orders on record."""
    rnd = random.Random(f"goal-{user_id}-{day}")
    return rnd.choice([900, 1000, 1100, 1200]) + rnd.choice([-100, 0, 0, 100])
