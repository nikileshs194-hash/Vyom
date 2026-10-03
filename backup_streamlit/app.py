"""
GigPilot - Streamlit MVP
Run with:  streamlit run app.py
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from data import default_zones, weekly_summary_mock, HOUR_BLOCKS
from agents import master_agent

st.set_page_config(page_title="GigPilot", layout="wide")

# ---------------- session state init ----------------
if "started" not in st.session_state:
    st.session_state.started = False
if "zones" not in st.session_state:
    st.session_state.zones = default_zones()
if "history" not in st.session_state:
    st.session_state.history = []
if "tick" not in st.session_state:
    st.session_state.tick = 0
if "current_zone_name" not in st.session_state:
    st.session_state.current_zone_name = None
if "last_event" not in st.session_state:
    st.session_state.last_event = None
if "flash" not in st.session_state:
    st.session_state.flash = None

st.title("GigPilot")
st.caption("Agentic AI for autonomous gig-work earnings optimization")

# ---------------- sidebar: GOAL screen ----------------
with st.sidebar:
    st.header("Today's Goal")
    target_earnings = st.number_input(
        "Target earnings (Rs)", min_value=100, max_value=5000, value=1000, step=50
    )
    available_hours = st.number_input(
        "Hours available", min_value=1.0, max_value=12.0, value=5.0, step=0.5
    )
    vehicle = st.selectbox("Vehicle", ["bike", "scooter", "car"])
    hour_block = st.selectbox("Time of day (for demand pattern)", HOUR_BLOCKS, index=2)
    earned_so_far = st.number_input(
        "Earned so far (Rs)", min_value=0, max_value=5000, value=620, step=10
    )
    hours_elapsed = st.number_input(
        "Hours already worked", min_value=0.0, max_value=12.0, value=2.5, step=0.5
    )

    start_clicked = st.button("Start / Update Goal", width="stretch")
    if start_clicked and hours_elapsed > available_hours:
        st.error("Hours already worked cannot exceed hours available.")
    elif start_clicked:
        st.session_state.started = True
        st.session_state.state = {
            "target_earnings": target_earnings,
            "earned_so_far": earned_so_far,
            "available_hours": available_hours,
            "hours_elapsed": hours_elapsed,
        }
        if st.session_state.current_zone_name is None:
            st.session_state.current_zone_name = st.session_state.zones[0]["name"]

    st.divider()
    st.caption("GigPilot Status")
    st.success("MONITORING" if st.session_state.started else "WAITING FOR GOAL")

if not st.session_state.started:
    st.info("Set your goal in the sidebar and click **Start / Update Goal** to begin.")
    st.stop()

state = st.session_state.state
zones = st.session_state.zones

# ---------------- DASHBOARD ----------------
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric(
        "Earned so far",
        f"Rs {state['earned_so_far']:.0f}",
        f"of Rs {state['target_earnings']:.0f} goal",
    )
with col2:
    remaining_hours = max(state["available_hours"] - state["hours_elapsed"], 0)
    st.metric("Time remaining", f"{remaining_hours:.1f} h")
with col3:
    st.metric("Current zone", st.session_state.current_zone_name.split(" - ")[1])
with col4:
    progress_pct = min(
        100, int(100 * state["earned_so_far"] / state["target_earnings"])
    )
    st.metric("Progress", f"{progress_pct}%")

st.progress(progress_pct / 100)
st.divider()

# ---------------- RECOMMENDATION ----------------
left, right = st.columns([3, 2])

with left:
    st.subheader("GigPilot Recommendation")
    rec = master_agent(
        state,
        zones,
        hour_block,
        vehicle,
        st.session_state.current_zone_name,
        tick=st.session_state.tick,
    )

    st.markdown(f"### {rec['action']}")
    st.write(rec["reason"])
    st.progress(rec["confidence"] / 100, text=f"Confidence: {rec['confidence']}%")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Accept", width="stretch", type="primary"):
            st.session_state.current_zone_name = rec["target_zone_name"]
            st.session_state.history.append(
                {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "action": rec["action"],
                    "outcome": "Accepted",
                    "confidence": rec["confidence"],
                }
            )
            # rerun so the metrics and recommendation reflect the new zone
            st.session_state.flash = ("success", f"Moved to {rec['target_zone_name']}")
            st.rerun()
    with c2:
        if st.button("Ignore", width="stretch"):
            st.session_state.history.append(
                {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "action": rec["action"],
                    "outcome": "Ignored",
                    "confidence": rec["confidence"],
                }
            )
            st.session_state.flash = (
                "info",
                "Recommendation ignored — staying on current plan.",
            )
            st.rerun()

    if st.session_state.flash:
        kind, message = st.session_state.flash
        st.session_state.flash = None
        (st.success if kind == "success" else st.info)(message)

    with st.expander("Decision trace"):
        for line in rec["decision_trace"]:
            st.write("•", line)

with right:
    st.subheader("Live Replanning Demo")
    st.caption("Trigger a live condition change and watch GigPilot replan.")

    zone_ids = {z["name"]: z["id"] for z in zones}
    pick = st.selectbox("Target zone for event", list(zone_ids.keys()))

    if st.button("Simulate traffic spike", width="stretch"):
        for z in zones:
            if z["id"] == zone_ids[pick]:
                z["traffic_factor"] = 1.8
        st.session_state.tick += 1
        st.session_state.last_event = f"Traffic spike simulated in {pick}"
        st.rerun()

    if st.button("Simulate incentive activation", width="stretch"):
        for z in zones:
            if z["id"] == zone_ids[pick]:
                z["incentive"] = {
                    "description": "Complete 3 more orders for Rs 100 bonus",
                    "bonus": 100,
                }
        st.session_state.tick += 1
        st.session_state.last_event = f"Incentive activated in {pick}"
        st.rerun()

    if st.button("Reset all zone conditions", width="stretch"):
        st.session_state.zones = default_zones()
        st.session_state.tick += 1
        st.session_state.last_event = "All zone conditions reset to normal"
        st.rerun()

    if st.session_state.last_event:
        st.warning(f"Last event: {st.session_state.last_event}")

    st.divider()
    st.caption("Zone comparison (expected Rs/hour)")
    comp_df = pd.DataFrame(
        [
            {
                "Zone": c["zone_name"].split(" - ")[1],
                "Demand": c["demand_level"],
                "Rs/hr (low-high)": f"{c['net_rate_low']:.0f}-{c['net_rate_high']:.0f}",
            }
            for c in rec["ranked_candidates"]
        ]
    )
    st.dataframe(comp_df, hide_index=True, width="stretch")

st.divider()

# ---------------- HISTORY + WEEKLY SUMMARY ----------------
tab1, tab2 = st.tabs(["Recommendation history", "Weekly summary"])

with tab1:
    if st.session_state.history:
        hist_df = pd.DataFrame(st.session_state.history)
        st.dataframe(hist_df, hide_index=True, width="stretch")
        csv = hist_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Export history as CSV", csv, "gigpilot_history.csv", "text/csv"
        )
    else:
        st.caption("No recommendations acted on yet.")

with tab2:
    summary = weekly_summary_mock()
    st.write(f"**Best earning window:** {summary['best_window']}")
    st.write(f"**Best-performing zone:** {summary['best_zone']}")
    st.write(f"**Average net earning rate:** Rs {summary['avg_net_rate']}/hour")
    st.caption(summary["note"])
