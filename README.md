# GigPilot

**Team Vyom — AI HACK X MRDU 2026 — Agentic AI track**
**Problem statement: AI Gig Worker Earnings Optimization Assistant**

GigPilot is a goal-driven agentic assistant for gig workers (delivery riders).
A worker sets a target earnings amount and the hours they have, and GigPilot
continuously watches demand, traffic and incentives across zones, works out
the best place to go right now, explains why in plain language, and replans
live the moment conditions change — the worker stays in control and accepts
or ignores every recommendation.

## The 6 agents

GigPilot is built from five specialist agents coordinated by one master
agent, not a single black-box model:

1. **Opportunity Agent** — finds available delivery orders in a zone
2. **Demand Agent** — reads how busy each zone currently is
3. **Earnings Agent** — tracks progress toward the worker's earnings goal
4. **Optimization Agent** — scores and ranks all zones against each other
5. **Planning Agent** — turns the winning zone into a plain-English
   recommendation with a step-by-step decision trace
6. **Master Agent** — orchestrates the five agents above and returns the
   final recommendation

All the real math (fuel cost, net earnings, zone scoring) is done by plain,
deterministic Python functions — the agents only reason over and explain
those numbers, never invent them. This logic is identical in both folders
below; only how it's presented to the user differs.

## What's in this folder

```
GigPilot_Submission/
├── website/              <- THE MAIN SUBMISSION: full website
│   ├── backend/           (FastAPI — serves the agent logic as an API)
│   └── frontend/           (HTML/CSS/JS — the actual site a user sees)
├── start_website.sh        <- run this to start the full website (Mac/Linux/Git Bash)
├── start_website.bat       <- same thing for Windows (double-click it)
├── requirements.txt        <- every Python package used, including tests
├── tests/                  <- automated tests: python -m pytest
│
└── backup_streamlit/     <- BACKUP ONLY, use if the website has a problem
    ├── app.py, data.py, agents.py
    └── start_backup.sh / start_backup.bat   <- run this to start the backup instead
```

## How to run it (the website — this is the real submission)

You need Python 3.10 or newer installed.

**Easiest way:** open a terminal in this folder and run:

```
bash start_website.sh
```

Then open **http://localhost:5500/index.html** in your browser. Press
Ctrl+C in the terminal to stop it.

**On Windows:** double-click `start_website.bat` instead. It opens the
backend and frontend in two windows and launches the site in your browser;
close those two windows to stop it.

**If that script doesn't work on your system**, you can start the two
pieces by hand in two terminal windows:

```
# Terminal 1
cd website/backend
pip3 install fastapi uvicorn
uvicorn main:app --port 8000

# Terminal 2
cd website/frontend
python3 -m http.server 5500
```

Then open **http://localhost:5500/index.html**.

## How to run the backup instead

Only use this if the website version won't run and you're out of time to
fix it — it has the identical agent logic in a single simpler file.

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

This covers every API endpoint, the replanning events, input validation,
and a headless click-through of the backup Streamlit app.

## Notes for judges / reviewers

- All data (zones, orders, demand patterns) is realistic mock data,
  generated with a fixed structure so the demo behaves consistently.
- "Simulate traffic spike" and "Simulate incentive activation" on the
  dashboard trigger a real recalculation across all agents — this is the
  moment that shows live replanning, not a static report.
- Every recommendation can be Accepted or Ignored by the worker — the
  agent never acts on its own; this is the human-in-the-loop boundary
  required by the brief.
