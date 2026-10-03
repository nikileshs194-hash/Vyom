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
    try {
      detail = errorDetail(await res.json());
    } catch (err) { /* non-JSON error body, keep the generic message */ }
    const error = new Error(detail);
    error.status = res.status;
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
  $("appView").classList.add("hidden");
  $("navUser").classList.add("hidden");
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
  $("mProgress").textContent = m.progress_pct + "% of " + rs(m.target_earnings) + " goal";
  $("progressFill").style.width = m.progress_pct + "%";
  $("mTime").textContent = m.remaining_hours.toFixed(1) + " h";
  $("mOrders").textContent = r.orders_done + " orders delivered this shift";
  $("mPace").textContent = m.current_pace === null ? "-" : rs(m.current_pace) + "/hr";
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
      box.title = row.zone_name + " at " + p.label + ": demand index " + p.value.toFixed(2);
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
  if (data.started) {
    renderShift(data);
    renderFeed(data);
    if (!wasStarted) refreshHistory();
  }
}

async function poll() {
  if (polling || !token) return;
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
    box.title = d.label + ": " + (d.amount ? rs(d.amount) + " in " + d.entries + " entries" : "nothing tracked");
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
      const hour = String(h).padStart(2, "0") + ":00";
      box.title = row.label + " " + hour + ": " + (amount ? rs(amount) : "nothing tracked");
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

// ---------------------------------------------------------------- actions

$("startBtn").addEventListener("click", async () => {
  const num = (id) => parseFloat($(id).value);
  await apiPost("/api/goal", {
    target_earnings: num("targetEarnings"),
    available_hours: num("availableHours"),
    vehicle: $("vehicle").value,
    earned_so_far: num("earnedSoFar"),
    hours_elapsed: num("hoursElapsed"),
  });
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
