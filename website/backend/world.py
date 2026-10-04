"""
GigPilot - live city state.

Keeps a picture of the city that moves in step with the real clock: open
orders, traffic and incentives per zone, plus one rider per logged-in user
with a shift running. Riders are placed by their real GPS position; the
order feed and incentives stand in for partner (Swiggy/Zomato) data, which
no platform publishes, and are generated from the demand model in data.py.
"""

import random
from collections import deque
from datetime import datetime, timedelta

from data import (
    HISTORY,
    IST,
    VEHICLE_COST_PER_KM,
    ZONE_BY_ID,
    ZONES,
    forecast_demand,
    order_distance_km,
    order_eta_min,
    order_payout,
    poisson,
    typical_traffic,
)
from partners import pick_partner

VISIBLE_ORDERS_PER_MIN = 0.4  # orders offered to a rider per minute at demand 1.0
INCENTIVE_CHANCE_PER_MIN = 0.0015
SPIKE_MINUTES = 45
RAIN_BURST_MINUTES = 60
RAIN_BURST_MM = 6.0
BUSY_PLACE_MINUTES = 240
BUSY_PLACE_BOOST = 3.5  # demand multiplier in the zone of the active busy place
WARM_UP_MINUTES = 12
CITY_DRIVE_FACTOR = 1.15  # OSRM free-flow times are optimistic for city riding


def fuel_cost(distance_km, vehicle):
    return round(distance_km * VEHICLE_COST_PER_KM.get(vehicle, 1.5), 1)


class World:
    def __init__(self, weather, roads, traffic, now=None, seed=None, on_earning=None,
                 on_rider_change=None):  # fmt: skip
        self.weather, self.roads, self.live_traffic = weather, roads, traffic
        self.on_rider_change = on_rider_change or (lambda rider: None)
        self.on_earning = on_earning or (lambda rider, amount, zone_id, kind, **details: None)
        self.rnd = random.Random(seed)
        # start a little early: the warm-up below advances to the requested time
        self.now = (now or datetime.now(IST)) - timedelta(minutes=WARM_UP_MINUTES)
        self.zones = {
            z["id"]: {
                "open_orders": [],
                "traffic_noise": 0.0,
                "incentive": None,
                "spike_until": None,
                "rain_until": None,
            }
            for z in ZONES
        }
        self.events = deque(maxlen=40)  # city-wide events
        self.busy_place = None  # {"place": ..., "until": ...} while a surge is on
        self.riders = {}  # user_id -> rider
        self._order_seq = 0
        self.advance(WARM_UP_MINUTES, quiet=True)  # so zones start with open orders

    # ------------------------------------------------------------ conditions

    def rain(self, zone_id, when=None):
        when = when or self.now
        until = self.zones[zone_id]["rain_until"]
        if until and when < until:
            return RAIN_BURST_MM
        return self.weather.at(zone_id, when)["rain"]

    def traffic(self, zone_id):
        state = self.zones[zone_id]
        factor = self.live_traffic.factor(zone_id)
        if factor is None:
            factor = typical_traffic(ZONE_BY_ID[zone_id], self.now)
            factor *= 1 + state["traffic_noise"]
            factor *= 1 + min(0.5, 0.12 * self.rain(zone_id))
        if state["spike_until"] and self.now < state["spike_until"]:
            factor = max(factor * 1.6, 1.8)
        return round(max(factor, 0.8), 2)

    def demand(self, zone_id, when=None):
        """History-based forecast, lifted by rain (people order in)."""
        when = when or self.now
        boost = 1 + min(0.35, 0.1 * self.rain(zone_id, when))
        surge = self.busy_place
        if surge and surge["place"]["zone_id"] == zone_id and when < surge["until"]:
            boost *= BUSY_PLACE_BOOST
        return round(forecast_demand(zone_id, when) * boost, 2)

    def travel(self, a, b):
        """(minutes, km) to ride from zone a to zone b in current traffic."""
        if a == b:
            return 0.0, 0.0
        congestion = (self.traffic(a) + self.traffic(b)) / 2
        minutes = self.roads.drive_minutes(a, b) * CITY_DRIVE_FACTOR * congestion
        return round(minutes, 1), round(self.roads.distance_km(a, b), 1)

    # ---------------------------------------------------------------- events

    def _event(self, kind, text):
        return {"time": self.now.strftime("%H:%M"), "kind": kind, "text": text}

    def log(self, kind, text, quiet=False):
        if not quiet:
            self.events.appendleft(self._event(kind, text))

    def log_rider(self, rider, kind, text):
        rider["events"].appendleft(self._event(kind, text))

    def spike_traffic(self, zone_id):
        self.zones[zone_id]["spike_until"] = self.now + timedelta(minutes=SPIKE_MINUTES)
        self.log("traffic", f"Traffic spike in {ZONE_BY_ID[zone_id]['name']} (demo)")

    def start_rain(self, zone_id):
        self.zones[zone_id]["rain_until"] = self.now + timedelta(minutes=RAIN_BURST_MINUTES)
        self.log("rain", f"Heavy rain in {ZONE_BY_ID[zone_id]['name']} (demo)")

    def start_incentive(self, zone_id, bonus=100, orders_needed=3, minutes=180, quiet=False):
        self.zones[zone_id]["incentive"] = {
            "description": f"Complete {orders_needed} orders for Rs {bonus} bonus",
            "bonus": bonus,
            "orders_needed": orders_needed,
            "progress": {},  # user_id -> orders completed
            "expires": self.now + timedelta(minutes=minutes),
        }
        self.log(
            "incentive",
            f"Incentive live in {ZONE_BY_ID[zone_id]['name']}: Rs {bonus} for "
            f"{orders_needed} orders",
            quiet,
        )

    def set_busy_place(self, place):
        """Make `place` the city's busy spot (replacing any other), or clear it."""
        if place is None:
            if self.busy_place:
                self.log("busy", f"{self.busy_place['place']['name']} is back to normal")
            self.busy_place = None
            return
        self.busy_place = {
            "place": place,
            "until": self.now + timedelta(minutes=BUSY_PLACE_MINUTES),
        }
        self.log("busy", f"Rush at {place['name']} - orders surging")

    def reset_conditions(self):
        self.busy_place = None
        for state in self.zones.values():
            state.update(spike_until=None, rain_until=None, incentive=None, traffic_noise=0.0)
        self.log("reset", "All demo zone events cleared")

    # ------------------------------------------------------------- the clock

    def advance_to(self, when):
        """Catch the city up to the real clock, one minute at a time."""
        minutes = int((when - self.now).total_seconds() // 60)
        if minutes > 240:  # the machine slept: skip ahead instead of replaying it all
            self.now = when - timedelta(minutes=WARM_UP_MINUTES)
            minutes = WARM_UP_MINUTES
        self.advance(minutes)

    def advance(self, minutes, quiet=False):
        for _ in range(int(minutes)):
            self.now += timedelta(minutes=1)
            self._step_zones(quiet)
            for rider in list(self.riders.values()):
                self._step_rider(rider)

    def _step_zones(self, quiet):
        now = self.now
        if self.busy_place and now >= self.busy_place["until"]:
            self.set_busy_place(None)
        for zone in ZONES:
            zid, state = zone["id"], self.zones[zone["id"]]
            state["traffic_noise"] = max(
                -0.15, min(0.35, state["traffic_noise"] * 0.97 + self.rnd.gauss(0, 0.015))
            )
            demand = self.demand(zid)
            state["open_orders"] = [o for o in state["open_orders"] if o["expires"] > now]
            for _ in range(poisson(self.rnd, demand * VISIBLE_ORDERS_PER_MIN)):
                state["open_orders"].append(self._new_order(zid, demand))

            incentive = state["incentive"]
            if incentive and incentive["expires"] <= now:
                state["incentive"] = None
                self.log("incentive", f"Incentive ended in {zone['name']}", quiet)
            elif not incentive and self.rnd.random() < INCENTIVE_CHANCE_PER_MIN * demand:
                self.start_incentive(
                    zid,
                    bonus=self.rnd.choice([60, 80, 100, 120, 150]),
                    orders_needed=self.rnd.choice([2, 3, 4]),
                    minutes=self.rnd.randint(40, 90),
                    quiet=quiet,
                )

    def _new_order(self, zone_id, demand):
        self._order_seq += 1
        km = order_distance_km(self.rnd)
        platform, merchant = pick_partner(self.rnd, ZONE_BY_ID[zone_id]["name"])
        return {
            "id": f"{zone_id}-{self._order_seq}",
            "zone_id": zone_id,
            "platform": platform,
            "merchant": merchant,
            "payout": order_payout(self.rnd, km, demand),
            "distance_km": km,
            "eta_min": order_eta_min(self.rnd, km, self.traffic(zone_id)),
            "expires": self.now + timedelta(minutes=self.rnd.uniform(3, 7)),
        }

    # ------------------------------------------------------------ the riders

    def add_rider(self, user_id, shift, zone_id, earned=None, orders_done=0, resume=None):
        """`shift` is a row from the shifts table; started_at is a datetime. `resume` is
        what the rider was in the middle of before a restart (see rider_progress)."""
        self.riders[user_id] = {
            "user_id": user_id,
            "shift_id": shift["id"],
            "zone_id": zone_id,
            "in_area": True,  # False while the rider's GPS is outside the service area
            "vehicle": shift["vehicle"],
            "status": "idle",
            "busy_until": None,
            "heading_to": None,
            "order": None,
            "started_at": shift["started_at"],
            "base_hours": shift["base_hours"],
            "base_earned": shift["base_earned"],
            "available_hours": shift["available_hours"],
            "target_earnings": shift["target_earnings"],
            "earned": shift["base_earned"] if earned is None else earned,
            "orders_done": orders_done,
            "snoozed": {},
            "decision": None,  # the rider's answer to the current suggestion
            "last_event": None,
            "events": deque(maxlen=30),
            "saved": None,
        }
        rider = self.riders[user_id]
        if resume:
            rider["heading_to"] = resume.get("heading_to")
            if resume.get("order"):  # carry on with the order that was under way
                rider["status"], rider["order"] = "on_order", resume["order"]
                rider["busy_until"] = resume["busy_until"]
        self._step_rider(rider)
        return rider

    @staticmethod
    def rider_progress(rider):
        """The part of a rider's state worth keeping across a restart."""
        return {
            "heading_to": rider["heading_to"],
            "order": rider["order"],
            "busy_until": rider["busy_until"] if rider["order"] else None,
        }

    def remove_rider(self, user_id):
        self.riders.pop(user_id, None)

    def hours_elapsed(self, rider):
        worked = (self.now - rider["started_at"]).total_seconds() / 3600
        return min(rider["base_hours"] + max(worked, 0), rider["available_hours"])

    def set_zone(self, rider, zone_id):
        """Called when the rider's real position puts them in a (new) zone."""
        if zone_id != rider["zone_id"]:
            rider["zone_id"] = zone_id
            self.log_rider(rider, "move", f"Now in {ZONE_BY_ID[zone_id]['name']}")
        if rider["heading_to"] == zone_id:
            rider["heading_to"] = None
            if rider["status"] == "heading":
                rider["status"] = "idle"
            self.log_rider(rider, "move", f"Arrived in {ZONE_BY_ID[zone_id]['name']}")
        self._step_rider(rider)

    def head_to(self, rider, zone_id):
        rider["heading_to"] = None if zone_id == rider["zone_id"] else zone_id
        if rider["status"] == "heading" and not rider["heading_to"]:
            rider["status"] = "idle"
        self._step_rider(rider)

    def credit(self, rider, amount, zone_id, kind, **details):
        """`details`: note, platform, merchant, distance_km - whatever is known."""
        rider["earned"] += amount
        self.on_earning(rider, amount, zone_id, kind, **details)

    def _step_rider(self, rider):
        self._advance_rider(rider)
        progress = (rider["status"], rider["heading_to"], rider["order"] and rider["order"]["id"])
        if progress != rider["saved"]:
            rider["saved"] = progress
            self.on_rider_change(rider)

    def _advance_rider(self, rider):
        if rider["status"] == "shift_over":
            return
        now = self.now
        if rider["status"] == "on_order" and now >= rider["busy_until"]:
            self._complete_order(rider)

        if rider["status"] != "on_order" and (
            self.hours_elapsed(rider) >= rider["available_hours"]
        ):
            rider["status"] = "shift_over"
            self.log_rider(rider, "shift", f"Shift over - earned Rs {rider['earned']:.0f}")
            return

        if rider["status"] == "idle" and rider["heading_to"]:
            rider["status"] = "heading"  # on the way: no orders until they arrive
        elif rider["status"] == "idle" and rider["in_area"]:
            orders = self.zones[rider["zone_id"]]["open_orders"]
            if orders:
                order = max(orders, key=lambda o: o["payout"] / o["eta_min"])
                orders.remove(order)
                rider["status"], rider["order"] = "on_order", order
                rider["busy_until"] = now + timedelta(minutes=order["eta_min"])

    def _complete_order(self, rider):
        order = rider["order"]
        net = round(
            order["payout"]
            - fuel_cost(order["distance_km"], rider["vehicle"])
            - order["payout"] * 0.03,
            1,
        )
        rider["orders_done"] += 1
        rider["status"], rider["order"] = "idle", None
        zone_name = ZONE_BY_ID[order["zone_id"]]["name"]
        self.credit(rider, net, order["zone_id"], "order", platform=order["platform"],
                    merchant=order["merchant"], distance_km=order["distance_km"])  # fmt: skip
        self.log_rider(
            rider,
            "order",
            f"{order['platform']} order from {order['merchant']} delivered in {zone_name}: "
            f"+Rs {net:.0f} net",
        )

        incentive = self.zones[order["zone_id"]]["incentive"]
        if incentive:
            done = incentive["progress"].get(rider["user_id"], 0) + 1
            incentive["progress"][rider["user_id"]] = done
            if done == incentive["orders_needed"]:
                self.credit(rider, incentive["bonus"], order["zone_id"], "incentive",
                            note=incentive["description"], platform=order["platform"])  # fmt: skip
                self.log_rider(
                    rider, "incentive", f"Incentive earned in {zone_name}: +Rs {incentive['bonus']}"
                )

    # ----------------------------------------------------------------- views

    def zone_views(self, user_id=None):
        """Everything the agents need to know about each zone right now."""
        views = []
        for zone in ZONES:
            zid, state = zone["id"], self.zones[zone["id"]]
            weather = self.weather.at(zid, self.now)
            incentive = state["incentive"]
            if incentive:
                done = incentive["progress"].get(user_id, 0)
                incentive = done < incentive["orders_needed"] and {
                    **incentive,
                    "progress": done,
                    "minutes_left": (incentive["expires"] - self.now).total_seconds() / 60,
                }
            views.append(
                {
                    **zone,
                    "demand": self.demand(zid),
                    "traffic": self.traffic(zid),
                    "rain": self.rain(zid),
                    "temp": weather["temp"],
                    "open_orders": state["open_orders"],
                    "history": HISTORY["zone_avg"][zid],
                    "busy_place": (
                        self.busy_place["place"]["name"]
                        if self.busy_place and self.busy_place["place"]["zone_id"] == zid
                        else None
                    ),
                    "incentive": incentive or None,
                }
            )
        return views

    def demand_forecast(self, zone_id, hours=12):
        """Demand index for the next `hours`, using the weather forecast."""
        start = self.now.replace(minute=0, second=0, microsecond=0)
        points = []
        for i in range(hours):
            when = start + timedelta(hours=i)
            points.append({"label": when.strftime("%H:00"), "value": self.demand(zone_id, when)})
        return points
