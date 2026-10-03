"""
GigPilot - live data providers (all free, no account needed by default).

  Weather  Open-Meteo   current conditions + hourly forecast per zone
  Roads    OSRM         real road distance / drive time between zones
  Traffic  TomTom       optional: only used if TOMTOM_API_KEY is set

Every provider degrades to a built-in estimate if the network call fails,
so the demo keeps working offline. `status()` reports which one is in use.
"""

import json
import math
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

from data import IST, ZONES, _bump, hour_of

OFFLINE = os.environ.get("GIGPILOT_OFFLINE") == "1"
CACHE_DIR = Path(__file__).parent / "cache"
ROADS_CACHE = CACHE_DIR / "roads.json"

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OSRM_URL = "https://router.project-osrm.org"
TOMTOM_URL = (
    "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
)

WEATHER_CODES = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 80: "Light showers",
    81: "Showers", 82: "Heavy showers", 95: "Thunderstorm", 96: "Thunderstorm",
    99: "Thunderstorm",
}  # fmt: skip


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(h))


class Weather:
    def __init__(self):
        self.zones = {}  # zone_id -> {"current": {...}, "hourly": {iso hour: (temp, mm)}}
        self.fetched_at = None
        self.error = None

    def refresh(self):
        if OFFLINE:
            return
        try:
            res = httpx.get(
                OPEN_METEO_URL,
                params={
                    "latitude": ",".join(str(z["lat"]) for z in ZONES),
                    "longitude": ",".join(str(z["lon"]) for z in ZONES),
                    "current": "temperature_2m,precipitation,weather_code,wind_speed_10m",
                    "hourly": "temperature_2m,precipitation",
                    "forecast_days": 2,
                    "timezone": "Asia/Kolkata",
                },
                timeout=12,
            )
            res.raise_for_status()
            body = res.json()
            rows = body if isinstance(body, list) else [body]
            zones = {}
            for zone, row in zip(ZONES, rows):
                cur, hourly = row["current"], row["hourly"]
                zones[zone["id"]] = {
                    "current": {
                        "temp": cur["temperature_2m"],
                        # reported per 15 minutes; convert to mm per hour
                        "rain": round(cur["precipitation"] * 4, 1),
                        "code": cur["weather_code"],
                        "wind": cur["wind_speed_10m"],
                    },
                    "hourly": {
                        t: (temp, mm)
                        for t, temp, mm in zip(
                            hourly["time"], hourly["temperature_2m"], hourly["precipitation"]
                        )
                    },
                }
            self.zones = zones
            self.fetched_at = datetime.now(IST)
            self.error = None
        except Exception as exc:  # network down, API change: keep last good data
            self.error = str(exc)[:120]

    def at(self, zone_id, when):
        """Weather for a zone at a (possibly simulated, future) time."""
        data = self.zones.get(zone_id)
        if data:
            if abs((when - datetime.now(IST)).total_seconds()) < 45 * 60:
                return {**data["current"], "live": True}
            hit = data["hourly"].get(when.strftime("%Y-%m-%dT%H:00"))
            if hit:
                return {"temp": hit[0], "rain": hit[1], "code": 61 if hit[1] > 0 else 1,
                        "wind": data["current"]["wind"], "live": True}  # fmt: skip
        # no data: a plain dry day with a mid-afternoon temperature peak
        temp = round(21 + 7 * _bump(hour_of(when), 14.5, 4.0), 1)
        return {"temp": temp, "rain": 0.0, "code": 1, "wind": 6.0, "live": False}

    def status(self):
        if self.zones:
            return {
                "mode": "live",
                "source": "Open-Meteo",
                "detail": "updated " + self.fetched_at.strftime("%H:%M"),
            }
        return {
            "mode": "simulated",
            "source": "built-in estimate",
            "detail": self.error or "offline",
        }


class Roads:
    """Zone-to-zone road distance and free-flow drive time."""

    def __init__(self):
        self.ids = [z["id"] for z in ZONES]
        self.index = {zid: i for i, zid in enumerate(self.ids)}
        self.source = "estimated"
        self.km = [[haversine_km(a, b) * 1.35 for b in ZONES] for a in ZONES]
        self.minutes = [[km / 24 * 60 for km in row] for row in self.km]
        self.routes = {}
        self._load_cache()

    def _load_cache(self):
        try:
            cached = json.loads(ROADS_CACHE.read_text())
            if cached["ids"] == self.ids:
                self.km, self.minutes = cached["km"], cached["minutes"]
                self.source = "osrm"
        except (OSError, ValueError, KeyError):
            pass

    def refresh(self):
        """One table request for all zone pairs; cached on disk afterwards."""
        if OFFLINE or self.source == "osrm":
            return
        try:
            coords = ";".join(f"{z['lon']},{z['lat']}" for z in ZONES)
            res = httpx.get(
                f"{OSRM_URL}/table/v1/driving/{coords}",
                params={"annotations": "duration,distance"},
                timeout=25,
            )
            res.raise_for_status()
            body = res.json()
            self.km = [[round(m / 1000, 2) for m in row] for row in body["distances"]]
            self.minutes = [[round(s / 60, 1) for s in row] for row in body["durations"]]
            self.source = "osrm"
            CACHE_DIR.mkdir(exist_ok=True)
            ROADS_CACHE.write_text(
                json.dumps({"ids": self.ids, "km": self.km, "minutes": self.minutes})
            )
        except Exception:
            pass  # keep the straight-line estimate

    def distance_km(self, a, b):
        return self.km[self.index[a]][self.index[b]]

    def drive_minutes(self, a, b):
        return self.minutes[self.index[a]][self.index[b]]

    def route(self, a, b):
        """Road geometry as [[lat, lon], ...] for drawing on the map."""
        if (a, b) in self.routes:
            return self.routes[(a, b)]
        za, zb = ZONES[self.index[a]], ZONES[self.index[b]]
        line, source = [[za["lat"], za["lon"]], [zb["lat"], zb["lon"]]], "straight line"
        if not OFFLINE:
            try:
                res = httpx.get(
                    f"{OSRM_URL}/route/v1/driving/"
                    f"{za['lon']},{za['lat']};{zb['lon']},{zb['lat']}",
                    params={"overview": "simplified", "geometries": "geojson"},
                    timeout=6,
                )
                res.raise_for_status()
                coords = res.json()["routes"][0]["geometry"]["coordinates"]
                line, source = [[lat, lon] for lon, lat in coords], "osrm"
            except Exception:
                pass
        result = {"line": line, "source": source}
        if source == "osrm" or OFFLINE:
            self.routes[(a, b)] = result
        return result

    def route_from(self, lat, lon, b):
        """Road geometry from an exact position (the rider's GPS fix) to zone b."""
        key = (round(lat, 3), round(lon, 3), b)
        if key in self.routes:
            return self.routes[key]
        zb = ZONES[self.index[b]]
        line, source = [[lat, lon], [zb["lat"], zb["lon"]]], "straight line"
        if not OFFLINE:
            try:
                res = httpx.get(
                    f"{OSRM_URL}/route/v1/driving/{lon},{lat};{zb['lon']},{zb['lat']}",
                    params={"overview": "simplified", "geometries": "geojson"},
                    timeout=6,
                )
                res.raise_for_status()
                coords = res.json()["routes"][0]["geometry"]["coordinates"]
                line, source = [[la, lo] for lo, la in coords], "osrm"
            except Exception:
                pass
        result = {"line": line, "source": source}
        if source == "osrm":
            if len(self.routes) > 500:
                self.routes.clear()
            self.routes[key] = result
        return result

    def status(self):
        if self.source == "osrm":
            return {"mode": "live", "source": "OSRM / OpenStreetMap",
                    "detail": "real road distances"}  # fmt: skip
        return {"mode": "simulated", "source": "straight-line estimate",
                "detail": "OSRM unreachable"}  # fmt: skip


class Traffic:
    """Optional live congestion from TomTom's free tier (needs TOMTOM_API_KEY).
    Without a key, traffic comes from the rush-hour model in the simulator."""

    REFRESH_SECONDS = 30 * 60  # 32 zones x 48 refreshes/day stays inside 2,500 free calls

    def __init__(self):
        self.key = os.environ.get("TOMTOM_API_KEY")
        self.factors = {}
        self.fetched_at = 0.0
        self.error = None

    def refresh(self):
        if OFFLINE or not self.key or time.time() - self.fetched_at < self.REFRESH_SECONDS:
            return
        self.fetched_at = time.time()
        factors = {}
        try:
            with httpx.Client(timeout=8) as client:
                for z in ZONES:
                    res = client.get(
                        TOMTOM_URL, params={"point": f"{z['lat']},{z['lon']}", "key": self.key}
                    )
                    res.raise_for_status()
                    flow = res.json()["flowSegmentData"]
                    ratio = flow["freeFlowSpeed"] / max(flow["currentSpeed"], 1)
                    factors[z["id"]] = round(min(max(ratio, 1.0), 2.5), 2)
            self.factors, self.error = factors, None
        except Exception as exc:
            self.error = str(exc)[:120]

    def factor(self, zone_id):
        return self.factors.get(zone_id)

    def status(self):
        if self.factors:
            return {"mode": "live", "source": "TomTom", "detail": "live road speeds"}
        detail = self.error or "set TOMTOM_API_KEY for live road speeds"
        return {"mode": "simulated", "source": "rush-hour model", "detail": detail}
