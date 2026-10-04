# GigPilot

**Team Vyom · AI HACK X MRDU 2026 · Agentic AI track**
**Problem statement: AI Gig Worker Earnings Optimization Assistant**

GigPilot is a goal-driven assistant for delivery riders in Hyderabad. A rider
logs in, says how much they want to earn and how long they will work, and
GigPilot follows their real location, watches demand, traffic, weather and
incentives across 32 zones, recommends where to be right now, explains why,
and replans as conditions change. An AI agent carries out typed or spoken
instructions on the page, in English, Telugu or Kannada. The rider accepts or
ignores every recommendation; nothing is done on their behalf without asking.

---

## Contents

1. [What it does](#what-it-does)
2. [Quick start](#quick-start)
3. [Turning on the AI agent](#turning-on-the-ai-agent)
4. [Using the site](#using-the-site)
5. [How it works](#how-it-works)
6. [What is real and what is generated](#what-is-real-and-what-is-generated)
7. [Project layout](#project-layout)
8. [API](#api)
9. [Data and privacy](#data-and-privacy)
10. [Tests](#tests)
11. [Configuration](#configuration)
12. [Backup version](#backup-version)
13. [Known limits](#known-limits)

---

## What it does

- **Recommends where to work.** "Stay in Medchal" or "Move to Kompally", with
  the expected earnings per hour, the ride time, a confidence score and a
  step-by-step decision trace.
- **Tracks your real position.** The map shows where you are, your trail for
  the day and every zone shaded by demand. Clicking a zone opens Google Maps
  directions to it.
- **Keeps the rider in control.** Accept or Ignore each suggestion. Once
  answered, GigPilot does not ask again until its suggestion changes.
- **Has an agent that works the screen.** Type or say an instruction and a
  pointer travels to the right field, types, presses and scrolls while a
  banner names each step. It handles several requests in one sentence and
  remembers the conversation.
- **Speaks three languages.** The agent listens, replies and narrates its
  steps in English, Telugu or Kannada.
- **Understands busy places.** Mark one of 95 well-known Hyderabad spots as
  busy and the recommendation moves to it.
- **Tracks earnings automatically.** Every order is recorded with its
  delivery app, merchant, zone and distance. Activity boxes show earnings per
  day for 26 weeks and per hour for the last 7 days.
- **Keeps each rider's data separate.** Individual accounts, with goals,
  earnings, decisions and location trail stored per user.

---

## Quick start

You need **Python 3.10 or newer** and an internet connection (for the map,
weather and road data).

**Windows:** double-click `start_website.bat`. It installs what is needed,
starts the server in its own window and opens the site.

**Mac / Linux / Git Bash:**

```
bash start_website.sh
```

**By hand:**

```
cd website/backend
pip install fastapi uvicorn httpx
python -m uvicorn main:app --port 8000
```

Then open **http://localhost:8000/**, choose **Create account**, and allow the
browser to share your location.

The server takes a few seconds on its first start of the day, while it builds
the market order history. After that it loads from a saved copy.

---

## Turning on the AI agent

Without any setup the agent runs in **Basic mode**: built-in rules that
understand a fixed set of instructions, one at a time.

With a free Google Gemini key it becomes a real AI agent:

1. Get a free key at https://aistudio.google.com/apikey
2. Copy `website/backend/.env.example` to `website/backend/.env`
3. Put the key after `GEMINI_API_KEY=` (no spaces, no quotes) and save
4. Restart the server

The agent bar then shows **AI · Gemini**. The `.env` file is excluded from
git, so the key is never uploaded.

The free tier allows only a few requests a minute per model. GigPilot keeps
each instruction to about two requests, switches to a backup model when one
is rate-limited, and falls back to Basic mode (saying so) if none is
available.

---

## Using the site

1. **Create an account and log in.** Allow location when asked. If the browser
   cannot share one, pick your zone by hand.
2. **Set a goal.** Enter the target, hours and vehicle, then **Start shift**.
   Orders from the feed are assigned in the zone you are actually in.
3. **Follow or ignore the recommendation.** If you accept a move, orders pause
   until your position reaches that zone. **Cancel move** undoes it.
4. **Change the goal any time.** The shift carries on; earnings, orders and
   pace are kept. Hours you give count from now.
5. **Use the agent bar** at the bottom. Type, or press the microphone and
   speak. Choose the language with the selector beside it.
6. **Set a busy place** to see the recommendation follow a rush.
7. **Demo tools** at the bottom of the page inject a made-up traffic spike,
   incentive or rain burst so you can watch the agents replan.

### Things to say to the agent

| To do this | Say or type |
|---|---|
| Start or change the goal | `Set my goal to 2600 in 7 hours on a bike` |
| Change only the amount | `Change my target to 3000` |
| Ask for advice | `Where should I go?` |
| Answer the suggestion | `Accept` / `Ignore` / `Cancel the move` |
| Mark a rush | `Charminar is busy, take me there` |
| Get directions | `Directions to Kompally` |
| Check progress | `How much have I earned?` / `How am I doing?` |
| Plan ahead | `Will I reach my goal today?` |
| Review earnings | `What is my best hour?` / `Which app pays me most?` |
| Finish | `End my shift` / `Log out` |

Amounts are read however they are said or typed: "2600", "2,600", "2.6k",
"two thousand six hundred", "1.5 lakh". A goal that would need an impossible
pace (more than about Rs 600 an hour) is questioned rather than set.

In Telugu or Kannada, for example: `నా లక్ష్యం 1500, 6 గంటల్లో` or
`ಎಲ್ಲಿಗೆ ಹೋಗಬೇಕು?`.

---

## How it works

### The six agents

The recommendation comes from five specialist agents coordinated by a master
agent (`website/backend/agents.py`), not from a single black-box model.

| Agent | What it does |
|---|---|
| **Opportunity** | Finds the orders open in a zone and ranks them by net earning per minute |
| **Demand** | Reads how busy each zone is from order history, weather and traffic |
| **Earnings** | Tracks progress toward the goal and the pace still needed |
| **Optimization** | Scores every zone on expected net Rs/hour after the travel time and fuel to reach it |
| **Planning** | Turns the winning zone into a recommendation, a reason and a decision trace; can also plan the rest of the shift hour by hour |
| **Master** | Orchestrates the others and returns the final answer |

All the arithmetic (fuel cost, net earnings, zone scores) is done by plain,
deterministic Python functions. The agents reason over and explain those
numbers; they never invent them. The same conditions always produce the same
advice, and every recommendation can be traced to its inputs.

A move is recommended only when it beats staying put by at least 8% after the
ride, which stops the advice flipping back and forth.

### The AI agent

`website/backend/agent.py` gives a Gemini model 18 tools, the same actions the
page offers (set goal, answer the recommendation, mark a busy place, open
directions, look up earnings, plan the shift, and so on). The model decides
which to call and in what order, checks each result, and replies in one to
three short sentences. Rules it is given:

- every fact must come from a tool or the status snapshot, never from memory;
- do exactly what was asked, with exactly the values given;
- if a tool reports that a change did not happen, never say that it did;
- reply in the rider's chosen language.

Each tool call is returned to the page as a step, which the page acts out on
screen. The actions themselves run on the server; the pointer shows what was
done, in order.

`website/backend/assistant.py` is the rule-based fallback used when there is
no key or Gemini cannot be reached.

### The live city

`website/backend/world.py` keeps the city in step with the real clock: open
orders per zone, traffic, incentives, any busy place, and one rider per
logged-in user with a shift running. An order under way is saved with the
shift, so it carries on if the server restarts.

---

## What is real and what is generated

| Data | Source | Status |
|---|---|---|
| Your location and trail | Browser location service | **Real** |
| Place name of your position | [Nominatim](https://nominatim.org) on OpenStreetMap data | **Real** |
| Time | The real clock, in IST | **Real** |
| Weather, now and hourly forecast | [Open-Meteo](https://open-meteo.com) | **Real** |
| Road distance, drive time, route line | [OSRM](https://project-osrm.org) on OpenStreetMap data | **Real** |
| Map and navigation | OpenStreetMap tiles; Google Maps links | **Real** |
| Accounts and stored history | SQLite database | **Real** |
| Road congestion | Rush-hour and rain model; live with a TomTom key | Estimated |
| Orders, payouts, incentives, demand history | Generated | Stand-in |
| Busy-place scores | Editorial estimates | Stand-in |

Delivery platforms such as Swiggy and Zomato do not publish their order or
demand data, so it is generated from one consistent demand model:

- about **296,000 market orders** over 60 days across 32 zones;
- about **26 weeks of shifts and 2,000 orders per rider**, synced into an
  account the first time GigPilot learns which zone the rider works in;
- six delivery apps (Swiggy, Zomato, Zepto, Blinkit, Swiggy Instamart,
  BigBasket), 114 restaurants and 95 busy places.

Pay rates in the model (Rs 28 base plus Rs 11.5 per km, with surge) are
assumptions, not any partner's actual pay structure. Merchant names are real
brands used for realism; their figures here are invented.

The agents read a snapshot of the city and do not depend on where it came
from, so a real partner feed could replace the generated one without
rewriting the logic.

All free sources above need no API key. If one cannot be reached, GigPilot
falls back to a built-in estimate and keeps running.

---

## Project layout

```
GigPilot_Submission/
├── website/                     THE MAIN SUBMISSION
│   ├── backend/
│   │   ├── main.py              FastAPI app: every endpoint, and serves the site
│   │   ├── agents.py            the six recommendation agents
│   │   ├── agent.py             AI agent: Gemini with 18 tools
│   │   ├── assistant.py         rule-based fallback; reads amounts and languages
│   │   ├── world.py             live city state: orders, traffic, riders' shifts
│   │   ├── live.py              weather, roads, place names, optional live traffic
│   │   ├── data.py              zones, demand model, generated market history
│   │   ├── partners.py          delivery apps, merchants, each rider's past shifts
│   │   ├── places.py            busy places dataset for Hyderabad
│   │   ├── db.py                SQLite: accounts, shifts, earnings, locations, log
│   │   ├── .env.example         template for the Gemini key
│   │   └── cache/roads.json     saved road distances between zones
│   └── frontend/
│       ├── index.html           page structure
│       ├── style.css            styling (one blue on neutrals)
│       └── app.js               map, polling, activity boxes, on-screen agent
├── tests/
│   ├── test_api.py              API, agents, assistant, data separation
│   └── test_streamlit_backup.py backup app click-through
├── backup_streamlit/            simple offline fallback (see below)
├── start_website.bat / .sh      one-step launchers
├── requirements.txt             every Python package used
└── README.md
```

Created at run time and excluded from git: `website/backend/gigpilot.db` (the
database), `website/backend/.env` (your key) and
`website/backend/cache/history_*.json` (the day's market history).

---

## API

Every endpoint except register, login and the zone list needs
`Authorization: Bearer <token>` and only returns that user's data.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/register`, `/api/login`, `/api/logout` | Accounts |
| GET | `/api/me` | Current user |
| GET | `/api/zones` | Zone list with coordinates |
| GET | `/api/state` | Everything for the dashboard: city, position, shift, recommendation |
| POST | `/api/goal` | Start a shift, or change the goal of a running one |
| POST | `/api/shift/end` | End the shift |
| POST | `/api/accept`, `/api/ignore`, `/api/cancel-move` | Answer the recommendation |
| POST | `/api/location`, `/api/location/zone` | Report a GPS position, or set a zone by hand |
| GET | `/api/route` | Road route from the rider to a zone |
| GET | `/api/activity` | Earnings by day, by hour, by zone and by app |
| GET | `/api/history` | Accepted and ignored recommendations |
| GET | `/api/places` | Busy places with how busy each is now |
| POST | `/api/busy-place` | Set, change or clear the busy place |
| POST | `/api/assistant` | One typed or spoken instruction (`text`, `lang`) |
| GET, POST | `/api/assistant/status`, `/api/assistant/reset` | Agent engine; start a new chat |
| POST | `/api/simulate/traffic`, `/rain`, `/incentive`, `/reset` | Demo events |

Interactive documentation is at http://localhost:8000/docs while the server
is running.

---

## Data and privacy

- **Passwords** are stored only as salted scrypt hashes. Login tokens are
  stored hashed.
- **Each account's data is separate:** shifts, earnings, decisions, location
  trail and the log of agent instructions.
- **Location** is used to place the rider in a zone and draw the trail. A
  vague network-based fix does not move a rider whose position is already
  known.
- **Sent to outside services:** zone coordinates to Open-Meteo and OSRM; the
  rider's position, rounded to about 100 m, to Nominatim for the place name.
- **With the AI agent on:** each instruction and the rider data it looks up
  (place name, earnings, recommendation) is sent to Google Gemini to produce
  the reply. Requests ask Google not to store them.
- **Voice** uses the browser's speech recognition. In Chrome, audio is sent to
  Google to be turned into text.

---

## Tests

```
pip install -r requirements.txt
python -m pytest
```

240 tests. They run the backend offline on a fixed date and random seed with
an in-memory database, and replace the Gemini model with scripted answers, so
no network or key is needed. They cover accounts and data separation,
location tracking, the shift, recommendations, busy places, order sync,
exact reading of amounts, unrealistic-goal confirmation, the three languages,
the agent's tool loop and its fallbacks, and the backup Streamlit app.

---

## Configuration

Set as environment variables, or (for the Gemini settings) in
`website/backend/.env`.

| Name | Purpose |
|---|---|
| `GEMINI_API_KEY` | Turns on the AI agent |
| `GEMINI_MODEL` | Use one specific model instead of the built-in list |
| `TOMTOM_API_KEY` | Live road speeds instead of the rush-hour model (untested) |
| `GIGPILOT_DB` | Path for the database file |
| `GIGPILOT_OFFLINE` | `1` disables all network calls and the background clock (used by tests) |

---

## Backup version

`backup_streamlit/` holds the earlier, simpler version: four zones, no login,
no live data, no map, no agent. It runs fully offline in a single file and is
there only in case the website cannot be run.

```
cd backup_streamlit
bash start_backup.sh
```

On Windows, double-click `backup_streamlit\start_backup.bat`. Or by hand:
`pip install "streamlit>=1.50" pandas` then `streamlit run app.py`.

---

## Known limits

- **Order data is generated.** Earnings shown come from the stand-in feed, not
  from real deliveries.
- **One city.** Zones, roads and busy places are for Hyderabad. Outside the
  service area GigPilot says so and pauses the order feed.
- **Desktop location is approximate.** A computer without GPS reports a
  network-based position that can be many kilometres off. A phone is exact.
- **Location needs `localhost` or `https`.** Browsers block it on plain `http`
  from another machine.
- **Spoken replies in Telugu or Kannada** need a voice for that language
  installed on the device; otherwise the reply is shown but not spoken.
- **Telugu and Kannada interface text** was written without review by a
  native speaker.
- **Only the agent is multilingual.** The rest of the page is in English.
- **Busy-place coordinates are approximate.**
- **Single server process.** Live state is held in memory alongside the
  database; it is built for a demo, not for many concurrent users.
