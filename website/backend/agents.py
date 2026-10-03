"""
GigPilot - agent logic.
Each function below is one "agent" from the architecture doc. The LLM
(if you wire one in) only ever narrates the result of these functions
in plain language - it never calculates payout, distance or cost itself.
That separation is the single strongest technical point in your pitch.

The agents are pure functions over a snapshot of the city (see
World.zone_views), so the same logic runs on simulated or real feeds.
"""

from data import VEHICLE_COST_PER_KM

MOVE_THRESHOLD = 1.08  # a move must beat staying put by 8% to be worth recommending
PLANNING_HORIZON_HOURS = 1.5

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
    horizon = min(max(earnings_data["remaining_hours"], 0.5), PLANNING_HORIZON_HOURS)

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
    }


# ---------- master orchestrator ----------


def master_agent(state, zones, vehicle, current_zone_id, travel, snoozed=()):
    opt_result = optimization_agent(state, zones, vehicle, current_zone_id, travel, snoozed)
    recommendation = planning_agent(opt_result, current_zone_id)
    recommendation["ranked_candidates"] = opt_result["ranked_candidates"]
    recommendation["earnings_data"] = opt_result["earnings_data"]
    return recommendation
