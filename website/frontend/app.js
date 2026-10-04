// GigPilot frontend logic.
// Logs the user in, follows their real GPS position, polls the FastAPI
// backend for the live city state and redraws the map, the recommendation
// and the earnings activity boxes. The backend normally serves this page,
// so API calls go to the same origin; opened as a file or from a separate
// static server on port 5500, it expects the backend on localhost:8000.
const API_BASE =
  location.protocol === "file:" || location.port === "5500" ? "http://localhost:8000" : "";
const POLL_MS = 3000;
const ACTIVITY_MS = 20000;
const LOCATION_SEND_MS = 5000;
const TOKEN_KEY = "gigpilot_token";

// one hue, light -> dark: every quantity on the page uses these four steps
const LEVELS = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab"];
const NAVY = "#0f2442";

const $ = (id) => document.getElementById(id);
let token = null;
let zones = [];
let lastState = null;
let registerMode = false;
let timers = [];
let watchId = null;
let lastLocationSent = 0;
let polling = false;
let agentHold = false; // true while the agent is acting a step out on screen
let goalEditing = false;
let toastTimer = null;

let map = null;
let places = [];
let youMarker = null;
let busyMarker = null;
let accuracyCircle = null;
let trailLayer = null;
let routeLayer = null;
let routeKey = null;
const zoneMarkers = {};

// ---------------------------------------------------------------- helpers

function showToast(message, isError, sticky) {
  const toast = $("toast");
  toast.textContent = message;
  toast.className = "toast" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  if (!sticky) toastTimer = setTimeout(() => toast.classList.add("hidden"), 4000);
}
function hideToast() {
  clearTimeout(toastTimer);
  $("toast").classList.add("hidden");
}

function errorDetail(body) {
  if (typeof body.detail === "string") return body.detail;
  if (body.detail && typeof body.detail.message === "string") return body.detail.message;
  if (Array.isArray(body.detail) && body.detail.length) {
    const d = body.detail[0];
    const field = d.loc && d.loc.length > 1 ? d.loc[d.loc.length - 1] + ": " : "";
    return field + d.msg.replace("Value error, ", "");
  }
  return "Check your inputs.";
}

async function apiFetch(path, options, silent) {
  const opts = { ...(options || {}) };
  opts.headers = { ...(opts.headers || {}) };
  if (token) opts.headers.Authorization = "Bearer " + token;
  let res;
  try {
    res = await fetch(API_BASE + path, opts);
  } catch (err) {
    if (!silent) showToast("Cannot reach the GigPilot backend. Is it running?", true);
    throw err;
  }
  if (res.status === 401 && token) {
    signOut("Your session has ended - please log in again.");
    throw new Error("logged out");
  }
  if (!res.ok) {
    let detail = "Check your inputs.";
    let code = null;
    try {
      const body = await res.json();
      detail = errorDetail(body);
      code = body.detail && body.detail.code;
    } catch (err) { /* non-JSON error body, keep the generic message */ }
    const error = new Error(detail);
    error.status = res.status;
    error.code = code;
    if (code === "confirm_goal") throw error; // the caller asks the rider before retrying
    if (!silent) showToast(detail, true);
    throw error;
  }
  return res.json();
}
const apiGet = (path, silent) => apiFetch(path, undefined, silent);
function apiPost(path, body, silent) {
  return apiFetch(
    path,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    },
    silent
  );
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function fillSelect(select, options) {
  options.forEach(([value, label]) => {
    const opt = el("option", label);
    opt.value = value;
    select.appendChild(opt);
  });
}
const rs = (n) => "Rs " + Math.round(n).toLocaleString("en-IN");
// 0 = nothing, 1..4 = quartiles of the largest value shown
const level = (value, max) => (value <= 0 || max <= 0 ? 0 : Math.min(4, Math.ceil((4 * value) / max)));
const demandColor = (index) => LEVELS[Math.min(3, Math.floor(index / 0.45))];

// Hover card for the activity boxes: shows at once, follows the pointer,
// and opens on tap for touch screens.
function setTip(box, title, sub) {
  box.dataset.tip = title;
  box.dataset.tipSub = sub;
}
function hourRange(h) {
  const label = (x) => (x % 12 || 12) + (x % 24 < 12 ? " AM" : " PM");
  return label(h) + " - " + label(h + 1);
}
function showTip(box, x, y) {
  $("tipTitle").textContent = box.dataset.tip;
  $("tipSub").textContent = box.dataset.tipSub;
  const tip = $("tip");
  tip.classList.remove("hidden");
  const w = tip.offsetWidth;
  const h = tip.offsetHeight;
  tip.style.left = Math.max(8, Math.min(x - w / 2, window.innerWidth - w - 8)) + "px";
  tip.style.top = (y - h - 14 < 8 ? y + 18 : y - h - 14) + "px";
}
function hideTip() {
  $("tip").classList.add("hidden");
  document.querySelectorAll(".tip-open").forEach((b) => b.classList.remove("tip-open"));
}
document.addEventListener("mousemove", (event) => {
  const box = event.target.closest ? event.target.closest("[data-tip]") : null;
  if (box) showTip(box, event.clientX, event.clientY);
  else if (!document.querySelector(".tip-open")) hideTip();
});
document.addEventListener("click", (event) => {
  const box = event.target.closest ? event.target.closest("[data-tip]") : null;
  hideTip();
  if (box) {
    box.classList.add("tip-open");
    const r = box.getBoundingClientRect();
    showTip(box, r.left + r.width / 2, r.top);
  }
});
window.addEventListener("scroll", hideTip, true);

// Google Maps hand-off links
function travelMode() {
  const vehicle = $("vehicle").value;
  return vehicle === "car" ? "driving" : "two-wheeler";
}
function directionsUrl(lat, lon) {
  let url = "https://www.google.com/maps/dir/?api=1&destination=" + lat + "," + lon +
    "&travelmode=" + travelMode();
  const pos = lastState && lastState.position;
  if (pos && !pos.manual) url += "&origin=" + pos.lat + "," + pos.lon;
  return url;
}
const placeUrl = (lat, lon) =>
  "https://www.google.com/maps/search/?api=1&query=" + lat.toFixed(6) + "," + lon.toFixed(6);

// ------------------------------------------------------------------- auth

function setAuthMode(register) {
  registerMode = register;
  $("tabLogin").classList.toggle("active", !register);
  $("tabRegister").classList.toggle("active", register);
  $("authNameRow").classList.toggle("hidden", !register);
  $("authSubmit").textContent = register ? "Create account" : "Log in";
  $("authPassword").autocomplete = register ? "new-password" : "current-password";
  $("authError").classList.add("hidden");
}
$("tabLogin").addEventListener("click", () => setAuthMode(false));
$("tabRegister").addEventListener("click", () => setAuthMode(true));

$("authForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = { username: $("authUsername").value.trim(), password: $("authPassword").value };
  if (registerMode) body.name = $("authName").value.trim() || body.username;
  try {
    const result = await apiPost(registerMode ? "/api/register" : "/api/login", body, true);
    $("authPassword").value = "";
    token = result.token;
    try { localStorage.setItem(TOKEN_KEY, token); } catch (err) { /* private window */ }
    await enterApp();
  } catch (err) {
    $("authError").textContent = err.status ? err.message : "Cannot reach the GigPilot backend.";
    $("authError").classList.remove("hidden");
  }
});

$("logoutBtn").addEventListener("click", async () => {
  try { await apiPost("/api/logout", undefined, true); } catch (err) { /* already gone */ }
  signOut();
});

function signOut(message) {
  token = null;
  lastState = null;
  try { localStorage.removeItem(TOKEN_KEY); } catch (err) { /* private window */ }
  timers.forEach(clearInterval);
  timers = [];
  if (watchId !== null && navigator.geolocation) navigator.geolocation.clearWatch(watchId);
  watchId = null;
  resetAgent();
  goalEditing = false;
  $("appView").classList.add("hidden");
  $("navUser").classList.add("hidden");
  $("navLinks").classList.add("hidden");
  $("navTagline").classList.remove("hidden");
  $("authView").classList.remove("hidden");
  ["dashboard", "kpis", "recBody", "endBtn"].forEach((id) => $(id).classList.add("hidden"));
  $("recEmpty").classList.remove("hidden");
  if (message) showToast(message, true);
}

async function enterApp() {
  if (!zones.length) {
    zones = await apiGet("/api/zones");
    const options = zones.map((z) => [z.id, z.name]);
    fillSelect($("manualZone"), options);
    fillSelect($("eventZone"), options);
  }
  $("authView").classList.add("hidden");
  $("appView").classList.remove("hidden");
  $("navUser").classList.remove("hidden");
  $("navLinks").classList.remove("hidden");
  $("navTagline").classList.add("hidden");
  initAgent();
  initMap();
  if (map) map.invalidateSize();
  startLocationWatch();
  await poll();
  refreshActivity();
  refreshHistory();
  refreshPlaces();
  timers.push(setInterval(poll, POLL_MS));
  timers.push(setInterval(refreshActivity, ACTIVITY_MS));
  timers.push(setInterval(refreshPlaces, 60000));
}

async function init() {
  try { token = localStorage.getItem(TOKEN_KEY); } catch (err) { token = null; }
  if (token) {
    try {
      await apiGet("/api/me", true);
      await enterApp();
      return;
    } catch (err) {
      if (token) { // backend unreachable rather than an expired login
        showToast("Cannot reach the GigPilot backend - retrying...", true, true);
        setTimeout(() => { hideToast(); init(); }, 3000);
        return;
      }
    }
  }
  $("authView").classList.remove("hidden");
}

// --------------------------------------------------------------- location

function startLocationWatch() {
  if (!navigator.geolocation) {
    locationProblem("This browser cannot share a location - pick your zone by hand.");
    return;
  }
  if (watchId !== null) navigator.geolocation.clearWatch(watchId);
  watchId = navigator.geolocation.watchPosition(
    async (pos) => {
      if (!token || Date.now() - lastLocationSent < LOCATION_SEND_MS) return;
      lastLocationSent = Date.now();
      const { latitude, longitude, accuracy } = pos.coords;
      try {
        await apiPost("/api/location", { lat: latitude, lon: longitude, accuracy }, true);
        if ($("manualZone").value) $("manualZone").value = "";
        poll();
      } catch (err) { /* the next fix will retry */ }
    },
    (err) => {
      locationProblem(
        err.code === err.PERMISSION_DENIED
          ? "Location permission was not given. Allow it in the browser, or pick your zone by hand."
          : "Your location is not available right now - pick your zone by hand."
      );
    },
    { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 }
  );
}
function locationProblem(message) {
  if (lastState && lastState.position) return; // keep showing the last known position
  $("hereLabel").textContent = "Location not shared";
  $("herePlace").textContent = "Where are you?";
  $("hereSub").textContent = message;
  $("hereSub").classList.add("warn");
  $("hereFallback").classList.remove("hidden");
}
$("locateBtn").addEventListener("click", () => {
  lastLocationSent = 0;
  startLocationWatch();
});
$("manualZone").addEventListener("change", async () => {
  const zoneId = $("manualZone").value;
  if (!zoneId) return;
  await apiPost("/api/location/zone", { zone_id: zoneId });
  await poll();
});

function renderLocation(pos) {
  const sub = $("hereSub");
  const live = Boolean(pos) && !pos.manual;
  $("hereDot").classList.toggle("on", live);
  $("hereFallback").classList.toggle("hidden", live);
  if (!pos) {
    if ($("hereLabel").textContent !== "Location not shared") {
      $("hereLabel").textContent = "Location not shared yet";
      $("herePlace").textContent = "Where are you?";
      sub.textContent = "Share your location to see where you are and get orders near you.";
    }
    return;
  }
  sub.classList.toggle("warn", !pos.in_service_area);
  if (pos.manual) {
    $("hereLabel").textContent = "Set by hand";
    $("herePlace").textContent = pos.zone_name;
    sub.textContent = "Not tracking your real position.";
    return;
  }
  $("hereLabel").textContent = "Live location";
  $("herePlace").textContent = pos.place || pos.lat.toFixed(4) + ", " + pos.lon.toFixed(4);
  if (pos.in_service_area) {
    sub.textContent =
      pos.zone_name + " zone · " + pos.distance_km + " km away · updated " +
      pos.updated.slice(0, 5) +
      // a fix this loose comes from the network, not a GPS chip
      (pos.accuracy > 1000 ? " · approximate, no GPS on this device" : "");
  } else {
    sub.textContent =
      pos.distance_km + " km outside the " + lastState.city_name +
      " service area - no orders are assigned. Nearest zone: " + pos.zone_name + ".";
  }
}

// -------------------------------------------------------------------- map

function initMap() {
  if (map || !window.L) return;
  const mid = (key) => zones.reduce((sum, z) => sum + z[key], 0) / zones.length;
  map = L.map("map", { scrollWheelZoom: false }).setView([mid("lat"), mid("lon")], 11);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  zones.forEach((z) => {
    zoneMarkers[z.id] = L.circleMarker([z.lat, z.lon], {
      radius: 10, weight: 2, color: "#ffffff", fillColor: LEVELS[1], fillOpacity: 0.95,
    })
      .addTo(map)
      .on("click", (event) => {
        L.DomEvent.stopPropagation(event);
        window.open(directionsUrl(z.lat, z.lon), "_blank", "noopener");
      });
  });
  map.on("click", (event) => {
    window.open(placeUrl(event.latlng.lat, event.latlng.lng), "_blank", "noopener");
  });
  trailLayer = L.polyline([], { color: "#2a78d6", weight: 3, opacity: 0.7 }).addTo(map);
  busyMarker = L.marker([0, 0], {
    icon: L.divIcon({ className: "busy-marker", iconSize: [20, 20] }),
    zIndexOffset: 900,
  }).on("click", (event) => {
    L.DomEvent.stopPropagation(event);
    const spot = busyMarker.getLatLng();
    window.open(directionsUrl(spot.lat, spot.lng), "_blank", "noopener");
  });
  youMarker = L.marker([0, 0], {
    icon: L.divIcon({ className: "you-marker", iconSize: [18, 18] }),
    zIndexOffset: 1000,
    interactive: false,
  });
  accuracyCircle = L.circle([0, 0], {
    radius: 0, color: "#2a78d6", weight: 1, fillOpacity: 0.12, interactive: false,
  });
}

function zoneTooltip(z, candidate) {
  const box = el("div");
  box.appendChild(el("strong", z.name));
  const lines = [
    "Demand: " + z.level + " (index " + z.demand.toFixed(2) + ")",
    "Open orders: " + z.open_orders,
    "Traffic: x" + z.traffic.toFixed(2) + " | " + z.temp + "°C" +
      (z.rain >= 0.1 ? " | rain " + z.rain + " mm/h" : ""),
  ];
  if (candidate) {
    lines.push("Expected: Rs " + candidate.net_rate_low + "-" + candidate.net_rate_high + "/hr");
    lines.push("Ride from you: ~" + candidate.travel_penalty_min + " min");
  }
  if (z.incentive) lines.push("Incentive: " + z.incentive);
  lines.push("Click for Google Maps directions");
  lines.forEach((line) => box.appendChild(el("div", line)));
  return box;
}

async function updateRoute(targetId, position) {
  const here = position ? position.lat.toFixed(3) + "," + position.lon.toFixed(3) : "";
  const key = targetId ? here + ">" + targetId : null;
  if (key === routeKey) return;
  routeKey = key;
  if (routeLayer) routeLayer.remove();
  routeLayer = null;
  if (!key) return;
  let route;
  try {
    route = await apiGet("/api/route?b=" + targetId, true);
  } catch (err) {
    return;
  }
  if (routeKey !== key) return; // a newer route was requested meanwhile
  routeLayer = L.polyline(route.line, {
    color: NAVY, weight: 4, opacity: 0.85, dashArray: "8 8", interactive: false,
  }).addTo(map);
}

function renderMap(data) {
  if (!map) return;
  const rec = data.recommendation;
  const byZone = {};
  if (rec) rec.ranked_candidates.forEach((c) => (byZone[c.zone_id] = c));
  const targetId = rec ? rec.target_zone_id : null;

  data.zones.forEach((z) => {
    const isTarget = z.id === targetId;
    zoneMarkers[z.id]
      .setStyle({
        fillColor: demandColor(z.demand),
        color: isTarget || z.incentive ? NAVY : "#ffffff",
        weight: isTarget ? 4 : 2,
        dashArray: z.incentive && !isTarget ? "4 4" : null,
      })
      .setRadius(isTarget ? 13 : 10)
      .bindTooltip(zoneTooltip(z, byZone[z.id]), { direction: "top", offset: [0, -8] });
  });

  const pos = data.position;
  if (pos) {
    youMarker.setLatLng([pos.lat, pos.lon]);
    if (!map.hasLayer(youMarker)) {
      youMarker.addTo(map);
      map.setView([pos.lat, pos.lon], pos.in_service_area ? 12 : 9);
    }
    accuracyCircle.setLatLng([pos.lat, pos.lon]).setRadius(pos.accuracy || 0);
    if (pos.accuracy && !map.hasLayer(accuracyCircle)) accuracyCircle.addTo(map);
  }
  trailLayer.setLatLngs(data.trail);

  const busy = data.busy_place;
  if (busy) {
    busyMarker.setLatLng([busy.lat, busy.lon]).bindTooltip(
      busy.name + " - busy until " + busy.until, { direction: "top", offset: [0, -10] }
    );
    if (!map.hasLayer(busyMarker)) busyMarker.addTo(map);
  } else if (map.hasLayer(busyMarker)) {
    busyMarker.remove();
  }

  const moving = data.rider && targetId && targetId !== data.rider.zone_id;
  updateRoute(moving ? targetId : null, pos);
}

// ----------------------------------------------------------------- render

function renderStatus(data) {
  $("navClock").textContent = data.clock.date + "  " + data.clock.time;
  $("navName").textContent = data.user.name;
  $("navAvatar").textContent = data.user.name.trim().charAt(0).toUpperCase();

  const city = data.city;
  const traffic =
    city.avg_traffic < 1.15 ? "Light" : city.avg_traffic < 1.4 ? "Moderate" : "Heavy";
  const stats = [
    [city.temp + "°C · " + city.condition,
      city.rain_zones ? "Raining in " + city.rain_zones + " zones" : "Weather"],
    [city.open_orders, "Open orders in the city"],
    [city.active_incentives, "Incentives live"],
    [traffic, "Traffic"],
  ];
  const box = $("cityChips");
  box.innerHTML = "";
  stats.forEach(([value, label]) => {
    const stat = el("div", undefined, "here-stat");
    stat.appendChild(el("b", String(value)));
    stat.appendChild(el("span", label));
    box.appendChild(stat);
  });
}

function renderShift(data) {
  const m = data.metrics;
  const r = data.rider;
  $("mEarned").textContent = rs(m.earned_so_far);
  const coming = m.order_under_way;
  $("mProgress").textContent =
    m.progress_pct + "% of " + rs(m.target_earnings) + " goal" +
    (coming ? " · +" + rs(coming.net) + " arriving " + coming.done : "");
  $("progressFill").style.width = m.progress_pct + "%";
  $("mTime").textContent = m.remaining_hours.toFixed(1) + " h";
  $("mOrders").textContent = r.orders_done + " orders delivered this shift";
  $("mPace").textContent = m.current_pace === null ? "Starting" : rs(m.current_pace) + "/hr";
  $("mPaceNeeded").textContent =
    m.required_pace === null ? "Shift time is up" : "Needs " + rs(m.required_pace) + "/hr for goal";
  $("mProjected").textContent = rs(m.projected_earnings);
  const gap = m.projected_earnings - m.target_earnings;
  $("mProjectedSub").textContent =
    gap >= 0 ? rs(gap) + " above goal" : rs(-gap) + " short of goal";
  const pos = data.position;
  const exact = pos && !pos.manual;
  $("mZone").textContent = exact
    ? pos.place || pos.lat.toFixed(4) + ", " + pos.lon.toFixed(4)
    : m.current_zone_name;
  $("mStatus").textContent =
    (exact ? "Zone: " + m.current_zone_name + " (" + pos.distance_km + " km) · " : "") +
    r.status_text;

  const rec = data.recommendation;
  $("recAction").textContent = rec.action;
  $("recReason").textContent = rec.reason;
  $("confFill").style.width = rec.confidence + "%";
  $("confLabel").textContent = "Confidence: " + rec.confidence + "%";
  const best = rec.ranked_candidates.find((c) => c.zone_id === rec.target_zone_id);
  const chips = $("recChips");
  chips.innerHTML = "";
  [
    "Rs " + rec.net_rate_low + "-" + rec.net_rate_high + " / hr",
    best.travel_penalty_min ? best.travel_penalty_min + " min ride" : "You are here",
    best.demand_level.toLowerCase() + " demand",
    best.open_orders + " open orders",
  ].forEach((text) => chips.appendChild(el("span", text, "rec-chip")));
  // once answered, the buttons give way to the answer until the suggestion changes
  const answered = Boolean(rec.decision) || r.status === "shift_over";
  $("acceptBtn").classList.toggle("hidden", answered);
  $("ignoreBtn").classList.toggle("hidden", answered);
  $("decisionNote").classList.toggle("hidden", !rec.decision);
  if (rec.decision) {
    $("decisionNote").textContent =
      (rec.decision.outcome === "Ignored" ? "Last suggestion ignored" : "Accepted") +
      " at " + rec.decision.time;
  }
  $("cancelMoveBtn").classList.toggle("hidden", !r.heading_to);
  const dest = rec.destination;
  $("recMapsLink").href = directionsUrl(dest.lat, dest.lon);
  $("recMapsLink").textContent = "Open route to " + dest.name + " in Google Maps";

  const traceList = $("traceList");
  traceList.innerHTML = "";
  rec.decision_trace.forEach((line) => traceList.appendChild(el("li", line)));

  const lastEvent = $("lastEvent");
  lastEvent.textContent = "Last event: " + data.last_event;
  lastEvent.classList.toggle("hidden", !data.last_event);

  const zoneBody = $("zoneCompareBody");
  zoneBody.innerHTML = "";
  rec.ranked_candidates.slice(0, 12).forEach((c) => {
    const tr = el("tr", undefined, c.zone_id === rec.target_zone_id ? "is-target" : "");
    [
      c.zone_name + (c.incentive_note ? " (incentive)" : ""),
      c.demand_level,
      c.net_rate_low + "-" + c.net_rate_high,
      c.travel_penalty_min + " min",
      c.open_orders,
    ].forEach((value) => tr.appendChild(el("td", String(value))));
    zoneBody.appendChild(tr);
  });

  // demand outlook: one row of boxes per zone, one box per hour
  const outlook = $("outlook");
  outlook.innerHTML = "";
  outlook.appendChild(el("span", ""));
  data.outlook[0].points.forEach((p) => outlook.appendChild(el("span", p.label.slice(0, 2))));
  const peak = Math.max(...data.outlook.flatMap((row) => row.points.map((p) => p.value)));
  data.outlook.forEach((row, i) => {
    outlook.appendChild(el("span", row.zone_name + (i === 0 ? " (you)" : ""), "row-label"));
    row.points.forEach((p) => {
      const box = el("i", "", "box lv" + level(p.value, peak));
      setTip(box, "Demand " + p.value.toFixed(2), row.zone_name + " at " + p.label);
      outlook.appendChild(box);
    });
  });
}

function renderFeed(data) {
  const feed = $("feed");
  feed.innerHTML = "";
  const mine = (data.my_events || []).map((e) => ({ ...e, mine: true }));
  const items = mine.concat(data.city_events);
  items.forEach((e) => {
    const li = el("li", undefined, e.mine ? "mine" : "");
    li.appendChild(el("span", e.time, "feed-time"));
    li.appendChild(el("span", e.mine ? "you" : e.kind, "feed-kind"));
    li.appendChild(el("span", e.text));
    feed.appendChild(li);
  });
  $("noFeed").classList.toggle("hidden", items.length > 0);
}

function render(data) {
  const wasStarted = lastState && lastState.started;
  lastState = data;
  renderStatus(data);
  renderLocation(data.position);
  renderMap(data);
  renderBusyPlace(data.busy_place);
  ["dashboard", "kpis", "recBody", "endBtn"].forEach((id) =>
    $(id).classList.toggle("hidden", !data.started)
  );
  $("recEmpty").classList.toggle("hidden", data.started);
  renderGoalCard(data);
  if (data.started) {
    renderShift(data);
    renderFeed(data);
    if (!wasStarted) refreshHistory();
  }
}

// With a shift running, the goal card shrinks to one line until "Edit goal" is pressed.
function renderGoalCard(data) {
  const summary = data.started && !goalEditing;
  // while a shift runs, only the target, hours and vehicle can change
  $("goalForm").classList.toggle("shift-running", data.started);
  $("startBtn").textContent = data.started ? "Update goal" : "Start shift";
  $("hoursLabel").textContent = data.started ? "Hours left from now" : "Hours available";
  $("goalSummary").classList.toggle("hidden", !data.started);
  $("goalForm").classList.toggle("hidden", summary);
  $("goalEdit").classList.toggle("hidden", !summary);
  if (data.started) {
    const m = data.metrics;
    $("goalSummaryText").textContent =
      rs(m.target_earnings) + " goal · " + m.remaining_hours.toFixed(1) + " h left";
  }
}
function setGoalEditing(on) {
  goalEditing = on;
  if (on && lastState && lastState.started) {
    const m = lastState.metrics;
    $("targetEarnings").value = m.target_earnings;
    $("availableHours").value = m.remaining_hours; // while a shift runs, this is hours from now
    $("earnedSoFar").value = 0;
    $("hoursElapsed").value = 0;
  }
  if (lastState) renderGoalCard(lastState);
}

async function poll(force) {
  if (!token || (agentHold && !force)) return;
  if (polling) {
    if (!force) return;
    // a refresh is already on its way; wait for it, then fetch again so nothing is stale
    while (polling) await new Promise((resolve) => setTimeout(resolve, 40));
  }
  polling = true;
  try {
    render(await apiGet("/api/state", true));
    if ($("toast").dataset.offline) {
      delete $("toast").dataset.offline;
      hideToast();
    }
  } catch (err) {
    if (token) {
      $("toast").dataset.offline = "1";
      showToast("Lost connection to the GigPilot backend - retrying...", true, true);
    }
  } finally {
    polling = false;
  }
}

// ------------------------------------------------------------ busy places

function renderBusyPlace(busy) {
  const note = $("busyActive");
  note.classList.toggle("hidden", !busy);
  if (busy) {
    note.textContent =
      busy.name + " is the busy place now (" + busy.zone_name + " zone, until " + busy.until + ")";
  }
}

async function refreshPlaces() {
  if (!token) return;
  try {
    places = await apiGet("/api/places", true);
  } catch (err) {
    return;
  }
  const options = $("placeOptions");
  if (!options.children.length) {
    places
      .map((p) => p.name)
      .sort()
      .forEach((name) => {
        const opt = el("option");
        opt.value = name;
        options.appendChild(opt);
      });
  }
  const list = $("placeList");
  list.innerHTML = "";
  places.slice(0, 8).forEach((p) => {
    const li = el("li", undefined, p.active ? "is-active" : "");
    const left = el("span", p.name + " ");
    left.appendChild(
      el("span", p.zone_name + " zone · " + p.category + (p.active ? " · busy now" : ""), "sub")
    );
    const boxes = el("span", undefined, "place-boxes");
    boxes.title = "Busy level " + p.busy_now.toFixed(1) + " of 5";
    for (let i = 1; i <= 5; i++) {
      boxes.appendChild(el("i", "", p.busy_now >= i - 0.5 ? "lv4" : "lv0"));
    }
    li.appendChild(left);
    li.appendChild(boxes);
    list.appendChild(li);
  });
}

async function setBusyPlace(placeId) {
  await apiPost("/api/busy-place", { place_id: placeId });
  await poll();
  refreshPlaces();
}
$("busyForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const typed = $("busyInput").value.trim().toLowerCase();
  const match =
    places.find((p) => p.name.toLowerCase() === typed) ||
    places.find((p) => p.name.toLowerCase().includes(typed));
  if (!match) {
    showToast("That place is not in the list - pick one of the suggestions.", true);
    return;
  }
  $("busyInput").value = match.name;
  await setBusyPlace(match.id);
  showToast(match.name + " set as the busy place.");
});
$("busyClear").addEventListener("click", async () => {
  $("busyInput").value = "";
  await setBusyPlace(null);
});

// ------------------------------------------------------ earnings activity

async function refreshActivity() {
  if (!token) return;
  let a;
  try {
    a = await apiGet("/api/activity", true);
  } catch (err) {
    return;
  }
  $("aToday").textContent = rs(a.today_total);
  $("aWeek").textContent = rs(a.week_total);
  $("aBest").textContent = a.best_hour ? a.best_hour.label + " · " + rs(a.best_hour.amount) : "-";
  $("aSlow").textContent =
    a.slowest_hour ? a.slowest_hour.label + " · " + rs(a.slowest_hour.amount) : "-";
  $("aMarket").textContent =
    "Across all riders in the partner order data, the busiest window last week was " +
    a.market.best_window + " and the busiest zone was " + a.market.best_zone + ".";

  // daily grid: one column per week (Monday first), like a contribution calendar
  const dayGrid = $("dayGrid");
  const months = $("dayMonths");
  dayGrid.innerHTML = "";
  months.innerHTML = "";
  const dayMax = Math.max(...a.days.map((d) => d.amount));
  let lastMonth = "";
  a.days.forEach((d, i) => {
    const box = el("i", "", "box lv" + level(d.amount, dayMax));
    setTip(box, d.amount ? rs(d.amount) : "No earnings",
      d.label + (d.amount ? " · " + d.entries + " orders" : ""));
    dayGrid.appendChild(box);
    if (i % 7 === 0) {
      const month = d.label.split(" ")[2];
      months.appendChild(el("span", month !== lastMonth ? month : ""));
      lastMonth = month;
    }
  });

  // hour grid: one row per day, one box per hour of the day
  const hourGrid = $("hourGrid");
  hourGrid.innerHTML = "";
  hourGrid.appendChild(el("span", ""));
  for (let h = 0; h < 24; h++) {
    hourGrid.appendChild(el("span", h % 3 === 0 ? String(h).padStart(2, "0") : "", "hour-head"));
  }
  const hourMax = Math.max(...a.hourly.flatMap((row) => row.hours));
  a.hourly.forEach((row) => {
    hourGrid.appendChild(
      el("span", row.label, "row-label" + (row.label === "Today" ? " today" : ""))
    );
    row.hours.forEach((amount, h) => {
      const box = el("i", "", "box lv" + level(amount, hourMax));
      const orders = row.entries[h];
      setTip(box, amount ? rs(amount) : "No earnings",
        row.label + ", " + hourRange(h) + (amount ? " · " + orders + " orders" : ""));
      hourGrid.appendChild(box);
    });
  });

  const zoneList = $("zoneList");
  zoneList.innerHTML = "";
  a.zones.forEach((z) => {
    const li = el("li");
    const left = el("span", z.zone_name + " ");
    left.appendChild(el("span", z.entries + " entries · " + z.share_pct + "%", "sub"));
    li.appendChild(left);
    li.appendChild(el("span", rs(z.amount), "amount"));
    zoneList.appendChild(li);
  });
  $("noZones").classList.toggle("hidden", a.zones.length > 0);

  $("syncNote").textContent =
    a.total_orders.toLocaleString("en-IN") + " orders synced automatically from your " +
    "delivery apps - nothing to enter by hand.";
  const platformList = $("platformList");
  platformList.innerHTML = "";
  a.platforms.forEach((p) => {
    const li = el("li");
    const left = el("span");
    left.appendChild(el("span", p.name, "app-tag"));
    left.appendChild(el("span", p.orders + " orders · " + p.share_pct + "%", "sub"));
    li.appendChild(left);
    li.appendChild(el("span", rs(p.amount), "amount"));
    platformList.appendChild(li);
  });

  const recent = $("recentList");
  recent.innerHTML = "";
  a.recent.forEach((e) => {
    const li = el("li");
    const left = el("span");
    left.appendChild(el("span", e.platform || "Earlier", "app-tag"));
    const what =
      e.kind === "incentive" ? "Incentive bonus"
      : e.kind === "manual" ? "Logged by hand" + (e.note ? " - " + e.note : "")
      : e.merchant || "Order";
    left.appendChild(document.createTextNode(what + " "));
    const where = [e.date.slice(5) + " " + e.time];
    if (e.zone_name) where.push(e.zone_name);
    if (e.distance_km) where.push(e.distance_km + " km");
    left.appendChild(el("span", where.join(" · "), "sub"));
    li.appendChild(left);
    li.appendChild(el("span", "+" + rs(e.amount), "amount"));
    recent.appendChild(li);
  });
}

async function refreshHistory() {
  let history;
  try {
    history = await apiGet("/api/history", true);
  } catch (err) {
    return;
  }
  const body = $("historyBody");
  body.innerHTML = "";
  $("noHistory").classList.toggle("hidden", history.length > 0);
  history.forEach((h) => {
    const tr = el("tr");
    [h.date.slice(5) + " " + h.time, h.action, h.outcome, h.confidence + "%"].forEach((value) =>
      tr.appendChild(el("td", String(value)))
    );
    body.appendChild(tr);
  });
}

// ------------------------------------------------------------------ agent
// Type or speak an instruction. The backend (a Gemini AI agent, or built-in
// rules without a key) works out the steps and carries them out, and reports
// each step back. The page then acts those steps out where the rider can see
// them: a pointer travels to the field or button, types, presses, scrolls.
// The work itself is already done on the server; this shows what was done.

const AGENT_SECTIONS = {
  map: "map", activity: "dayGrid", busy: "busyForm", history: "historyBody", feed: "feed",
  zones: "zoneCompareBody", goal: "goalCard", recommendation: "recAction",
};
// ---- languages: English, Telugu, Kannada ---------------------------------
// The agent listens, replies and narrates its steps in the chosen language.
// Each entry: the English text (as a pattern), then its Telugu and Kannada forms.
const SPEECH_CODE = { en: "en-IN", te: "te-IN", kn: "kn-IN" };
const RUPEES = { en: "rupees", te: "రూపాయలు", kn: "ರೂಪಾಯಿ" };
const AGENT_CHIPS = {
  en: ["Set my goal to 1500 in 6 hours", "Where should I go?", "Charminar is busy, take me there",
    "How much have I earned?"],
  te: ["నా లక్ష్యం 1500, 6 గంటల్లో", "ఎక్కడికి వెళ్ళాలి?",
    "Charminar బిజీగా ఉంది, నన్ను అక్కడికి తీసుకెళ్ళు", "ఎంత సంపాదించాను?"],
  kn: ["ನನ್ನ ಗುರಿ 1500, 6 ಗಂಟೆಗಳಲ್ಲಿ", "ಎಲ್ಲಿಗೆ ಹೋಗಬೇಕು?",
    "Charminar ಬ್ಯುಸಿ ಇದೆ, ನನ್ನನ್ನು ಅಲ್ಲಿಗೆ ಕರೆದುಕೊಂಡು ಹೋಗು", "ಎಷ್ಟು ಗಳಿಸಿದ್ದೇನೆ?"],
};
const TRANSLATIONS = [
  [/^Thinking about: (.*)$/, "ఆలోచిస్తున్నాను: $1", "ಯೋಚಿಸುತ್ತಿದ್ದೇನೆ: $1"],
  [/^Reading your current status$/, "మీ ప్రస్తుత స్థితిని చూస్తున్నాను", "ನಿಮ್ಮ ಈಗಿನ ಸ್ಥಿತಿಯನ್ನು ನೋಡುತ್ತಿದ್ದೇನೆ"],
  [/^Opening the goal form$/, "లక్ష్యం ఫారం తెరుస్తున్నాను", "ಗುರಿ ಫಾರ್ಮ್ ತೆರೆಯುತ್ತಿದ್ದೇನೆ"],
  [/^Typing the target: Rs (.*)$/, "లక్ష్యం టైప్ చేస్తున్నాను: Rs $1", "ಗುರಿ ಟೈಪ್ ಮಾಡುತ್ತಿದ್ದೇನೆ: Rs $1"],
  [/^Setting the hours: (.*)$/, "గంటలు సెట్ చేస్తున్నాను: $1", "ಗಂಟೆಗಳನ್ನು ಹೊಂದಿಸುತ್ತಿದ್ದೇನೆ: $1"],
  [/^Choosing the vehicle: (.*)$/, "వాహనం ఎంచుకుంటున్నాను: $1", "ವಾಹನ ಆಯ್ಕೆ ಮಾಡುತ್ತಿದ್ದೇನೆ: $1"],
  [/^Pressing (.*)$/, "$1 నొక్కుతున్నాను", "$1 ಒತ್ತುತ್ತಿದ್ದೇನೆ"],
  [/^Accepting the recommendation$/, "సిఫార్సును అంగీకరిస్తున్నాను", "ಶಿಫಾರಸನ್ನು ಒಪ್ಪಿಕೊಳ್ಳುತ್ತಿದ್ದೇನೆ"],
  [/^Ignoring the recommendation$/, "సిఫార్సును విస్మరిస్తున్నాను", "ಶಿಫಾರಸನ್ನು ನಿರ್ಲಕ್ಷಿಸುತ್ತಿದ್ದೇನೆ"],
  [/^Recommendation answered$/, "సిఫార్సుకు సమాధానం ఇచ్చారు", "ಶಿಫಾರಸಿಗೆ ಉತ್ತರಿಸಲಾಗಿದೆ"],
  [/^Cancelling the move$/, "ప్రయాణాన్ని రద్దు చేస్తున్నాను", "ಪ್ರಯಾಣವನ್ನು ರದ್ದುಮಾಡುತ್ತಿದ್ದೇನೆ"],
  [/^Ending the shift$/, "షిఫ్ట్ ముగిస్తున్నాను", "ಶಿಫ್ಟ್ ಮುಗಿಸುತ್ತಿದ್ದೇನೆ"],
  [/^Typing the busy place: (.*)$/, "రద్దీ ప్రదేశం టైప్ చేస్తున్నాను: $1", "ಜನಸಂದಣಿ ಸ್ಥಳ ಟೈಪ್ ಮಾಡುತ್ತಿದ್ದೇನೆ: $1"],
  [/^Busy place is set$/, "రద్దీ ప్రదేశం సెట్ అయింది", "ಜನಸಂದಣಿ ಸ್ಥಳ ಹೊಂದಿಸಲಾಗಿದೆ"],
  [/^Waiting for a busy place$/, "రద్దీ ప్రదేశం కోసం వేచి ఉన్నాను", "ಜನಸಂದಣಿ ಸ್ಥಳಕ್ಕಾಗಿ ಕಾಯುತ್ತಿದ್ದೇನೆ"],
  [/^Clearing the busy place$/, "రద్దీ ప్రదేశాన్ని తొలగిస్తున్నాను", "ಜನಸಂದಣಿ ಸ್ಥಳವನ್ನು ತೆಗೆಯುತ್ತಿದ್ದೇನೆ"],
  [/^Reading the busiest places$/, "అత్యంత రద్దీ ప్రదేశాలను చూస్తున్నాను", "ಹೆಚ್ಚು ಜನಸಂದಣಿಯ ಸ್ಥಳಗಳನ್ನು ನೋಡುತ್ತಿದ್ದೇನೆ"],
  [/^Finding (.*) on the map$/, "మ్యాప్‌లో $1 వెతుకుతున్నాను", "ನಕ್ಷೆಯಲ್ಲಿ $1 ಹುಡುಕುತ್ತಿದ್ದೇನೆ"],
  [/^Comparing the zones$/, "జోన్లను పోల్చుతున్నాను", "ವಲಯಗಳನ್ನು ಹೋಲಿಸುತ್ತಿದ್ದೇನೆ"],
  [/^Reading your earnings activity$/, "మీ సంపాదన వివరాలు చూస్తున్నాను", "ನಿಮ್ಮ ಗಳಿಕೆಯ ವಿವರ ನೋಡುತ್ತಿದ್ದೇನೆ"],
  [/^Reading your latest orders$/, "మీ తాజా ఆర్డర్లు చూస్తున్నాను", "ನಿಮ್ಮ ಇತ್ತೀಚಿನ ಆರ್ಡರ್‌ಗಳನ್ನು ನೋಡುತ್ತಿದ್ದೇನೆ"],
  [/^Opening Google Maps directions$/, "Google Maps దారి తెరుస్తున్నాను", "Google Maps ದಾರಿ ತೆರೆಯುತ್ತಿದ್ದೇನೆ"],
  [/^Showing (.*)$/, "$1 చూపిస్తున్నాను", "$1 ತೋರಿಸುತ್ತಿದ್ದೇನೆ"],
  [/^Choosing (.*)$/, "$1 ఎంచుకుంటున్నాను", "$1 ಆಯ್ಕೆ ಮಾಡುತ್ತಿದ್ದೇನೆ"],
  [/^Triggering the demo event$/, "డెమో ఈవెంట్ ప్రారంభిస్తున్నాను", "ಡೆಮೊ ಈವೆಂಟ್ ಪ್ರಾರಂಭಿಸುತ್ತಿದ್ದೇನೆ"],
  [/^Updating your location$/, "మీ లొకేషన్ అప్‌డేట్ చేస్తున్నాను", "ನಿಮ್ಮ ಸ್ಥಳವನ್ನು ನವೀಕರಿಸುತ್ತಿದ್ದೇನೆ"],
  [/^Logging out$/, "లాగ్ అవుట్ చేస్తున్నాను", "ಲಾಗ್ ಔಟ್ ಮಾಡುತ್ತಿದ್ದೇನೆ"],
  [/^Checking with you before changing anything$/, "ఏదైనా మార్చే ముందు మిమ్మల్ని అడుగుతున్నాను",
    "ಏನನ್ನಾದರೂ ಬದಲಿಸುವ ಮೊದಲು ನಿಮ್ಮನ್ನು ಕೇಳುತ್ತಿದ್ದೇನೆ"],
  [/^Tell the agent what to do - it will work the screen for you$/,
    "ఏం చేయాలో ఏజెంట్‌కు చెప్పండి - అది మీ కోసం స్క్రీన్‌పై పని చేస్తుంది",
    "ಏನು ಮಾಡಬೇಕೆಂದು ಏಜೆಂಟ್‌ಗೆ ಹೇಳಿ - ಅದು ನಿಮಗಾಗಿ ಪರದೆಯ ಮೇಲೆ ಕೆಲಸ ಮಾಡುತ್ತದೆ"],
  [/^Listening\.\.\.$/, "వింటున్నాను...", "ಕೇಳುತ್ತಿದ್ದೇನೆ..."],
  [/^I could not reach GigPilot\. Is the server running\?$/,
    "GigPilot‌ను చేరుకోలేకపోయాను. సర్వర్ నడుస్తోందా?", "GigPilot ತಲುಪಲು ಆಗಲಿಲ್ಲ. ಸರ್ವರ್ ಚಾಲನೆಯಲ್ಲಿದೆಯೇ?"],
  [/^I need microphone permission to hear you\. Allow it in the browser, or type instead\.$/,
    "మీ మాట వినడానికి మైక్రోఫోన్ అనుమతి కావాలి. బ్రౌజర్‌లో అనుమతించండి, లేదా టైప్ చేయండి.",
    "ನಿಮ್ಮ ಮಾತು ಕೇಳಲು ಮೈಕ್ರೊಫೋನ್ ಅನುಮತಿ ಬೇಕು. ಬ್ರೌಸರ್‌ನಲ್ಲಿ ಅನುಮತಿಸಿ, ಅಥವಾ ಟೈಪ್ ಮಾಡಿ."],
  [/^I could not hear that\. Try again, or type it\.$/,
    "అది వినపడలేదు. మళ్ళీ ప్రయత్నించండి, లేదా టైప్ చేయండి.", "ಅದು ಕೇಳಿಸಲಿಲ್ಲ. ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ, ಅಥವಾ ಟೈಪ್ ಮಾಡಿ."],
  [/^New chat started\. What should I do\?$/, "కొత్త చాట్ మొదలైంది. నేను ఏం చేయాలి?",
    "ಹೊಸ ಚಾಟ್ ಆರಂಭವಾಗಿದೆ. ನಾನು ಏನು ಮಾಡಬೇಕು?"],
  [/^The AI agent is unavailable right now \((.*)\), so this was handled in basic mode, which answers in English\.$/,
    "AI ఏజెంట్ ఇప్పుడు అందుబాటులో లేదు ($1). అందుకే బేసిక్ మోడ్‌లో చేశాను; అది ఇంగ్లీష్‌లో సమాధానం ఇస్తుంది.",
    "AI ಏಜೆಂಟ್ ಈಗ ಲಭ್ಯವಿಲ್ಲ ($1). ಆದ್ದರಿಂದ ಬೇಸಿಕ್ ಮೋಡ್‌ನಲ್ಲಿ ಮಾಡಿದೆ; ಅದು ಇಂಗ್ಲಿಷ್‌ನಲ್ಲಿ ಉತ್ತರಿಸುತ್ತದೆ."],
  [/^This device has no voice for this language, so replies are shown but not spoken\.$/,
    "ఈ పరికరంలో తెలుగు వాయిస్ లేదు, కాబట్టి సమాధానాలు కనిపిస్తాయి కానీ వినిపించవు.",
    "ಈ ಸಾಧನದಲ್ಲಿ ಕನ್ನಡ ಧ್ವನಿ ಇಲ್ಲ, ಆದ್ದರಿಂದ ಉತ್ತರಗಳು ಕಾಣಿಸುತ್ತವೆ ಆದರೆ ಕೇಳಿಸುವುದಿಲ್ಲ."],
  [/^Run$/, "నడుపు", "ನಡೆಸು"],
  [/^Skip$/, "దాటవేయి", "ಬಿಟ್ಟುಬಿಡು"],
];
let agentLang = "en";
try { agentLang = localStorage.getItem("gigpilot_lang") || "en"; } catch (err) { /* private window */ }
if (!SPEECH_CODE[agentLang]) agentLang = "en";

// English text -> the same text in the agent's current language.
function loc(text) {
  if (agentLang === "en") return text;
  for (const [pattern, te, kn] of TRANSLATIONS) {
    if (pattern.test(text)) return text.replace(pattern, agentLang === "te" ? te : kn);
  }
  return text;
}

function applyAgentLanguage() {
  $("asstLang").value = agentLang;
  $("asstInput").placeholder = loc("Tell the agent what to do - it will work the screen for you");
  $("asstSend").textContent = loc("Run");
  $("agentStop").textContent = loc("Skip");
  $("asstChips").innerHTML = "";
  AGENT_CHIPS[agentLang].forEach((text) => {
    const chip = el("button", text, "asst-chip");
    chip.type = "button";
    chip.addEventListener("click", () => agentRun(text));
    $("asstChips").appendChild(chip);
  });
}

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let listening = false;
let agentSkip = false;
let lastProblem = null;
let replyTimer = null;

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, agentSkip ? 0 : ms));
const isShown = (node) => Boolean(node) && node.offsetParent !== null;

function setStep(text) {
  $("agentStep").textContent = loc(text);
}

// Bring an element into view, send the pointer to it and ring it.
async function pointAt(node, label) {
  if (!isShown(node)) return false;
  setStep(label);
  node.scrollIntoView({ behavior: agentSkip ? "auto" : "smooth", block: "center" });
  await pause(480);
  const box = node.getBoundingClientRect();
  const cursor = $("agentCursor");
  cursor.classList.remove("hidden");
  cursor.style.transform =
    "translate(" + (box.left + Math.min(box.width / 2, 70)) + "px, " +
    (box.top + box.height / 2) + "px)";
  await pause(620);
  node.classList.add("agent-focus");
  return true;
}
async function typeInto(node, value, label) {
  if (!(await pointAt(node, label))) return;
  node.value = "";
  for (const ch of String(value)) {
    node.value += ch;
    await pause(34);
  }
  await pause(320);
  node.classList.remove("agent-focus");
}
async function choose(node, value, label) {
  if (!(await pointAt(node, label))) return;
  node.value = value;
  await pause(420);
  node.classList.remove("agent-focus");
}
async function press(node, label) {
  if (!(await pointAt(node, label))) return false;
  node.classList.add("agent-press");
  await pause(300);
  node.classList.remove("agent-press", "agent-focus");
  return true;
}
async function lookAt(node, label, ms) {
  if (!(await pointAt(node, label))) return false;
  await pause(ms || 1000);
  node.classList.remove("agent-focus");
  return true;
}

async function refreshAll() {
  await poll(true);
  refreshPlaces();
  refreshActivity();
  refreshHistory();
}
const zoneNamed = (name) =>
  zones.find((z) => name && z.name.toLowerCase() === String(name).toLowerCase()) ||
  zones.find((z) => name && String(name).toLowerCase().includes(z.name.toLowerCase()));

// One tool the agent used -> the same thing acted out on the page.
async function actOut(step) {
  const a = step.args || {};
  if (step.ok === false) {
    // the step did not go through (for example a goal that needs confirming): show nothing
    // being changed, rather than acting out something that did not happen
    setStep("Checking with you before changing anything");
    await pause(900);
    return;
  }
  switch (step.tool) {
    case "get_status":
      await lookAt(isShown($("kpis")) ? $("kpis") : document.querySelector(".here-card"),
        "Reading your current status", 800);
      break;
    case "set_goal":
      if (isShown($("goalEdit"))) {
        await press($("goalEdit"), "Opening the goal form");
        setGoalEditing(true);
      }
      if (a.target_earnings !== undefined) {
        await typeInto($("targetEarnings"), a.target_earnings, "Typing the target: Rs " + a.target_earnings);
      }
      if (a.available_hours !== undefined) {
        await typeInto($("availableHours"), a.available_hours, "Setting the hours: " + a.available_hours);
      }
      if (a.vehicle) await choose($("vehicle"), a.vehicle, "Choosing the vehicle: " + a.vehicle);
      await press($("startBtn"), "Pressing " + $("startBtn").textContent);
      setGoalEditing(false);
      await refreshAll();
      break;
    case "answer_recommendation": {
      const accept = a.decision === "accept";
      const pressed = await press($(accept ? "acceptBtn" : "ignoreBtn"),
        accept ? "Accepting the recommendation" : "Ignoring the recommendation");
      await refreshAll();
      if (!pressed) await lookAt($("recAction"), "Recommendation answered", 700);
      break;
    }
    case "cancel_move":
      await press($("cancelMoveBtn"), "Cancelling the move");
      await refreshAll();
      break;
    case "end_shift":
      await press($("endBtn"), "Ending the shift");
      await refreshAll();
      break;
    case "set_busy_place":
      if (a.place) {
        await typeInto($("busyInput"), a.place, "Typing the busy place: " + a.place);
        await press(document.querySelector("#busyForm button[type=submit]"), "Pressing Set");
        await refreshAll();
        if (lastState && lastState.busy_place) $("busyInput").value = lastState.busy_place.name;
        await lookAt($("busyActive"), "Busy place is set", 700);
      } else {
        await lookAt($("busyInput"), "Waiting for a busy place", 700);
      }
      break;
    case "clear_busy_place":
      await press($("busyClear"), "Clearing the busy place");
      $("busyInput").value = "";
      await refreshAll();
      break;
    case "list_busy_places":
      await lookAt($("placeList"), "Reading the busiest places");
      break;
    case "zone_info": {
      const zone = zoneNamed(a.zone);
      await lookAt($("map"), "Finding " + (zone ? zone.name : "the zone") + " on the map", 500);
      if (zone && map) {
        map.flyTo([zone.lat, zone.lon], 13, { duration: agentSkip ? 0 : 0.8 });
        await pause(900);
        zoneMarkers[zone.id].openTooltip();
        await pause(1500);
        zoneMarkers[zone.id].closeTooltip();
      }
      break;
    }
    case "top_zones":
      await lookAt($("zoneCompareBody").closest("table"), "Comparing the zones");
      break;
    case "earnings_summary":
      await lookAt($("hourGrid"), "Reading your earnings activity");
      break;
    case "recent_orders":
      await lookAt($("recentList"), "Reading your latest orders");
      break;
    case "open_directions":
      await refreshAll();
      if (!(await lookAt($("recMapsLink"), "Opening Google Maps directions", 800))) {
        await lookAt($("map"), "Opening Google Maps directions", 800);
      }
      break;
    case "show_section": {
      const target = $(AGENT_SECTIONS[a.section]);
      await lookAt(target && (target.closest(".card") || target), "Showing " + a.section);
      break;
    }
    case "simulate_event": {
      const tools = document.querySelector(".demo-tools");
      if (tools) tools.open = true;
      const zone = zoneNamed(a.zone);
      if (zone) await choose($("eventZone"), zone.id, "Choosing " + zone.name);
      const button = { traffic: "trafficBtn", rain: "rainBtn", incentive: "incentiveBtn", reset: "resetBtn" }[a.kind];
      await press($(button), "Triggering the demo event");
      await refreshAll();
      break;
    }
    case "set_location":
      await refreshAll();
      await lookAt(document.querySelector(".here-card"), "Updating your location", 800);
      break;
    case "log_out":
      await press($("logoutBtn"), "Logging out");
      break;
    default:
      break;
  }
}

async function actOutAll(trace) {
  agentHold = true; // the page stays as it was until each step is shown
  for (const step of trace) {
    try {
      await actOut(step);
    } catch (err) { /* a missing element must not stop the remaining steps */ }
  }
  agentHold = false;
  $("agentCursor").classList.add("hidden");
  document.querySelectorAll(".agent-focus, .agent-press").forEach((n) =>
    n.classList.remove("agent-focus", "agent-press")
  );
  await refreshAll();
}

function agentWorking(on) {
  document.body.classList.toggle("agent-working", on);
  $("agentBanner").classList.toggle("hidden", !on);
  $("asstSend").disabled = on;
  if (!on) $("agentCursor").classList.add("hidden");
}

function agentLog(text, who, link) {
  const msg = el("div", text, "asst-msg " + who);
  if (link) {
    msg.appendChild(el("br"));
    const a = el("a", link.label);
    a.href = link.url;
    a.target = "_blank";
    a.rel = "noopener";
    msg.appendChild(a);
  }
  $("asstLog").appendChild(msg);
  $("asstLog").scrollTop = $("asstLog").scrollHeight;
}

function showReply(text, link) {
  const box = $("agentReply");
  box.innerHTML = "";
  box.appendChild(el("span", text));
  if (link) {
    const a = el("a", link.label);
    a.href = link.url;
    a.target = "_blank";
    a.rel = "noopener";
    box.appendChild(a);
  }
  const close = el("button", "×", "asst-x");
  close.type = "button";
  close.setAttribute("aria-label", "Dismiss");
  close.addEventListener("click", () => box.classList.add("hidden"));
  box.appendChild(close);
  box.classList.remove("hidden");
  clearTimeout(replyTimer);
  replyTimer = setTimeout(() => box.classList.add("hidden"), link ? 30000 : 14000);
}

const noVoiceTold = {};
function speak(text, lang) {
  if (!$("asstSpeak").checked || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const code = SPEECH_CODE[lang] || "en-IN";
  const voices = window.speechSynthesis.getVoices();
  const voice = voices.find((v) => v.lang.replace("_", "-").toLowerCase().startsWith(code.slice(0, 2)));
  if (!voice && lang !== "en" && voices.length) {
    // reading Telugu or Kannada with an English voice is gibberish: say nothing instead
    if (!noVoiceTold[lang]) {
      noVoiceTold[lang] = true;
      agentLog(loc("This device has no voice for this language, so replies are shown but not spoken."), "bot");
    }
    return;
  }
  const utterance = new SpeechSynthesisUtterance(text.replace(/\bRs\b\.?/g, RUPEES[lang] || "rupees"));
  utterance.lang = code;
  if (voice) utterance.voice = voice;
  window.speechSynthesis.speak(utterance);
}

function showEngine(engine) {
  const badge = $("asstEngine");
  const ai = engine === "gemini";
  badge.textContent = ai ? "AI · Gemini" : "Basic mode";
  badge.classList.toggle("ai", ai);
  badge.title = ai
    ? "Instructions are understood and carried out by a Gemini AI agent"
    : "Built-in rules. Add a Gemini key in website/backend/.env to turn on the AI agent";
}

async function agentRun(text) {
  text = text.trim();
  if (!text || document.body.classList.contains("agent-working")) return;
  agentSkip = false;
  $("agentReply").classList.add("hidden");
  $("asstChips").classList.add("hidden");
  agentLog(text, "you");
  setStep("Thinking about: " + text);
  agentWorking(true);
  agentHold = true; // freeze the page now, so changes appear only as each step is shown
  let result;
  try {
    result = await apiPost("/api/assistant", { text, lang: agentLang }, true);
  } catch (err) {
    agentHold = false;
    agentWorking(false);
    showReply(loc("I could not reach GigPilot. Is the server running?"));
    return;
  }
  showEngine(result.engine);
  if (result.problem && result.problem !== lastProblem) {
    agentLog(loc("The AI agent is unavailable right now (" + result.problem + "), so this was " +
      "handled in basic mode, which answers in English."), "bot");
  }
  lastProblem = result.problem;

  const link = result.actions.find((action) => action.type === "open_url") || null;
  if (link) window.open(link.url, "_blank", "noopener"); // may be blocked; a link is shown too
  if (result.trace.length) {
    await actOutAll(result.trace);
  } else {
    agentHold = false;
    const scroll = result.actions.find((action) => action.type === "scroll");
    const target = scroll && $(AGENT_SECTIONS[scroll.section]);
    if (target) (target.closest(".card") || target).scrollIntoView({ behavior: "smooth", block: "center" });
    await refreshAll();
  }
  agentWorking(false);
  agentLog(result.reply, "bot", link);
  showReply(result.reply, link);
  speak(result.reply, result.lang || "en");
  if (result.actions.some((action) => action.type === "logout") &&
      !result.trace.some((step) => step.tool === "log_out")) {
    setTimeout(() => $("logoutBtn").click(), 1200);
  } else if (result.trace.some((step) => step.tool === "log_out")) {
    setTimeout(() => $("logoutBtn").click(), 900);
  }
}

function resetAgent() {
  if (recognition && listening) recognition.stop();
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  agentWorking(false);
  agentHold = false;
  $("asstLog").innerHTML = "";
  $("agentSheet").classList.add("hidden");
  $("agentReply").classList.add("hidden");
  $("asstChips").classList.remove("hidden");
}

function initAgent() {
  applyAgentLanguage();
  if (!Recognition) {
    $("asstMic").disabled = true;
    $("asstMic").title = "Voice needs Chrome or Edge";
  }
  apiGet("/api/assistant/status", true).then((status) => showEngine(status.engine)).catch(() => {});
}

$("asstForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("asstInput").value;
  $("asstInput").value = "";
  agentRun(text);
});
$("agentStop").addEventListener("click", () => {
  agentSkip = true; // finish the remaining steps at once
});
$("asstToggle").addEventListener("click", () => $("agentSheet").classList.toggle("hidden"));
$("asstClose").addEventListener("click", () => $("agentSheet").classList.add("hidden"));
$("asstNew").addEventListener("click", async () => {
  try { await apiPost("/api/assistant/reset", undefined, true); } catch (err) { /* offline */ }
  $("asstLog").innerHTML = "";
  $("asstChips").classList.remove("hidden");
  showReply(loc("New chat started. What should I do?"));
});

$("asstLang").addEventListener("change", () => {
  agentLang = $("asstLang").value;
  try { localStorage.setItem("gigpilot_lang", agentLang); } catch (err) { /* private window */ }
  if (recognition && listening) recognition.stop();
  applyAgentLanguage();
  $("asstChips").classList.remove("hidden");
});

$("asstMic").addEventListener("click", () => {
  if (!Recognition) return;
  if (listening) {
    recognition.stop();
    return;
  }
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  recognition = new Recognition();
  recognition.lang = SPEECH_CODE[agentLang]; // listen in the chosen language
  recognition.interimResults = true;
  let heard = "";
  recognition.onstart = () => {
    listening = true;
    $("asstMic").classList.add("listening");
    $("asstInput").placeholder = loc("Listening...");
  };
  recognition.onresult = (event) => {
    heard = Array.from(event.results).map((r) => r[0].transcript).join(" ");
    $("asstInput").value = heard;
  };
  recognition.onerror = (event) => {
    const denied = event.error === "not-allowed" || event.error === "service-not-allowed";
    showReply(loc(
      denied
        ? "I need microphone permission to hear you. Allow it in the browser, or type instead."
        : "I could not hear that. Try again, or type it."
    ));
  };
  recognition.onend = () => {
    listening = false;
    $("asstMic").classList.remove("listening");
    $("asstInput").placeholder = loc("Tell the agent what to do - it will work the screen for you");
    if (heard.trim()) {
      $("asstInput").value = "";
      agentRun(heard);
    }
  };
  recognition.start();
});

// nav links scroll to their section
document.querySelectorAll("[data-go]").forEach((button) =>
  button.addEventListener("click", () => {
    const target = $(button.dataset.go);
    if (target) (target.closest(".card") || target).scrollIntoView({ behavior: "smooth", block: "start" });
  })
);

// ---------------------------------------------------------------- actions

$("goalEdit").addEventListener("click", () => setGoalEditing(true));

$("startBtn").addEventListener("click", async () => {
  const num = (id) => parseFloat($(id).value);
  const body = {
    target_earnings: num("targetEarnings"),
    available_hours: num("availableHours"),
    vehicle: $("vehicle").value,
    earned_so_far: num("earnedSoFar"),
    hours_elapsed: num("hoursElapsed"),
  };
  if (lastState && lastState.started) {
    // changing a running shift: the hours typed are hours from now
    body.hours_left = body.available_hours;
    body.available_hours = lastState.metrics.available_hours;
    body.earned_so_far = 0;
    body.hours_elapsed = 0;
  }
  try {
    await apiPost("/api/goal", body);
  } catch (err) {
    if (err.code !== "confirm_goal") throw err;
    // an amount that needs an impossible pace is usually a typing slip: ask first
    if (!window.confirm(err.message + "\n\nSet this goal anyway?")) return;
    await apiPost("/api/goal", { ...body, confirmed: true });
  }
  goalEditing = false;
  await poll();
});

$("endBtn").addEventListener("click", async () => {
  await apiPost("/api/shift/end");
  showToast("Shift ended. Your earnings stay in your activity history.");
  await poll();
  refreshActivity();
});

async function act(path, body) {
  await apiPost(path, body);
  await poll();
}
$("acceptBtn").addEventListener("click", async () => {
  await act("/api/accept");
  refreshHistory();
});
$("ignoreBtn").addEventListener("click", async () => {
  await act("/api/ignore");
  refreshHistory();
});
$("cancelMoveBtn").addEventListener("click", () => act("/api/cancel-move"));

const eventBody = () => ({ zone_id: $("eventZone").value });
$("trafficBtn").addEventListener("click", () => act("/api/simulate/traffic", eventBody()));
$("incentiveBtn").addEventListener("click", () => act("/api/simulate/incentive", eventBody()));
$("rainBtn").addEventListener("click", () => act("/api/simulate/rain", eventBody()));
$("resetBtn").addEventListener("click", () => act("/api/simulate/reset"));

init();
