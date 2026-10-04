"""
GigPilot - agent logic.
Each function below is one "agent" from the architecture doc. The LLM
(if you wire one in) only ever narrates the result of these functions
in plain language - it never calculates payout, distance or cost itself.
That separation is the single strongest technical point in your pitch.

The agents are pure functions over a snapshot of the city (see
World.zone_views), so the same logic runs on simulated or real feeds.
"""

from datetime import timedelta

from data import VEHICLE_COST_PER_KM

MOVE_THRESHOLD = 1.08  # a move must beat staying put by 8% to be worth recommending
PLANNING_HORIZON_HOURS = 1.5
SURGE_HORIZON_HOURS = 4.0  # a known surge is worth planning further ahead for
SURGE_PAY = 2.0  # surge pricing on orders around the busy place
SURGE_WAIT_MIN = 1.0  # orders come back to back there

# ---------- deterministic tools ----------


def calculate_fuel_cost(distance_km, vehicle):
    rate = VEHICLE_COST_PER_KM.get(vehicle, 1.5)
    return round(distance_km * rate, 1)


def calculate_net_earning(payout, distance_km, vehicle):
    fuel = calculate_fuel_cost(distance_km, vehicle)
    operating_cost = round(payout * 0.03, 1)  # wear/misc, small flat %
    net = round(payout - fuel - operating_cost, 1)
    return net, fuel, operating_cost


def demand_level(index):
    if index >= 1.2:
        return "VERY HIGH"
    if index >= 0.85:
        return "HIGH"
    if index >= 0.5:
        return "MEDIUM"
    return "LOW"


# ---------- opportunity agent ----------


def opportunity_agent(zone, vehicle):
    """Orders open in this zone right now, best net earning per minute first."""
    opps = []
    for o in zone["open_orders"]:
        net, fuel, _ = calculate_net_earning(o["payout"], o["distance_km"], vehicle)
        opps.append({**o, "net_earning": net, "fuel_cost": fuel})
    return sorted(opps, key=lambda o: o["net_earning"] / o["eta_min"], reverse=True)


# ---------- demand agent ----------


def demand_agent(zones):
    results = []
    for z in zones:
        results.append(
            {
                "zone_id": z["id"],
                "zone_name": z["name"],
                "multiplier": z["demand"],
                "level": demand_level(z["demand"]),
                "traffic_factor": z["traffic"],
                "rain_mm": z["rain"],
                "incentive": z["incentive"],
                "busy_place": z["busy_place"],
            }
        )
    return results


# ---------- earnings agent ----------


def earnings_agent(state):
    remaining_target = max(state["target_earnings"] - state["earned_so_far"], 0)
    remaining_hours = max(state["available_hours"] - state["hours_elapsed"], 0)
    # None = shift is over, so no pace can still reach the goal
    required_rate = (
        round(remaining_target / remaining_hours, 1) if remaining_hours > 0 else None
    )
    return {
        "earned_so_far": state["earned_so_far"],
        "target_earnings": state["target_earnings"],
        "remaining_target": remaining_target,
        "remaining_hours": round(remaining_hours, 2),
        "required_rate_per_hour": required_rate,
    }


# ---------- optimization agent ----------


def optimization_agent(state, zones, vehicle, current_zone_id, travel, snoozed=()):
    """Score every zone by what the rider would actually net per hour there
    over the planning horizon, after losing the travel time and fuel to get
    there. `travel(a, b)` returns (minutes, km) in current traffic."""
    demand_data = demand_agent(zones)
    earnings_data = earnings_agent(state)
    surge_on = any(d["busy_place"] for d in demand_data)
    horizon = min(
        max(earnings_data["remaining_hours"], 0.5),
        SURGE_HORIZON_HOURS if surge_on else PLANNING_HORIZON_HOURS,
    )

    candidates = []
    for zone, d in zip(zones, demand_data):
        top_opps = opportunity_agent(zone, vehicle)[:3]
        hist = zone["history"]
        hist_net, _, _ = calculate_net_earning(hist["payout"], hist["km"], vehicle)
        hist_eta = 7 + hist["km"] * 2.7 * zone["traffic"]
        if top_opps:
            # blend what is on offer right now with the zone's long-run average
            avg_net = (sum(o["net_earning"] for o in top_opps) / len(top_opps) + hist_net) / 2
            avg_eta = (sum(o["eta_min"] for o in top_opps) / len(top_opps) + hist_eta) / 2
        else:
            avg_net, avg_eta = hist_net, hist_eta
        # search/wait time between orders shrinks as demand rises
        wait_min = min(max(9 / max(d["multiplier"], 0.05), 2.5), 40)
        if d["busy_place"]:
            avg_net, avg_eta, wait_min = hist_net * SURGE_PAY, hist_eta, SURGE_WAIT_MIN
        cycle_min = wait_min + avg_eta
        rate = avg_net * 60 / cycle_min

        travel_min, travel_km = travel(current_zone_id, zone["id"])

        incentive_rate, incentive_note = 0, None
        incentive = d["incentive"]
        if incentive:
            orders_left = incentive["orders_needed"] - incentive["progress"]
            needed_min = orders_left * cycle_min
            if travel_min + needed_min <= incentive["minutes_left"]:
                incentive_rate = incentive["bonus"] / max(needed_min / 60, 1.0)
                incentive_note = incentive["description"]

        working_share = max(horizon - travel_min / 60, 0) / horizon
        score = (rate + incentive_rate) * working_share - (
            calculate_fuel_cost(travel_km, vehicle) / horizon
        )
        candidates.append(
            {
                "zone_id": d["zone_id"],
                "zone_name": d["zone_name"],
                "demand_level": d["level"],
                "demand_index": d["multiplier"],
                "net_rate_low": round(rate * 0.85),
                "net_rate_high": round(rate * 1.15 + incentive_rate),
                "expected_rate": round(rate + incentive_rate),
                "travel_penalty_min": round(travel_min),
                "travel_km": travel_km,
                "traffic_factor": d["traffic_factor"],
                "rain_mm": d["rain_mm"],
                "open_orders": len(zone["open_orders"]),
                "incentive_note": incentive_note,
                "busy_place": d["busy_place"],
                "score": round(score, 1),
            }
        )

    ranked = sorted(candidates, key=lambda c: c["score"], reverse=True)
    current = next(c for c in ranked if c["zone_id"] == current_zone_id)
    allowed = [c for c in ranked if c["zone_id"] not in snoozed or c is current]
    best = allowed[0]
    if best is not current and best["score"] < current["score"] * MOVE_THRESHOLD + 10:
        best = current  # not enough gain to justify the ride
    alternative = next(c for c in allowed if c is not best)

    margin = (best["score"] - alternative["score"]) / max(best["score"], 1)
    confidence = min(95, max(55, int(60 + margin * 150)))

    return {
        "ranked_candidates": ranked,
        "best": best,
        "current": current,
        "confidence": confidence,
        "earnings_data": earnings_data,
        "demand_data": demand_data,
    }


# ---------- planning agent (explanation step) ----------


def planning_agent(opt_result, current_zone_id):
    best, current = opt_result["best"], opt_result["current"]
    ranked = opt_result["ranked_candidates"]
    e = opt_result["earnings_data"]

    trace = [
        f"Goal: Rs {e['target_earnings']:.0f} | Earned so far: Rs {e['earned_so_far']:.0f} "
        f"| Remaining: Rs {e['remaining_target']:.0f} in {e['remaining_hours']:.1f}h"
    ]
    if e["remaining_target"] == 0:
        trace.append("Goal already reached - anything earned now is extra")
    elif e["required_rate_per_hour"] is None:
        trace.append(
            f"No shift time left - Rs {e['remaining_target']:.0f} of the goal is still open"
        )
    else:
        trace.append(
            f"Required pace to hit goal: ~Rs {e['required_rate_per_hour']:.0f}/hour"
        )

    levels = [c["demand_level"] for c in ranked]
    trace.append(
        f"Demand scan of {len(ranked)} zones: "
        + ", ".join(
            f"{levels.count(level)} {level}"
            for level in ("VERY HIGH", "HIGH", "MEDIUM", "LOW")
            if level in levels
        )
    )
    raining = [c["zone_name"] for c in ranked if c["rain_mm"] >= 0.5]
    if raining:
        trace.append(
            f"Rain in {len(raining)} zone(s) ({', '.join(raining[:3])}"
            f"{'...' if len(raining) > 3 else ''}) - demand up, roads slower"
        )
    trace.append(
        "Top zones after travel cost: "
        + "; ".join(
            f"{c['zone_name']} Rs {c['expected_rate']}/hr ({c['travel_penalty_min']} min away)"
            for c in ranked[:3]
        )
    )
    trace.append(
        f"Current zone {current['zone_name']}: Rs {current['expected_rate']}/hr, "
        f"{current['open_orders']} open order(s), traffic x{current['traffic_factor']}"
    )
    trace.append(
        f"Chosen: {best['zone_name']} | expected "
        f"Rs {best['net_rate_low']}-{best['net_rate_high']}/hr "
        f"| travel cost ~{best['travel_penalty_min']} min"
    )
    if best["incentive_note"]:
        trace.append(f"Active incentive in this zone: {best['incentive_note']}")
    busy = next((c for c in ranked if c["busy_place"]), None)
    if busy:
        trace.append(
            f"Busy place alert: {busy['busy_place']} ({busy['zone_name']} zone) - "
            f"Rs {busy['expected_rate']}/hr, {busy['travel_penalty_min']} min away"
        )
    trace.append(f"Decision confidence: {opt_result['confidence']}%")

    if best["zone_id"] == current_zone_id:
        action = f"Stay in {best['zone_name']}"
        reason = (
            f"{best['zone_name']} currently offers the best expected rate (Rs "
            f"{best['net_rate_low']}-{best['net_rate_high']}/hr) once travel time "
            f"to any other zone is counted."
        )
    else:
        action = f"Move to {best['zone_name']}"
        reason = (
            f"{best['zone_name']} is forecasted at Rs "
            f"{best['net_rate_low']}-{best['net_rate_high']}/hr "
            f"({best['demand_level']} demand) against Rs {current['expected_rate']}/hr "
            f"here, which beats staying put even after the "
            f"~{best['travel_penalty_min']} min ride ({best['travel_km']:.1f} km)."
        )
    if best["busy_place"]:
        reason += f" {best['busy_place']} is the busy place right now - orders are surging there."
    if best["rain_mm"] >= 0.5:
        reason += " Rain there is pushing more people to order in."
    if best["incentive_note"]:
        reason += (
            f" An active incentive there adds further upside: {best['incentive_note']}."
        )

    return {
        "action": action,
        "reason": reason,
        "decision_trace": trace,
        "confidence": opt_result["confidence"],
        "net_rate_low": best["net_rate_low"],
        "net_rate_high": best["net_rate_high"],
        "expected_rate": best["expected_rate"],
        "target_zone_id": best["zone_id"],
        "target_zone_name": best["zone_name"],
        "busy_place": best["busy_place"],
    }


def hourly_rate(zone, vehicle):
    """Expected net Rs/hour in a zone from its demand, traffic and order history alone -
    usable for a future hour, when there are no live orders to look at yet."""
    hist = zone["history"]
    surge = 1 + max(0.0, zone["demand"] - 1.0) * 0.35  # same surge rule the orders follow
    net, _, _ = calculate_net_earning(hist["payout"] * surge, hist["km"], vehicle)
    eta = 7 + hist["km"] * 2.7 * zone["traffic"]
    wait = min(max(9 / max(zone["demand"], 0.05), 2.5), 40)
    if zone.get("busy_place"):
        net, wait = net * SURGE_PAY, SURGE_WAIT_MIN
    return net * 60 / (wait + eta)


def plan_shift(state, slots, start_zone_id, vehicle, travel, first=None):
    """The rest of the shift, hour by hour: where to be, what it should pay, and the
    running total against the goal. Part of the planning agent's job.

    `slots` are the remaining stretches of the shift in order, each
    {"start": datetime, "hours": length, "zones": zone views for that time}. A move is
    planned only if it beats staying by MOVE_THRESHOLD after the ride there.
    `first` = (zone_id, rate) pins the first slot to the live recommendation."""
    earned = max(state["earned_so_far"], 0)
    target = state["target_earnings"]
    here = start_zone_id
    rows, goal_time = [], None

    for i, slot in enumerate(slots):
        options = {}
        for zone in slot["zones"]:
            rate = hourly_rate(zone, vehicle)
            if i == 0 and first and zone["id"] == first[0]:
                rate = first[1]
            ride_min, ride_km = travel(here, zone["id"])
            usable = max(slot["hours"] - ride_min / 60, 0)
            gain = rate * usable - calculate_fuel_cost(ride_km, vehicle)
            options[zone["id"]] = (gain, rate, ride_min, zone)
        stay = options[here]
        pick = max(options.values(), key=lambda o: o[0])
        if i == 0 and first:
            pick = options[first[0]]
        elif pick[3]["id"] != here and pick[0] < stay[0] * MOVE_THRESHOLD + 5:
            pick = stay
        gain, rate, ride_min, zone = pick
        gain = max(gain, 0)

        if goal_time is None and gain > 0 and earned < target <= earned + gain:
            into = (target - earned) / gain * slot["hours"] * 60
            goal_time = slot["start"] + timedelta(minutes=round(into))
        earned += gain
        rows.append(
            {
                "time": slot["start"].strftime("%H:%M"),
                "zone_id": zone["id"],
                "zone_name": zone["name"],
                "move_min": round(ride_min) if zone["id"] != here else 0,
                "rate": round(rate),
                "earn": round(gain),
                "total": round(earned),
                "demand_level": demand_level(zone["demand"]),
                "rain_mm": zone["rain"],
                "temp": zone["temp"],
                "busy_place": zone.get("busy_place"),
                "goal_reached": earned >= target,
            }
        )
        here = zone["id"]

    best = max(rows, key=lambda r: r["rate"], default=None)
    return {
        "rows": rows,
        "projected": round(earned),
        "goal": round(target),
        "goal_time": goal_time.strftime("%H:%M") if goal_time else None,
        "already_reached": state["earned_so_far"] >= target,
        "shortfall": max(round(target - earned), 0),
        "best_hour": best and {"time": best["time"], "zone_name": best["zone_name"],
                               "rate": best["rate"]},  # fmt: skip
        "rain_hours": [r["time"] for r in rows if r["rain_mm"] >= 0.5],
        "moves": sum(1 for r in rows if r["move_min"]),
    }


# ---------- master orchestrator ----------


def master_agent(state, zones, vehicle, current_zone_id, travel, snoozed=()):
    opt_result = optimization_agent(state, zones, vehicle, current_zone_id, travel, snoozed)
    recommendation = planning_agent(opt_result, current_zone_id)
    recommendation["ranked_candidates"] = opt_result["ranked_candidates"]
    recommendation["earnings_data"] = opt_result["earnings_data"]
    return recommendation
