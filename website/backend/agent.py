"""
GigPilot - AI agent (Google Gemini, free tier).

Where assistant.py matches an instruction against rules, this hands the
instruction to a language model along with a set of tools - the same
actions the page offers - and lets the model decide which to call, in
what order, until the request is done. It can chain steps ("set my goal,
then mark Charminar busy and take me there"), ask follow-up questions and
remember the conversation.

Needs GEMINI_API_KEY (environment variable, or a line in a `.env` file
next to this module). Without a key, or if a call fails, main.py falls
back to the rule-based assistant.

Uses the Gemini Interactions API in stateless mode (`store: false`), so
Google does not keep the conversation; the history lives in this process.
"""

import json
import os
import re
import time
from pathlib import Path

import httpx

from data import CITY, PLACES, ZONES

API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
# Tried in this order. The free tier allows only a few requests a minute per model, so
# when one is rate limited the next takes over until it is available again.
DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
MAX_STEPS = 8  # model turns per instruction, so a confused model cannot loop forever
HISTORY_ITEMS = 40
TIMEOUT_SECONDS = 40
ENV_FILE = Path(__file__).parent / ".env"

LANGUAGES = {"en": "English", "te": "Telugu", "kn": "Kannada"}

SECTIONS = ["map", "activity", "busy", "history", "feed", "zones", "goal", "recommendation"]


def _function(name, description, properties=None, required=()):
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
        },
    }


TOOLS = [
    _function(
        "get_status",
        "Everything about the rider right now: shift and goal progress, the current "
        "recommendation and whether it was answered, location, busy place, weather. "
        "Call this first whenever you need facts you do not already have.",
    ),
    _function(
        "set_goal",
        "Start a shift or change the current goal. Omitted values keep their current "
        "setting (or sensible defaults when no shift is running).",
        {
            "target_earnings": {"type": "number", "description": "Rupees to earn in the shift"},
            "available_hours": {
                "type": "number",
                "description": "Hours from now the rider will keep working (not hours since "
                "the shift began)",
            },
            "vehicle": {"type": "string", "enum": ["bike", "scooter", "car"]},
            "confirmed": {
                "type": "boolean",
                "description": "True ONLY when the rider has just explicitly confirmed an "
                "amount that GigPilot reported as unrealistic. Never true on a first attempt.",
            },
        },
    ),
    _function(
        "answer_recommendation",
        "Accept or ignore GigPilot's current recommendation on the rider's behalf.",
        {"decision": {"type": "string", "enum": ["accept", "ignore"]}},
        ["decision"],
    ),
    _function("cancel_move", "Cancel a move the rider accepted but has not completed."),
    _function("end_shift", "End the rider's shift. Only when the rider clearly asks."),
    _function(
        "set_busy_place",
        "Mark a known busy place as the city's busy spot right now, creating an order surge "
        "there. Replaces any other busy place.",
        {"place": {"type": "string", "description": "Name of a busy place or its zone"}},
        ["place"],
    ),
    _function("clear_busy_place", "Remove the current busy place."),
    _function(
        "list_busy_places",
        "The busy places dataset with how busy each is at this hour, busiest first.",
        {"limit": {"type": "integer", "description": "How many to return (default 8)"}},
    ),
    _function(
        "zone_info",
        "Demand, expected earnings per hour, ride time and open orders for one zone.",
        {"zone": {"type": "string"}},
        ["zone"],
    ),
    _function(
        "top_zones",
        "The best zones right now by expected earnings per hour.",
        {"count": {"type": "integer", "description": "How many to return (default 5)"}},
    ),
    _function(
        "earnings_summary",
        "The rider's tracked earnings: today, last 7 days, best and slowest hour, "
        "by delivery app and by zone.",
    ),
    _function(
        "recent_orders",
        "The rider's latest orders with app, merchant, zone, time and amount.",
        {"limit": {"type": "integer", "description": "How many to return (default 5)"}},
    ),
    _function(
        "open_directions",
        "Open Google Maps directions on the rider's screen. With no destination, goes to "
        "the currently recommended place.",
        {"destination": {"type": "string", "description": "A zone or busy place name"}},
    ),
    _function(
        "show_section",
        "Scroll the page to a section so the rider can see it.",
        {"section": {"type": "string", "enum": SECTIONS}},
        ["section"],
    ),
    _function(
        "simulate_event",
        "Demo only: inject a made-up traffic spike, heavy rain or incentive in a zone, "
        "or reset all demo events.",
        {
            "kind": {"type": "string", "enum": ["traffic", "rain", "incentive", "reset"]},
            "zone": {"type": "string", "description": "Required unless kind is reset"},
        },
        ["kind"],
    ),
    _function(
        "set_location",
        "Set the rider's location by hand to a zone (only if they say their GPS is wrong "
        "or unavailable).",
        {"zone": {"type": "string"}},
        ["zone"],
    ),
    _function("log_out", "Log the rider out. Only when the rider clearly asks."),
]

SYSTEM = f"""You are the GigPilot agent, working for one delivery rider in {CITY}, India.
GigPilot helps the rider decide where to work to reach their earnings goal.

How to work:
- You act through the tools. Never state a number, place or status from memory or guesswork:
  get it from a tool or from the status snapshot in this conversation.
- Each rider message ends with a snapshot of GigPilot's current status. Use it instead of
  calling get_status, unless you have changed something since and need fresh values.
- Be economical with turns: when several tool calls do not depend on each other's results,
  request them together in the same turn.
- Carry out the rider's whole request, calling as many tools as it takes, in a sensible
  order. Check the result of each step before the next. If a tool reports a problem, say so
  plainly and suggest what the rider can do.
- If the request is unclear or missing something you cannot look up, ask one short question.
- Do exactly what was asked, with exactly the values given. Do not round, adjust or
  "improve" a number, and do not add steps the rider did not ask for. After changing
  something, state the exact values that are now set, as returned by the tool.
- Amounts may be spoken or typed loosely. Read them as a person would: "two thousand six
  hundred", "2600", "2,600", "2.6k" and "2000 600" (a speech-to-text split) all mean 2600;
  "1.5 lakh" is 150000; "fifteen hundred" is 1500. Hours ("in 6 hours", "for the next hour")
  are counted from now. If a number could be read two ways, ask which was meant.
- If set_goal reports that the amount looks unrealistic, nothing has changed. Tell the rider
  the pace it would need, and ask whether they meant it or a different amount. Call set_goal
  again with confirmed true only if they then say yes.
- If a tool returns an error or "changed": false, the action did NOT happen. Never say it did.
- The rider stays in control: accept or ignore a recommendation, end the shift or log out
  only when they have asked for it.
- Language: riders speak English, Telugu or Kannada, often mixed, sometimes in Roman
  letters. Understand all of them. Each rider message names the language to reply in:
  always answer in that language, in its own script (Telugu script for Telugu, Kannada
  script for Kannada), in simple everyday words a delivery rider would use.
  Tool arguments are never translated: pass zone and place names exactly as they appear in
  the lists below, in English. In replies, keep zone and place names recognisable and write
  amounts with digits, as "Rs 1,200".
- Replies are read aloud while the rider is on the road: one to three short sentences, plain
  words, no lists, no markdown. Amounts are in rupees, written "Rs 1,200".
- Orders, payouts and busy scores are demo data standing in for delivery-platform data. Do
  not claim a live connection to Swiggy, Zomato, Zepto or Blinkit.
- Stay on GigPilot topics. For anything else, say briefly that you can only help with the
  rider's shift.

Examples of turning an instruction into tool calls:
- "make it 2600 for the next 5 hours" -> set_goal(target_earnings=2600, available_hours=5)
- "raise my target to three thousand" -> set_goal(target_earnings=3000)   (hours untouched)
- "I'm on the scooter today" -> set_goal(vehicle="scooter")   (nothing else touched)
- "charminar is packed, take me there" -> set_busy_place(place="Charminar"), then
  answer_recommendation(decision="accept") and open_directions() together
- "skip that, what else?" -> answer_recommendation(decision="ignore"), then report the new
  suggestion from the tool result
- "how am I doing?" -> answer from the status snapshot; no tool needed
- "what's the best area right now?" -> top_zones()

Zones: {", ".join(z["name"] for z in ZONES)}.
Busy places: {", ".join(p["name"] for p in PLACES)}."""


class AgentError(Exception):
    """The model could not be reached or answered in an unexpected shape."""


def _read_env_file():
    values = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.strip().startswith("#"):
                values[name.strip()] = value.strip().strip("\"'")
    except OSError:
        pass
    return values


class Agent:
    def __init__(self):
        env = _read_env_file()
        self.key = os.environ.get("GEMINI_API_KEY") or env.get("GEMINI_API_KEY")
        model = os.environ.get("GEMINI_MODEL") or env.get("GEMINI_MODEL")
        self.models = [model] if model else list(DEFAULT_MODELS)
        self.histories = {}  # user_id -> conversation so far, as Interactions input items
        self.cooling = {}  # model -> time until which it is rate limited
        self.last_error = None

    def enabled(self):
        return bool(self.key)

    def reset(self, user_id):
        self.histories.pop(user_id, None)

    # ---- talking to the model

    def call(self, items):
        """One model turn. Returns the interaction JSON."""
        problem = None
        for model in self.models:
            if self.cooling.get(model, 0) > time.time():
                problem = problem or f"{model} is rate limited for a moment"
                continue
            try:
                res = httpx.post(
                    API_URL,
                    headers={"x-goog-api-key": self.key},
                    json={
                        "model": model,
                        "store": False,
                        "system_instruction": SYSTEM,
                        "input": items,
                        "tools": TOOLS,
                    },
                    timeout=TIMEOUT_SECONDS,
                )
            except httpx.HTTPError as exc:
                raise AgentError(f"could not reach Gemini ({type(exc).__name__})") from exc
            if res.status_code == 200:
                return res.json()
            try:
                body = res.json()
                body = body[0] if isinstance(body, list) else body  # errors can come wrapped
                message = body["error"]["message"]
            except (ValueError, KeyError, TypeError, IndexError):
                message = res.text[:200]
            problem = f"Gemini returned {res.status_code} for {model}: {message[:160]}"
            if res.status_code == 429:  # rate limited: rest this model, try the next
                wait = re.search(r"retry in (\d+)", message)
                self.cooling[model] = time.time() + (int(wait.group(1)) + 2 if wait else 60)
            elif res.status_code >= 500:  # overloaded or down: rest it briefly, try the next
                self.cooling[model] = time.time() + 30
            elif res.status_code not in (400, 404):  # a bad key will not work on any model
                break
        raise AgentError(problem)

    # ---- the agent loop

    @staticmethod
    def _text(steps):
        """The model's words: the text parts of its output steps."""
        parts = [
            part.get("text", "")
            for step in steps
            if step.get("type") == "model_output"
            for part in step.get("content") or []
            if part.get("type") == "text"
        ]
        return " ".join(p.strip() for p in parts if p.strip())

    def run(self, user, text, execute, status=None, lang="en"):
        """Carry out one instruction. `execute(user, tool_name, arguments)` runs a tool and
        returns (result dict for the model, list of page actions). `status` is a snapshot of
        the rider's situation, sent along so the model need not spend a turn asking for it.
        Returns (reply, actions, trace) - the trace lists each tool used, in order, so the
        page can show the agent's steps on screen."""
        uid = user["id"]
        items = list(self.histories.get(uid, []))
        asked = {"type": "user_input", "content": text}
        notes = [f"[Reply in {LANGUAGES.get(lang, 'English')}.]"]
        if status is not None:
            notes.append(f"[GigPilot status right now: {json.dumps(status, default=str)}]")
        asked["content"] = text + "\n\n" + "\n".join(notes)
        items.append(asked)
        actions, trace, reply = [], [], None

        for _ in range(MAX_STEPS):
            interaction = self.call(items)
            steps = interaction.get("steps")
            if not isinstance(steps, list):
                raise AgentError("Gemini answered in an unexpected format")
            items.extend(steps)  # passed back unchanged: it carries the model's own state
            calls = [s for s in steps if s.get("type") == "function_call"]
            if not calls:
                reply = self._text(steps) or (interaction.get("output_text") or "").strip()
                break
            for call in calls:
                try:
                    result, page_actions = execute(user, call["name"], call.get("arguments") or {})
                except Exception as exc:  # a tool bug must not end the conversation
                    result, page_actions = {"error": f"{type(exc).__name__}: {exc}"}, []
                actions.extend(page_actions)
                trace.append({"tool": call["name"], "args": call.get("arguments") or {},
                              "ok": "error" not in result})  # fmt: skip
                items.append(
                    {
                        "type": "function_result",
                        "name": call["name"],
                        "call_id": call.get("id"),
                        "result": [{"type": "text", "text": json.dumps(result, default=str)}],
                    }
                )
        if not reply:
            reply = "I did part of that but could not finish. Please tell me the next step."

        asked["content"] = text  # the snapshot goes stale; keep only the rider's words
        self.histories[uid] = self._trim(items)
        self.last_error = None
        return reply, actions, trace

    @staticmethod
    def _trim(items):
        """Keep recent turns only, always starting at a rider message so a tool call is
        never separated from its result."""
        starts = [i for i, item in enumerate(items) if item.get("type") == "user_input"]
        for start in starts:
            if len(items) - start <= HISTORY_ITEMS:
                return items[start:]
        return items[starts[-1] :] if starts else []
