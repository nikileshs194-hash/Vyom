"""
GigPilot - assistant command understanding.

Turns a typed or spoken instruction ("set my goal to 1500 in 6 hours",
"Charminar is busy", "accept", "how much have I earned") into an intent
with its details. It is rule-based on purpose: it needs no paid AI
service, answers instantly, and the same sentence always does the same
thing. main.py carries the intent out.
"""

import re
from difflib import SequenceMatcher

from data import PLACES, ZONES

HELP = (
    "You can say things like: set my goal to 1500 in 6 hours, accept, ignore, "
    "where should I go, how much have I earned, Charminar is busy, clear the busy place, "
    "directions to Kompally, show the map, what is my best hour, or end my shift."
)

SECTIONS = {
    "map": ("map", "city"),
    "activity": ("activity", "earnings", "boxes", "orders"),
    "busy": ("busy places", "places"),
    "history": ("history", "decisions"),
    "feed": ("feed", "events"),
    "zones": ("zones", "comparison", "compare"),
    "goal": ("goal", "target"),
    "recommendation": ("recommendation", "suggestion"),
}
VEHICLES = ("bike", "scooter", "car")
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}  # fmt: skip


def normalise(text):
    text = text.lower().replace(",", "")
    text = re.sub(r"(\d)\s*k\b", r"\g<1>000", text)  # "2k" -> "2000"
    text = re.sub(r"[^a-z0-9.' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _aliases():
    zone_names = {normalise(z["name"]): ("zone", z) for z in ZONES}
    place_names = {}
    for p in PLACES:
        for alias in (p["name"], p["name"].split(",")[0], p["id"].replace("-", " ")):
            if normalise(alias) not in zone_names:  # "Kompally" alone means the zone
                place_names.setdefault(normalise(alias), ("place", p))
    return zone_names, place_names


ZONE_ALIASES, PLACE_ALIASES = _aliases()


def _find(text, aliases):
    """Best alias mentioned in the text: exact phrase first, then a close
    spelling (speech recognition often garbles place names)."""
    for alias in sorted(aliases, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return aliases[alias][1]
    words = text.split()
    best, best_score = None, 0.8
    for alias, (_, item) in aliases.items():
        if len(alias) < 7:  # short names are too easy to confuse with ordinary words
            continue
        size = len(alias.split())
        for i in range(len(words) - size + 1):
            score = SequenceMatcher(None, alias, " ".join(words[i : i + size])).ratio()
            if score > best_score:
                best, best_score = item, score
    return best


def find_zone(text):
    return _find(text, ZONE_ALIASES)


def find_place(text):
    return _find(text, PLACE_ALIASES)


def _has(text, *patterns):
    return any(re.search(rf"\b(?:{p})\b", text) for p in patterns)


def _goal_details(text):
    details = {}
    for word, value in NUMBER_WORDS.items():
        text = re.sub(rf"\b{word}\b", str(value), text)
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", text)
    if hours:
        details["available_hours"] = float(hours.group(1))
        text = text.replace(hours.group(0), " ")
    amounts = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text) if float(n) >= 100]
    if amounts:
        details["target_earnings"] = amounts[0]
    vehicle = next((v for v in VEHICLES if _has(text, v)), None)
    if vehicle:
        details["vehicle"] = vehicle
    return details


def parse(text):
    """Returns {"intent": ..., plus whatever details that intent needs}."""
    t = normalise(text)
    if not t:
        return {"intent": "help"}
    zone, place = find_zone(t), find_place(t)

    if _has(t, "log ?out", "sign ?out", "log me out"):
        return {"intent": "logout"}
    if _has(t, "help", "what can you do", "what can i say", "commands"):
        return {"intent": "help"}
    if _has(t, "hi", "hello", "hey", "good (morning|afternoon|evening)") and len(t.split()) <= 3:
        return {"intent": "greeting"}

    # ---- busy place and demo events
    if _has(t, "busy", "rush", "crowd", "crowded", "surge", "hotspot", "packed"):
        if _has(t, "clear", "remove", "reset", "cancel", "no more", "stop", "not"):
            return {"intent": "clear_busy_place"}
        if not (place or zone) and _has(t, "show", "list", "which", "what", "see", "view"):
            return {"intent": "show", "section": "busy"}
        return {"intent": "set_busy_place", "place": place, "zone": zone}
    if _has(t, "clear", "reset", "remove") and _has(t, "events?", "demo", "conditions?"):
        return {"intent": "reset_events"}
    if zone and _has(t, "traffic", "jam", "congestion"):
        return {"intent": "traffic", "zone": zone}
    if zone and _has(t, "rain", "raining", "storm") and not _has(t, "is it", "weather"):
        return {"intent": "rain", "zone": zone}
    if zone and _has(t, "incentive", "bonus"):
        return {"intent": "incentive", "zone": zone}

    # ---- the shift
    if _has(t, "end", "stop", "finish", "close") and _has(t, "shift", "work", "day"):
        return {"intent": "end_shift"}
    if _has(t, "cancel", "undo") and not _has(t, "busy"):
        return {"intent": "cancel_move"}
    goal = _goal_details(t)
    if _has(t, "goal", "target", "shift", "earn", "make") and (
        "target_earnings" in goal or _has(t, "set", "start", "begin", "update", "change")
    ):
        return {"intent": "set_goal", **goal}
    if _has(t, "start", "begin") and not (zone or place):
        return {"intent": "set_goal", **goal}
    if goal and _has(t, "set", "update", "change", "switch"):
        return {"intent": "set_goal", **goal}

    # ---- answering the current suggestion
    if _has(t, "accept", "agree", "yes", "yeah", "okay", "ok", "sure", "go ahead", "do it",
            "let'?s go", "confirm"):  # fmt: skip
        return {"intent": "accept"}
    if _has(t, "ignore", "skip", "reject", "decline", "dismiss", "not now", "no"):
        return {"intent": "ignore"}

    # ---- moving around the page and the city
    if _has(t, "show", "scroll", "see", "view", "open") and not _has(t, "maps", "route"):
        for section, words in SECTIONS.items():
            if _has(t, *words):
                return {"intent": "show", "section": section}
    if _has(t, "navigate", "directions?", "route", "take me", "google maps", "maps", "go to",
            "drive to", "ride to", "how do i get"):  # fmt: skip
        return {"intent": "navigate", "place": place, "zone": zone}
    if zone and _has(t, "i am in", "i'm in", "im in", "my location is", "set (my )?location",
                     "i am at", "put me in"):  # fmt: skip
        return {"intent": "set_location", "zone": zone}
    if _has(t, "where am i", "my location", "current location"):
        return {"intent": "where_am_i"}

    # ---- questions
    if _has(t, "where should", "recommend\\w*", "suggest\\w*", "what should i do", "best zone",
            "where to go", "what next", "advice"):  # fmt: skip
        return {"intent": "recommendation"}
    if _has(t, "best hour", "peak", "when do i earn", "best time"):
        return {"intent": "best_hour"}
    if _has(t, "slowest", "worst hour", "worst time"):
        return {"intent": "slowest_hour"}
    if _has(t, "swiggy", "zomato", "zepto", "blinkit", "which app", "apps?"):
        return {"intent": "apps"}
    if _has(t, "week", "weekly", "7 days"):
        return {"intent": "week"}
    if _has(t, "time left", "hours left", "remaining", "how long"):
        return {"intent": "time_left"}
    if _has(t, "earn\\w*", "made", "income", "progress", "how much", "money", "goal", "target"):
        return {"intent": "earnings"}
    if _has(t, "weather", "rain\\w*", "temperature", "hot", "cold"):
        return {"intent": "weather"}
    if _has(t, "top zones?", "busiest zones?", "best zones?", "compare"):
        return {"intent": "top_zones"}
    if _has(t, "status", "what am i doing", "what's happening", "update me"):
        return {"intent": "status"}
    if place:
        return {"intent": "place_info", "place": place}
    if zone:
        return {"intent": "zone_info", "zone": zone}
    return {"intent": "unknown"}
