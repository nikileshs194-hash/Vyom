"""
GigPilot - mock data generator.
Everything here is synthetic but internally consistent, so the demo
always behaves sensibly no matter what the worker enters.
"""

import random

HOUR_BLOCKS = ["morning", "afternoon", "evening", "night"]

VEHICLE_COST_PER_KM = {
    "bike": 1.2,
    "scooter": 1.8,
    "car": 3.5,
}


def default_zones():
    """Each zone has a demand multiplier per hour-block, a traffic factor
    (changed live by 'simulate event' actions), and an optional incentive."""
    return [
        {
            "id": "A",
            "name": "Zone A - Koramangala",
            "demand": {"morning": 1.3, "afternoon": 0.9, "evening": 1.1, "night": 0.7},
            "traffic_factor": 1.0,
            "distance_from_last_km": 0,
            "incentive": None,
        },
        {
            "id": "B",
            "name": "Zone B - Indiranagar",
            "demand": {"morning": 0.8, "afternoon": 1.0, "evening": 1.4, "night": 1.1},
            "traffic_factor": 1.0,
            "distance_from_last_km": 6,
            "incentive": None,
        },
        {
            "id": "C",
            "name": "Zone C - HSR Layout",
            "demand": {"morning": 1.0, "afternoon": 1.1, "evening": 1.6, "night": 0.9},
            "traffic_factor": 1.0,
            "distance_from_last_km": 9,
            "incentive": None,
        },
        {
            "id": "D",
            "name": "Zone D - Whitefield",
            "demand": {"morning": 1.5, "afternoon": 0.7, "evening": 0.8, "night": 0.6},
            "traffic_factor": 1.0,
            "distance_from_last_km": 14,
            "incentive": None,
        },
    ]


def generate_opportunities(zone, n=4, seed_offset=0):
    """A handful of candidate orders available right now in this zone."""
    rnd = random.Random(zone["id"] + str(seed_offset))
    opps = []
    for i in range(n):
        payout = round(rnd.uniform(80, 220), 0)
        distance = round(rnd.uniform(1.5, 7.0), 1)
        eta = int(distance * rnd.uniform(3.5, 5.5))
        opps.append(
            {
                "id": f"{zone['id']}-{i+1}",
                "zone_id": zone["id"],
                "payout": payout,
                "distance_km": distance,
                "eta_min": eta,
            }
        )
    return opps


def weekly_summary_mock():
    """Pre-seeded 'last 7 days' stats for the personalization story."""
    return {
        "best_window": "6:30 PM - 9:00 PM",
        "best_zone": "Zone B - Indiranagar",
        "avg_net_rate": 278,
        "note": "Zone D recommendations were less reliable because travel time varied more.",
    }
