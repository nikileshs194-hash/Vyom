# GigPilot

**Team Vyom — AI HACK X MRDU 2026 — Agentic AI track**
**Problem statement: AI Gig Worker Earnings Optimization Assistant**

GigPilot is a goal-driven agentic assistant for gig workers (delivery riders).
A worker logs in, sets a target earnings amount and the hours they have, and
GigPilot follows their real location, continuously watches demand, traffic,
weather and incentives across 32 Hyderabad zones, works out the best place
to go right now, explains why in plain language, and replans live the moment
conditions change — the worker stays in control and accepts or ignores every
recommendation.

## The 6 agents

GigPilot is built from five specialist agents coordinated by one master
agent, not a single black-box model (all in `website/backend/agents.py`):

1. **Opportunity Agent** — finds available delivery orders in a zone
2. **Demand Agent** — reads how busy each zone currently is
3. **Earnings Agent** — tracks progress toward the worker's earnings goal
4. **Optimization Agent** — scores and ranks all zones against each other,
   after the travel time and fuel needed to reach them
5. **Planning Agent** — turns the winning zone into a plain-English
   recommendation with a step-by-step decision trace
6. **Master Agent** — orchestrates the agents above and returns the
   final recommendation

All the real math (fuel cost, net earnings, zone scoring) is done by plain,
deterministic Python functions — the agents only reason over and explain
those numbers, never invent them.

## What is real and what stands in for partner data

| Data | Source | Status |
|---|---|---|
| Your location and location trail | The browser's GPS / location service | **Real** |
| Time | The real clock, in IST | **Real** |
| Weather (now + hourly forecast, per zone) | [Open-Meteo](https://open-meteo.com) — free, no key | **Real** |
| Name of the place you are in | [Nominatim](https://nominatim.org) lookup on OpenStreetMap data — free, no key | **Real** |
| Road distance, drive time, route line on the map | [OSRM](https://project-osrm.org) public server on OpenStreetMap data — free, no key | **Real** (distances cached in `website/backend/cache/roads.json`) |
| Map and navigation | OpenStreetMap tiles; directions open in Google Maps | **Real** |
| Accounts, goals, earnings, decisions | SQLite database, one set of data per user | **Real** |
| Road congestion | Rush-hour model + rain. Becomes real if you set a free [TomTom](https://developer.tomtom.com) key in the `TOMTOM_API_KEY` environment variable | Estimated by default |
| Orders, payouts, incentives, demand history | This is the data a partner such as Swiggy or Zomato would supply. Until then it is generated: about 295,000 orders over 60 days across 32 zones, from one consistent demand model | Stand-in |

The status bar shows a badge for each source. If a live source cannot be
reached, GigPilot falls back to a built-in estimate and keeps running.

## Accounts and stored data

Every person creates their own account (username + password). Passwords
are stored only as salted scrypt hashes, and every API call needs that
user's login token. Each account has its own:

- shifts and goals
- earnings (each order and incentive, with time, zone, app and merchant)
- accepted / ignored recommendations
- location trail

Everything is stored in `website/backend/gigpilot.db` (SQLite, created on
first run). A shift that is still open is reloaded if the server restarts.
Set the `GIGPILOT_DB` environment variable to keep the file somewhere else.

## Earnings activity

Instead of charts, GigPilot shows activity boxes:

- **Daily activity** — one box per day for the last 26 weeks, darker = more earned
- **When you earn** — one row per day, one box per hour, for the last 7 days,
  with your best and slowest hour
- **Where you earned** — earnings per zone over the last 7 days
- **Partner apps** — earnings per app (Swiggy, Zomato, Zepto, Blinkit, Swiggy Instamart,
  BigBasket) and the latest
  orders with merchant, zone and distance

Nothing is entered by hand. Every order carries the app it came through, and the
first time GigPilot learns which zone a rider works in, it syncs about 26 weeks of
their past shifts and orders (around 140 shifts and 2,000 orders) into their account. Both the live orders and that
history are generated in `website/backend/partners.py`, standing in for the feed the
delivery platforms would provide.

## Busy places

`website/backend/places.py` is a dataset of 95 well-known busy spots in
Hyderabad (malls, food streets, markets, office parks, transit hubs) with
approximate coordinates and an estimated busy score. Each zone's baseline
popularity comes from the places inside it. On the site you can set which
place is busy right now: orders surge there, the recommendation moves to
it, and the Google Maps link points at the place itself. Setting another
place replaces it. The busy scores are estimates standing in for partner
sales data.

Once you Accept or Ignore a recommendation, GigPilot shows your answer
instead of the buttons and only asks again when its suggestion changes.

## AI agent (text and voice)

A command bar is docked at the bottom of every page. Type an instruction,
or press the microphone and say it, and the agent works the screen for
you: a banner names each step while a pointer travels to the right field
or button, types, presses and scrolls, and the page updates as it goes.
It then replies in a card above the bar (and aloud, if "Speak replies" is
ticked). **Skip** finishes the remaining steps at once. The speech-bubble
button opens the full conversation.

**Three languages.** The selector in the bar switches the agent between
English, Telugu (తెలుగు) and Kannada (ಕನ್ನಡ). In the chosen language it
listens to speech, understands typed instructions, replies, narrates its
steps and speaks the reply. With the Gemini key this covers free-form
speech in all three, including mixed language. Without a key, the built-in
rules understand a small set of common Telugu and Kannada words and reply
in English. Spoken replies in Telugu or Kannada need a voice for that
language installed on the device.

The actions themselves are carried out on the server; the pointer shows
what was done, in the order it was done.

With a Google Gemini key it is a real AI agent: a language model is given
17 tools - the same actions the page offers - and decides which to call,
in what order, checking each result before the next step. So it handles
free wording, several requests in one sentence ("set my goal to 1500 for
6 hours, mark Charminar busy and take me there"), follow-up questions,
and remembers the conversation. Every fact it states comes from a tool,
not from the model's memory.

**Turning it on (free):**

1. Get a free key at https://aistudio.google.com/apikey
2. Copy `website/backend/.env.example` to `website/backend/.env`
3. Put the key after `GEMINI_API_KEY=` and restart the server

The bar shows **AI - Gemini** when the agent is active and **Basic
mode** otherwise. In basic mode, or if Gemini cannot be reached, the
rule-based assistant in `assistant.py` answers instead: it understands a
fixed set of instructions, one at a time, with no key needed.

With the agent on, your instructions and the rider data it looks up
(location name, earnings, recommendation) are sent to Google to produce
the reply. Requests ask Google not to store them. The `.env` file is
excluded from git, so the key is never uploaded.

Voice uses the browser's own speech recognition, which needs Chrome or
Edge and microphone permission.

## What's in this folder

```
GigPilot_Submission/
├── website/              <- THE MAIN SUBMISSION: full website
│   ├── backend/
│   │   ├── main.py         FastAPI app: API + serves the site
│   │   ├── agents.py       the six agents
│   │   ├── world.py        live city state (orders, traffic, riders' shifts)
│   │   ├── live.py         live data providers (weather, roads, traffic)
│   │   ├── db.py           SQLite: accounts, shifts, earnings, locations
│   │   ├── agent.py        AI agent: Gemini with tools (optional, needs a free key)
│   │   ├── assistant.py    rule-based fallback for typed and spoken instructions
│   │   ├── places.py       busy places dataset for Hyderabad
│   │   ├── partners.py     partner apps: which app an order came from, synced order history
│   │   └── data.py         zones, demand model, generated order history
│   └── frontend/           HTML/CSS/JS: login, map (Leaflet), activity boxes
├── start_website.sh        <- run this to start the website (Mac/Linux/Git Bash)
├── start_website.bat       <- same thing for Windows (double-click it)
├── requirements.txt        <- every Python package used, including tests
├── tests/                  <- automated tests: python -m pytest
│
└── backup_streamlit/     <- BACKUP ONLY, use if the website has a problem
    ├── app.py, data.py, agents.py
    └── start_backup.sh / start_backup.bat   <- run this to start the backup instead
```

## How to run it (the website — this is the real submission)

You need Python 3.10 or newer, and an internet connection for the map and
the live data.

**On Windows:** double-click `start_website.bat`. It starts the server in
its own window and opens the site; close that window to stop it.

**On Mac/Linux/Git Bash:**

```
bash start_website.sh
```

Then open **http://localhost:8000/** in your browser. Press Ctrl+C in the
terminal to stop it.

**By hand:**

```
cd website/backend
pip3 install fastapi uvicorn httpx
uvicorn main:app --port 8000
```

The server takes a few seconds to start because it builds the order
history first.

## Using it

1. **Create account**, then log in. Allow the browser to share your location.
2. Set a goal and press **Start / Update Goal**. Orders from the feed are
   assigned in the zone you are actually in, in real time.
3. The map shows your real position, your trail today, and every zone
   coloured by demand. **Click a zone** to open Google Maps directions to
   it; **click anywhere else on the map** to open that spot in Google Maps.
4. **Accept** a "Move to" recommendation, ride there, and orders resume once
   your GPS position reaches the zone. **Cancel move** if you change your
   mind; **Ignore** drops that zone from the suggestions for 20 minutes.
5. If the browser cannot share a location, pick a zone by hand. If you are
   outside Hyderabad, GigPilot says so, pauses the order feed and plans from
   the nearest zone.
6. **Demo tools** (bottom of the page) inject a made-up traffic spike,
   incentive or rain burst so you can watch the agents replan.

Location sharing works on `http://localhost` and on any `https://` address.
Browsers block it on plain `http://` from another machine.

## How to run the backup instead

Only use this if the website version won't run and you're out of time to
fix it. It is the earlier, simpler version: four zones, no login, no live
data, no map — but it runs fully offline in a single file.

```
cd backup_streamlit
bash start_backup.sh
```

On Windows, double-click `backup_streamlit\start_backup.bat` instead.

Or manually: `pip3 install "streamlit>=1.50" pandas` then `streamlit run app.py`.

## Running the tests

```
pip3 install -r requirements.txt
python -m pytest
```

The tests run the backend offline on a fixed date and random seed with an
in-memory database, and cover accounts and data separation between users,
location tracking, the shift, the replanning events, earnings tracking,
input validation, and a headless click-through of the backup Streamlit app.

## Notes for judges / reviewers

- Location, time, weather, roads and each user's stored data are real.
  Orders, payouts, incentives and the 28-day demand history stand in for
  partner data, because no delivery platform makes them available.
- The demo tools trigger a real recalculation across all agents — this is
  the moment that shows live replanning, not a static report.
- Every recommendation can be Accepted or Ignored by the worker — the
  agent never acts on its own; this is the human-in-the-loop boundary
  required by the brief.
