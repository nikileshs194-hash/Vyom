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
# ---- reading amounts the way people say and type them
UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}  # fmt: skip
TENS = {w: 10 * i for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}  # fmt: skip
SCALES = {"thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
          "lacs": 100_000, "crore": 10_000_000, "crores": 10_000_000}  # fmt: skip


def words_to_digits(text):
    """ "two thousand six hundred" -> "2600", "five" -> "5"; everything else is kept."""
    tokens, out, i = text.split(), [], 0
    while i < len(tokens):
        total = current = 0
        j, seen = i, False
        while j < len(tokens):
            word = tokens[j]
            follows_number = j + 1 < len(tokens) and (tokens[j + 1] in UNITS or tokens[j + 1] in TENS)
            if word in UNITS:
                current += UNITS[word]
            elif word in TENS:
                current += TENS[word]
            elif word == "hundred" and seen:
                current = max(current, 1) * 100
            elif word in SCALES and seen:
                total, current = total + max(current, 1) * SCALES[word], 0
            elif not (word == "and" and seen and follows_number):
                break
            seen = True
            j += 1
        if seen:
            out.append(str(total + current))
            i = j
        else:
            out.append(word)
            i += 1
    return " ".join(out)


def _scale(match):
    value = float(match.group(1)) * {"k": 1_000, "hundred": 100, **SCALES}[match.group(2)]
    return str(int(value)) if value.is_integer() else str(value)


def _join_split(match):
    """Speech-to-text often writes "two thousand six hundred" as "2000 600"."""
    big, small = int(match.group(1)), int(match.group(2))
    if big >= 1000 and big % 1000 == 0 and 100 <= small < 1000:
        return str(big + small)
    return match.group(0)


# Everyday Telugu and Kannada words for the commonest instructions, mapped to the English
# the rules below understand. This keeps the basics working without the AI agent; anything
# beyond these (and all free-form speech) needs the Gemini agent, which knows both languages.
# Longer phrases are listed first so they are matched before the words inside them.
OTHER_LANGUAGES = [
    # ---- Telugu
    ("ఎక్కడికి వెళ్ళాలి", "where should i go"), ("ఎక్కడికి వెళ్లాలి", "where should i go"),
    ("ఎక్కడ పని చేయాలి", "where should i go"), ("ఎంత సంపాదించాను", "how much have i earned"),
    ("ఎంత సమయం మిగిలింది", "time left"), ("నన్ను అక్కడికి తీసుకెళ్ళు", "take me there"),
    ("షిఫ్ట్ ముగించు", "end shift"), ("షిఫ్ట్ ఆపు", "end shift"),
    ("షిఫ్ట్ ప్రారంభించు", "start shift"), ("మొదలుపెట్టు", "start shift"),
    ("రద్దీగా ఉంది", "is busy"), ("బిజీగా ఉంది", "is busy"), ("రద్దీ", "busy"), ("బిజీ", "busy"),
    ("మ్యాప్ చూపించు", "show map"), ("దారి చూపించు", "directions"),
    ("అంగీకరించు", "accept"), ("ఒప్పుకో", "accept"), ("అవును", "yes"), ("సరే", "ok"),
    ("విస్మరించు", "ignore"), ("వద్దు", "ignore"), ("కాదు", "no"),
    ("రద్దు చేయి", "cancel"), ("రద్దు", "cancel"), ("లక్ష్యం", "goal"), ("టార్గెట్", "goal"),
    ("గంటల్లో", "hours"), ("గంటలలో", "hours"), ("గంటలు", "hours"), ("గంట", "hours"),
    ("రూపాయలు", ""), ("రూపాయిలు", ""), ("సంపాదన", "earnings"), ("వాతావరణం", "weather"),
    ("స్థితి", "status"), ("సహాయం", "help"), ("మ్యాప్", "map"), ("దారి", "directions"),
    ("స్కూటర్", "scooter"), ("బైక్", "bike"), ("కారు", "car"),
    # ---- Kannada
    ("ಎಲ್ಲಿಗೆ ಹೋಗಬೇಕು", "where should i go"), ("ಎಲ್ಲಿ ಕೆಲಸ ಮಾಡಬೇಕು", "where should i go"),
    ("ಎಷ್ಟು ಗಳಿಸಿದ್ದೇನೆ", "how much have i earned"), ("ಎಷ್ಟು ಸಮಯ ಉಳಿದಿದೆ", "time left"),
    ("ನನ್ನನ್ನು ಅಲ್ಲಿಗೆ ಕರೆದುಕೊಂಡು ಹೋಗು", "take me there"),
    ("ಶಿಫ್ಟ್ ಮುಗಿಸು", "end shift"), ("ಶಿಫ್ಟ್ ನಿಲ್ಲಿಸು", "end shift"),
    ("ಶಿಫ್ಟ್ ಪ್ರಾರಂಭಿಸು", "start shift"), ("ಶುರುಮಾಡು", "start shift"),
    ("ಬ್ಯುಸಿ ಇದೆ", "is busy"), ("ಜನಸಂದಣಿ", "busy"), ("ಬ್ಯುಸಿ", "busy"),
    ("ನಕ್ಷೆ ತೋರಿಸು", "show map"), ("ದಾರಿ ತೋರಿಸು", "directions"),
    ("ಒಪ್ಪಿಕೊಳ್ಳಿ", "accept"), ("ಒಪ್ಪಿಕೊ", "accept"), ("ಹೌದು", "yes"), ("ಸರಿ", "ok"),
    ("ನಿರ್ಲಕ್ಷಿಸು", "ignore"), ("ಬೇಡ", "ignore"), ("ಇಲ್ಲ", "no"),
    ("ರದ್ದುಮಾಡು", "cancel"), ("ರದ್ದು", "cancel"), ("ಗುರಿ", "goal"), ("ಟಾರ್ಗೆಟ್", "goal"),
    ("ಗಂಟೆಗಳಲ್ಲಿ", "hours"), ("ಗಂಟೆಗಳು", "hours"), ("ಗಂಟೆ", "hours"),
    ("ರೂಪಾಯಿಗಳು", ""), ("ರೂಪಾಯಿ", ""), ("ಗಳಿಕೆ", "earnings"), ("ಹವಾಮಾನ", "weather"),
    ("ಸ್ಥಿತಿ", "status"), ("ಸಹಾಯ", "help"), ("ನಕ್ಷೆ", "map"), ("ದಾರಿ", "directions"),
    ("ಸ್ಕೂಟರ್", "scooter"), ("ಬೈಕ್", "bike"), ("ಕಾರು", "car"),
]  # fmt: skip


def to_english(text):
    """Swap the Telugu and Kannada words we know for their English equivalents."""
    for phrase, english in OTHER_LANGUAGES:
        if phrase in text:
            text = text.replace(phrase, f" {english} ")
    return text


def normalise(text):
    """Lower-case, strip punctuation and turn every way of writing an amount into plain
    digits: "1,500", "Rs.1500", "2k", "1.5 lakh", "two thousand six hundred", "2000 600"."""
    text = re.sub(r"(?<=\d),(?=\d)", "", to_english(text).lower())  # 1,500 and 2,00,600
    text = re.sub(r"\b(?:rs|inr)\.?\s*(?=\d)|₹\s*", " ", text)
    text = re.sub(r"[^a-z0-9.' ]+", " ", text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)  # a full stop is not a decimal point
    text = words_to_digits(re.sub(r"\s+", " ", text).strip())
    text = re.sub(r"(\d+(?:\.\d+)?)\s*(k|hundred|thousand|lakhs?|lacs?|crores?)\b", _scale, text)
    text = re.sub(r"\b(\d+) (\d+)\b(?! ?(?:hours?|hrs?|h)\b)", _join_split, text)
    return text


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
    if "target_earnings" in goal and (
        "available_hours" in goal or _has(t, "want", "need", "aim", "make it")
    ):  # "fifteen hundred in six hours", "I want 2500"
        return {"intent": "set_goal", **goal}

    # ---- answering the current suggestion
    if _has(t, "accept", "agree", "yes", "yeah", "okay", "ok", "sure", "go ahead", "do it",
            "let'?s go", "confirm", "set it anyway", "i am sure", "i'm sure"):  # fmt: skip
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
    if _has(t, "plan", "will i (reach|make|hit)", "can i (reach|make|hit)", "rest of (my|the) shift",
            "schedule", "on track"):  # fmt: skip
        return {"intent": "plan"}
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
