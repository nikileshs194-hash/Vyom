// GigPilot frontend logic.
// Talks to the FastAPI backend at API_BASE. Change this one line if the
// backend runs on a different host/port (e.g. during the actual demo).
const API_BASE = "http://localhost:8000";

let zonesCache = [];

async function apiFetch(path, options) {
  let res;
  try {
    res = await fetch(API_BASE + path, options);
  } catch (err) {
    alert("Cannot reach the GigPilot backend at " + API_BASE + ". Is it running?");
    throw err;
  }
  if (!res.ok) {
    let detail = "Check your inputs.";
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail) && body.detail.length) {
        const d = body.detail[0];
        const field = d.loc && d.loc.length > 1 ? d.loc[d.loc.length - 1] + ": " : "";
        detail = field + d.msg.replace("Value error, ", "");
      }
    } catch (err) { /* non-JSON error body, keep the generic message */ }
    alert("GigPilot backend rejected the request (" + res.status + "). " + detail);
    throw new Error(path + " failed with " + res.status);
  }
  return res.json();
}
async function apiGet(path) {
  return apiFetch(path);
}
async function apiPost(path, body) {
  return apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
}

function zoneShort(fullName) {
  // "Zone A - Koramangala" -> "Koramangala"
  const parts = fullName.split(" - ");
  return parts.length > 1 ? parts[1] : fullName;
}

async function init() {
  const hourBlocks = await apiGet("/api/hour-blocks");
  const hourSelect = document.getElementById("hourBlock");
  hourBlocks.forEach((h) => {
    const opt = document.createElement("option");
    opt.value = h;
    opt.textContent = h;
    hourSelect.appendChild(opt);
  });
  hourSelect.value = "evening";

  zonesCache = await apiGet("/api/zones");
  const eventZoneSelect = document.getElementById("eventZone");
  zonesCache.forEach((z) => {
    const opt = document.createElement("option");
    opt.value = z.id;
    opt.textContent = z.name;
    eventZoneSelect.appendChild(opt);
  });
}

document.getElementById("launchBtn").addEventListener("click", () => {
  document.getElementById("appSection").classList.remove("hidden");
  document.getElementById("appSection").scrollIntoView({ behavior: "smooth" });
});

document.getElementById("startBtn").addEventListener("click", async () => {
  const goal = {
    target_earnings: parseFloat(document.getElementById("targetEarnings").value),
    available_hours: parseFloat(document.getElementById("availableHours").value),
    vehicle: document.getElementById("vehicle").value,
    hour_block: document.getElementById("hourBlock").value,
    earned_so_far: parseFloat(document.getElementById("earnedSoFar").value),
    hours_elapsed: parseFloat(document.getElementById("hoursElapsed").value),
  };
  await apiPost("/api/goal", goal);

  const badge = document.getElementById("statusBadge");
  badge.textContent = "MONITORING";
  badge.className = "badge badge-monitoring";

  document.getElementById("dashboard").classList.remove("hidden");
  await refreshRecommendation();
  await refreshHistory();
  await refreshWeeklySummary();
});

async function refreshRecommendation() {
  const data = await apiGet("/api/recommendation");
  if (!data.started) return;

  const m = data.metrics;
  document.getElementById("mEarned").textContent =
    "Rs " + m.earned_so_far.toFixed(0) + " / Rs " + m.target_earnings.toFixed(0);
  document.getElementById("mTime").textContent = m.remaining_hours.toFixed(1) + " h";
  document.getElementById("mZone").textContent = zoneShort(m.current_zone_name);
  document.getElementById("mProgress").textContent = m.progress_pct + "%";
  document.getElementById("progressFill").style.width = m.progress_pct + "%";

  const rec = data.recommendation;
  document.getElementById("recAction").textContent = rec.action;
  document.getElementById("recReason").textContent = rec.reason;
  document.getElementById("confFill").style.width = rec.confidence + "%";
  document.getElementById("confLabel").textContent = "Confidence: " + rec.confidence + "%";

  const traceList = document.getElementById("traceList");
  traceList.innerHTML = "";
  rec.decision_trace.forEach((line) => {
    const li = document.createElement("li");
    li.textContent = line;
    traceList.appendChild(li);
  });

  const zoneBody = document.getElementById("zoneCompareBody");
  zoneBody.innerHTML = "";
  rec.ranked_candidates.forEach((c) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + zoneShort(c.zone_name) + "</td>" +
      "<td>" + c.demand_level + "</td>" +
      "<td>" + c.net_rate_low.toFixed(0) + "-" + c.net_rate_high.toFixed(0) + "</td>";
    zoneBody.appendChild(tr);
  });

  const lastEventEl = document.getElementById("lastEvent");
  if (data.last_event) {
    lastEventEl.textContent = "Last event: " + data.last_event;
    lastEventEl.classList.remove("hidden");
  } else {
    lastEventEl.classList.add("hidden");
  }
}

async function refreshHistory() {
  const history = await apiGet("/api/history");
  const body = document.getElementById("historyBody");
  const noHistory = document.getElementById("noHistory");
  body.innerHTML = "";
  if (history.length === 0) {
    noHistory.classList.remove("hidden");
    return;
  }
  noHistory.classList.add("hidden");
  history.forEach((h) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + h.time + "</td>" +
      "<td>" + h.action + "</td>" +
      "<td>" + h.outcome + "</td>" +
      "<td>" + h.confidence + "</td>";
    body.appendChild(tr);
  });
}

async function refreshWeeklySummary() {
  const s = await apiGet("/api/weekly-summary");
  document.getElementById("wBestWindow").textContent = s.best_window;
  document.getElementById("wBestZone").textContent = s.best_zone;
  document.getElementById("wAvgRate").textContent = s.avg_net_rate;
  document.getElementById("wNote").textContent = s.note;
}

document.getElementById("acceptBtn").addEventListener("click", async () => {
  await apiPost("/api/accept");
  await refreshRecommendation();
  await refreshHistory();
});

document.getElementById("ignoreBtn").addEventListener("click", async () => {
  await apiPost("/api/ignore");
  await refreshRecommendation();
  await refreshHistory();
});

document.getElementById("trafficBtn").addEventListener("click", async () => {
  const zoneId = document.getElementById("eventZone").value;
  await apiPost("/api/simulate/traffic", { zone_id: zoneId });
  await refreshRecommendation();
});

document.getElementById("incentiveBtn").addEventListener("click", async () => {
  const zoneId = document.getElementById("eventZone").value;
  await apiPost("/api/simulate/incentive", { zone_id: zoneId });
  await refreshRecommendation();
});

document.getElementById("resetBtn").addEventListener("click", async () => {
  await apiPost("/api/simulate/reset");
  await refreshRecommendation();
});

init();
