// The page: picks a site and dates, sends the half-hours to the worker, draws the result.

const $ = (id) => document.getElementById(id);
const HALF_HOUR = 1800e3;
const DAY = 86400e3;
const COLUMNS = 8;
const WIDTH = COLUMNS - 1;      // model inputs per row, without the time column
const FRAME_TIME = 120;         // ms; the shortest time a half-hour stays on screen while playing
const FILE = "file";            // the key of a loaded file among the sites
const SITE_CODE = /[A-Z]{2}-[A-Za-z0-9]{3}/;
const OWN_FILE = "A ZIP or CSV in FLUXNET format, with the columns of FLUXMET_HH. It stays in this browser: nothing is uploaded. You can also drop it on the page.";
const SHUTTLE_FILL = "#2f6db3";
// Of the FLUXNET Shuttle's hubs, only TERN sends the headers that let a web page read its files.
const HUBS = {
  ICOS: { terms: "The link accepts the ICOS data licence, CC BY 4.0." },
  AmeriFlux: { terms: "AmeriFlux shares it under CC BY 4.0." },
  TERN: { terms: "TERN shares it under CC BY 4.0.", direct: true },
};
const LINES = { 0.5: "#ffffff", 0.8: "#ffe066", 0.9: "#ff9f1c" };
// Light to dark along a yellow to red ramp, readable over aerial imagery.
const RAMP = [[255, 240, 120], [255, 190, 60], [245, 120, 40], [215, 50, 40], [140, 10, 60]];
// `reach`: how far to draw, in multiples of z - d. At four sites in October 2026 the 90% contour reached 47 to 91 with
// Kljun and 111 to 338 with Hsieh.
const MODELS = {
  kljun2015: {
    name: "Kljun et al. 2015",
    reach: 75,
    citations: ["Kljun, N., Calanca, P., Rotach, M. W., Schmid, H. P. (2015). A simple two-dimensional parameterisation for Flux Footprint Prediction (FFP). Geosci. Model Dev. 8, 3695-3713. https://doi.org/10.5194/gmd-8-3695-2015"],
  },
  hsieh2000: {
    name: "Hsieh et al. 2000",
    reach: 200,
    citations: [
      "Hsieh, C.-I., Katul, G., Chi, T. (2000). An approximate analytical model for footprint estimation of scalar fluxes in thermally stratified atmospheric flows. Adv. Water Resour. 23, 765-772. https://doi.org/10.1016/S0309-1708(99)00042-1",
      "Detto, M., Montaldo, N., Albertson, J. D., Mancini, M., Katul, G. (2006). Soil moisture and vegetation controls on evapotranspiration in a heterogeneous Mediterranean ecosystem on Sardinia, Italy. Water Resour. Res. 42, W08419. https://doi.org/10.1029/2005WR004693",
    ],
  },
};

let catalogue = { sites: {} };
let shuttle = null;         // the FLUXNET Shuttle's site list, fetched on first use
let source = "icos";        // the open tab: icos, shuttle or file
let archive = null;         // what a loaded file holds beyond its site, see useArchive
let reader = null;          // the worker reading a file
let linked = null;          // the form a shared link keeps for a site whose archive is not loaded yet
let siteId = "";
let worker;
let workerReady = false;
let workerStatus = "Starting Python";
let jobId = 0;
let current = null;         // what is on the map: { site, from, to, label, summary }
let steps = null;           // the half-hours on the timeline, see startSteps
let overlay = null;         // the density image on the map
let outlines = {};          // fraction -> contour polyline on the map
const markers = {};
const shuttleMarkers = {};
let fileMarker = null;
const years = new Map();    // "SITE/YEAR" -> Float32Array

// ---- map ----

const map = L.map("map", { worldCopyJump: true }).setView([52, 10], 4);
const backgrounds = {
  Aerial: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
  }),
  Streets: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap contributors",
  }),
};
backgrounds.Aerial.addTo(map);
Object.values(backgrounds).forEach((layer) => {
  layer.on("tileerror", () => showMessage("Some map tiles did not load. Check your connection."));
});

const switcher = L.control({ position: "topright" });
switcher.onAdd = () => {
  const box = L.DomUtil.create("div", "basemap");
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", "Background map");
  for (const [name, layer] of Object.entries(backgrounds)) {
    const button = L.DomUtil.create("button", "", box);
    button.type = "button";
    button.textContent = name;
    button.setAttribute("aria-pressed", map.hasLayer(layer));
    button.addEventListener("click", () => {
      Object.values(backgrounds).forEach((other) => other.remove());
      layer.addTo(map);
      box.querySelectorAll("button").forEach((other) => other.setAttribute("aria-pressed", other === button));
    });
  }
  L.DomEvent.disableClickPropagation(box);
  return box;
};
switcher.addTo(map);
L.control.scale({ imperial: false }).addTo(map);
map.createPane("field").style.zIndex = 350;
map.getPane("field").style.pointerEvents = "none";
const layers = { icos: L.layerGroup().addTo(map), shuttle: L.layerGroup() };

const legend = L.control({ position: "bottomright" });
legend.onAdd = () => {
  const box = L.DomUtil.create("div", "legend");
  box.innerHTML = Object.entries(LINES).map(([fraction, color]) =>
    `<div><i style="border-color:${color}"></i>${Math.round(fraction * 100)}% of the flux</div>`).join("");
  return box;
};

function clearFootprint() {
  overlay?.remove();
  overlay = null;
  Object.values(outlines).forEach((line) => line.remove());
  outlines = {};
  legend.remove();
}

const metresPerDegree = (lat) => [111320, 111320 * Math.cos((lat * Math.PI) / 180)];

function toLatLng(site, [x, y]) {
  const [perLat, perLon] = metresPerDegree(site.lat);
  return [site.lat + y / perLat, site.lon + x / perLon];
}

const square = (site, half) => [toLatLng(site, [-half, -half]), toLatLng(site, [half, half])];

// The grid's outer cell edge, half a cell beyond its outermost centres.
const gridCorner = (summary) => summary.half * (1 + 1 / (summary.size[0] - 1));

function colour(value) {
  const position = value * (RAMP.length - 1);
  const low = Math.min(Math.floor(position), RAMP.length - 2);
  const mix = position - low;
  return RAMP[low].map((channel, i) => channel + (RAMP[low + 1][i] - channel) * mix);
}

// A log scale from the outermost contour to the peak, since density spans decades.
function fieldImage({ summary, field }) {
  const [width, height] = summary.size;
  const values = new Float32Array(field);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  const image = context.createImageData(width, height);
  const floor = Math.log(summary.contours.at(-1)?.level ?? summary.peak_density / 1000);
  const span = Math.log(summary.peak_density) - floor;
  for (let i = 0; i < values.length; i += 1) {
    if (!(values[i] > 0)) continue;
    const value = (Math.log(values[i]) - floor) / span;
    if (value < 0) continue;
    const [red, green, blue] = colour(Math.min(value, 1));
    image.data.set([red, green, blue, 110 + 110 * Math.min(value, 1)], i * 4);
  }
  context.putImageData(image, 0, 0);
  return canvas.toDataURL("image/png");
}

// Layers are updated in place, so stepping through half-hours does not flicker.
function drawFootprint(site, result) {
  const { summary } = result;
  const bounds = square(site, gridCorner(summary));
  const image = fieldImage(result);
  if (overlay) {
    overlay.setBounds(bounds);
    overlay.setUrl(image);
  } else {
    overlay = L.imageOverlay(image, bounds, { interactive: false, pane: "field" }).addTo(map);
    legend.addTo(map);
  }
  for (const [fraction, color] of Object.entries(LINES)) {
    const contour = summary.contours.find((item) => String(item.fraction) === fraction);
    outlines[fraction] ??= L.polyline([], { color, weight: 2, interactive: false }).addTo(map);
    outlines[fraction].setLatLngs((contour?.lines ?? []).map((line) => line.map((point) => toLatLng(site, point))));
  }
}

function fitFootprint(site, summary) {
  const corner = gridCorner(summary);
  const reach = summary.contours.at(-1)?.reach_m ?? corner;
  map.fitBounds(square(site, Math.min(reach * 1.25, corner)));
}

// ---- data ----

async function loadYear(site, year) {
  const key = `${site}/${year}`;
  if (!years.has(key)) {
    const response = await fetch(`data/${key}.bin`);
    if (!response.ok) throw new Error(`Could not load ${key}`);
    years.set(key, new Float32Array(await response.arrayBuffer()));
  }
  return years.get(key);
}

const utc = (text) => Date.parse(`${text}T00:00:00Z`);
const isoDate = (time) => new Date(time).toISOString().slice(0, 10);
const stamp = (time) => new Date(time).toISOString().slice(0, 16).replace("T", " ");

function keepHour(hour, hours) {
  if (hours === "day") return hour >= 6 && hour < 18;
  if (hours === "night") return hour < 6 || hour >= 18;
  return true;
}

// The rows between two dates, inclusive, without the time column, and their times.
async function selectRows(site, from, to, hours) {
  const start = utc(from);
  const end = utc(to) + DAY;
  const wanted = Object.keys(catalogue.sites[site].years)
    .filter((year) => Number(year) >= new Date(start).getUTCFullYear() && Number(year) <= new Date(end - 1).getUTCFullYear());
  const chunks = await Promise.all(wanted.map((year) => loadYear(site, year)));
  const rows = [];
  const times = [];
  chunks.forEach((table, index) => {
    const origin = Date.UTC(Number(wanted[index]), 0, 1);
    for (let i = 0; i < table.length; i += COLUMNS) {
      const time = origin + table[i] * HALF_HOUR;
      if (time < start || time >= end) continue;
      if (!keepHour(new Date(time).getUTCHours(), hours)) continue;
      times.push(time);
      for (let c = 1; c < COLUMNS; c += 1) rows.push(table[i + c]);
    }
  });
  return { rows: new Float32Array(rows), times };
}

function medianHeight(rows) {
  const heights = [];
  for (let i = 0; i < rows.length; i += WIDTH) heights.push(rows[i]);
  heights.sort((a, b) => a - b);
  return heights[heights.length >> 1];
}

// Wide enough for a tall tower's 90% contour, kept to one of the choices on offer.
function automaticHalf(rows) {
  const wanted = MODELS[$("model").value].reach * medianHeight(rows);
  return [250, 500, 1000, 2000, 4000].find((half) => half >= wanted) ?? 4000;
}

const chosenHalf = (rows) => ($("half").value === "auto" ? automaticHalf(rows) : Number($("half").value));

// ---- worker ----

function startWorker() {
  if (worker) return;
  worker = new Worker("worker.js", { type: "module" });
  worker.onmessage = ({ data }) => handlers[data.type]?.(data);
  worker.onerror = (event) => finishWithError(event.message || "The Python worker failed to start.");
}

function stopWorker() {
  worker?.terminate();
  worker = null;
  workerReady = false;
}

const pending = new Map();

const handlers = {
  status: ({ text }) => {
    workerStatus = text;
    if (!reader) $("status").textContent = text;
  },
  ready: () => { workerReady = true; },
  progress: ({ done, total }) => {
    $("status").textContent = `Computing footprints: ${done.toLocaleString("en")} of ${total.toLocaleString("en")} half-hours`;
    $("fill").style.width = `${(100 * done) / total}%`;
  },
  result: ({ id, summary, field }) => { pending.get(id)?.resolve({ summary, field }); pending.delete(id); },
  file: ({ id, name, bytes }) => { pending.get(id)?.resolve({ name, bytes }); pending.delete(id); },
  info: ({ id, info }) => { pending.get(id)?.resolve(info); pending.delete(id); },
  table: ({ id, counts, table }) => { pending.get(id)?.resolve({ counts, table }); pending.delete(id); },
  error: ({ id, text }) => {
    if (id === null) {
      console.error(text);
      finishWithError("Python could not be downloaded. Check your connection and try again.");
    } else {
      pending.get(id)?.reject(new Error(text));
      pending.delete(id);
    }
  },
};

function ask(message, transfer = []) {
  startWorker();
  jobId += 1;
  const id = jobId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker.postMessage({ ...message, id }, transfer);
  });
}

// ---- page ----

function showBusy(busy) {
  $("progress").hidden = !busy;
  $("run").disabled = busy || !siteId;
  $("steps").disabled = busy || !siteId;
  $("read").disabled = busy;
  $("file").disabled = busy;
  if (busy) $("fill").style.width = "0";
}

function showMessage(text) {
  $("message").textContent = text ?? "";
  $("message").hidden = !text;
}

function finishWithError(text) {
  closeSteps();
  showBusy(false);
  showMessage(`Something went wrong: ${text}`);
  pending.clear();
  stopWorker();
}

function sourcesFor(site, from, to) {
  return site.sources.filter((source) => source.start <= to.slice(0, 10) && source.end >= from.slice(0, 10));
}

function describeFile({ sources: [{ start, end }], file, ecosystem }) {
  const worst = file.missing.indexOf(Math.max(...file.missing));
  const short = file.count < file.total ? ` ${file.used[worst]} is missing most often.` : "";
  return `${start} to ${end}. ${file.count.toLocaleString("en")} of ${file.total.toLocaleString("en")} ${file.unit} have every input.${short} ${ecosystem ?? ""}`;
}

function describeCoverage(site) {
  if (site.file) return describeFile(site);
  const total = Object.values(site.years).reduce((sum, count) => sum + count, 0);
  const first = site.sources[0].start;
  const last = site.sources.at(-1).end;
  return `${first} to ${last}, ${total.toLocaleString("en")} half-hours. ${site.ecosystem ?? ""}`;
}

function externalLink(text, url) {
  const link = document.createElement("a");
  link.href = url.replace("http://", "https://");
  link.target = "_blank";     // a click must not lose the footprint on the map
  link.rel = "noopener";
  link.textContent = text;
  return link;
}

function showOrigin(site) {
  if (site?.file) {
    $("origin").textContent = `From ${site.file.name}, which stays in this browser.`;
    $("origin").hidden = false;
    return;
  }
  const links = [["station page", site?.url], ...(site?.sources ?? []).map((source) => [`${source.kind} data`, source.doi])]
    .filter(([, url]) => url)
    .map(([text, url]) => externalLink(text, url));
  $("origin").replaceChildren("ICOS data: ", ...links.flatMap((link, index) => (index ? [", ", link] : [link])));
  $("origin").hidden = !links.length;
}

function setDates(days) {
  const site = catalogue.sites[siteId];
  if (!site) return;
  const last = site.sources.at(-1).end;
  const first = isoDate(Math.max(utc(last) - (days - 1) * DAY, utc(site.sources[0].start)));
  $("from").value = first;
  $("to").value = last;
  closeSteps();
}

// Leaflet cannot animate a map with no size, as in a tab that was never shown.
function flyTo({ lat, lon }) {
  const zoom = Math.max(map.getZoom(), 13);
  if (map.getSize().x) map.flyTo([lat, lon], zoom, { duration: 0.6 });
  else map.setView([lat, lon], zoom);
}

function paintMarkers(group, id, fill) {
  Object.entries(group).forEach(([key, marker]) => {
    marker.setStyle({ fillColor: key === id ? "#ff9f1c" : fill, radius: key === id ? 8 : 6 });
  });
}

function showDetails(site) {
  $("details").hidden = !site?.file;
  $("place").hidden = !site?.file || !site.file.lacksPlace;
  $("lat").required = !$("place").hidden;
  $("lon").required = !$("place").hidden;
  $("zm").required = Boolean(site?.file) && !archive.span;
}

function selectSite(id, { keepDates = false } = {}) {
  closeSteps();
  siteId = id;
  if (source === "icos") {
    $("site").value = id;
    paintMarkers(markers, id, "#0b6e4f");
  }
  $("run").disabled = !id;
  $("steps").disabled = !id;
  const site = catalogue.sites[id];
  showOrigin(site);
  showDetails(site);
  if (!site) {
    $("coverage").textContent = "";
    return;
  }
  $("coverage").textContent = describeCoverage(site);
  const first = site.sources[0].start;
  const last = site.sources.at(-1).end;
  for (const input of [$("from"), $("to")]) {
    input.min = first;
    input.max = last;
  }
  if (!keepDates) setDates(30);
  if (site.lat !== null) flyTo(site);
  startWorker();    // Python starts downloading while the visitor picks dates.
}

// A link can name a site in the Shuttle's list, but it cannot carry a file.
function writeState(from, to, at) {
  const site = catalogue.sites[siteId];
  const place = site.file ? site.file.listed && { shuttle: site.id } : { site: siteId };
  if (!place) {
    clearState();
    return;
  }
  const state = new URLSearchParams({ ...place, from, to, hours: $("hours").value, area: $("half").value, model: $("model").value });
  if (at) state.set("at", at);
  if (site.file && $("zm").value) state.set("zm", $("zm").value);
  history.replaceState(null, "", `#${state}`);
}

function clearState() {
  history.replaceState(null, "", location.pathname + location.search);
}

function formatDistance(metres) {
  return metres >= 1000 ? `${(metres / 1000).toFixed(2)} km` : `${Math.round(metres)} m`;
}

function formatArea(hectares) {
  return hectares < 0.1 ? `${Math.round(hectares * 1e4)} m²` : `${hectares.toFixed(hectares < 1 ? 2 : 1)} ha`;
}

function compass(degrees) {
  return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(degrees / 45) % 8];
}

const roughnessRow = (summary) => (summary.z0 == null ? [] : [["Roughness length z0, estimated", `${summary.z0.toFixed(2)} m`]]);

function footprintRows(summary) {
  return [
    ["Model", MODELS[summary.model].name],
    ["Peak", `${formatDistance(summary.peak_m)} ${compass(summary.peak_bearing)}`],
    ...summary.contours.map((c) => [`${Math.round(c.fraction * 100)}% reach, area`, `${formatDistance(c.reach_m)}, ${formatArea(c.area_ha)}`]),
    ["Measurement height z - d", `${summary.zm.toFixed(1)} m`],
    ...roughnessRow(summary),
    ["Flux inside the map area", `${Math.min(Math.round(summary.captured * 100), 100)}%`],
  ];
}

// What a run from a file rests on, in words for the page and the exports.
function estimates(file) {
  const [ustar, , , heat, temperature, pressure] = file.used;
  return `mo_length from ${ustar}, ${heat}, ${temperature} and ${pressure}; v_sigma as 2 u*, crude; pblh as u* / f, crude`;
}

function heightSource() {
  if ($("zm").value) return `${$("zm").value} m, entered by the visitor`;
  return "sensor height from VAR_INFO_HEIGHT minus 0.67 of canopy height from HEIGHTC, each as dated in the file";
}

function zone({ file }) {
  if (file?.offset === null || file?.offset === undefined) return "local standard time";
  return `local standard time, UTC${file.offset < 0 ? "" : "+"}${file.offset}`;
}

function fileProvenance(site) {
  if (!site.file) return {};
  return { data_file: site.file.name, estimated_inputs: estimates(site.file), z_minus_d: heightSource(), times: zone(site) };
}

function heightNote(from, to) {
  if ($("zm").value) return "z - d is the value you entered, for every half-hour.";
  const dates = archive.canopy.map(([date]) => date.slice(0, 10));
  const worked = "z - d comes from the file: sensor height (VAR_INFO_HEIGHT) minus 0.67 of canopy height (HEIGHTC).";
  if (dates.length === 1) return `${worked} Its only canopy reading stands for every date. Enter z - d if you know it better.`;
  if (from.slice(0, 10) >= dates[0] && to.slice(0, 10) <= dates.at(-1)) return worked;
  return `${worked} Canopy readings run from ${dates[0]} to ${dates.at(-1)}. Earlier dates use their median and later dates the last one, so enter z - d if you know it better.`;
}

// Against the wind data of the ICOS tab, WD from a sonic stays within 5 degrees at three sites. From a separate sensor
// it sits 7 degrees off at FI-Hyy, 30 at DK-Sor and about 125 at FR-Pue from 2022 on.
function windNote({ windSensor }) {
  if (!windSensor || windSensor.startsWith("SA-")) return [];
  return [`WD in this file comes from ${windSensor}, not the sonic, so check the footprint's direction. At FR-Pue such a WD sits about 125° from the sensor the ICOS tab uses.`];
}

function fileNotes(site, from, to) {
  const link = site.file.listed
    ? "A shared link keeps the site and dates, but whoever opens it has to load the archive too."
    : "A shared link cannot carry a file, so this page's address does not lead back to this result. Share a download instead.";
  return [
    `Estimated, not measured: ${estimates(site.file)}. For one month at five ICOS sites, pblh ran 3 to 5 times ETC's own and reach stayed within 11%.`,
    heightNote(from, to),
    ...windNote(site.file),
    `Times are ${zone(site)}.`,
    ...(site.file.unit === "hours" ? ["This file is hourly, so each record is an hour, not a half-hour."] : []),
    link,
  ];
}

function fillList(id, texts) {
  $(id).replaceChildren(...texts.map((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  }));
}

function showResults({ site, from, to, title, rows, notes, summary }) {
  $("result-title").textContent = title;
  $("stats").replaceChildren(...rows.flatMap(([term, value]) => {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value;
    return [dt, dd];
  }));

  const sources = sourcesFor(site, from, to);
  const shown = [...notes];
  if (summary.used) {
    if (summary.contours.some((c) => !c.closed)) shown.push("A contour reaches the edge of the map area. Choose a wider area.");
    if (summary.contours.length < 3) shown.push("The 90% contour lies outside the map area. Choose a wider area.");
  }
  for (const source of sources) {
    if (source.kind === "near-real-time") shown.push("Includes near-real-time data, which is preliminary.");
    if (source.wind) shown.push(`Released data has no eddy-covariance wind direction. This uses sensor ${source.wind.sensor}, which is about ${Math.abs(source.wind.offset)}° from it in the latest data.`);
  }
  if (site.file) shown.push(...fileNotes(site, from, to));
  fillList("notes", shown);
  fillList("citations", [
    ...sources.map((source) => source.citation).filter(Boolean), ...MODELS[summary.model].citations,
    `FluxPrint ${summary.version}. https://github.com/pedrohenriquecoimbra/FluxPrint`,
  ]);
  $("export").hidden = !summary.used;
  $("results").hidden = false;
}

function chosenDates() {
  const from = $("from").value;
  const to = $("to").value;
  showMessage();
  if (!from || !to || from > to) {
    showMessage("Choose a start date on or before the end date.");
    return null;
  }
  return { from, to };
}

function clearResults() {
  current = null;
  clearFootprint();
  $("results").hidden = true;
}

function runOptions(site, half, from, to) {
  const sources = sourcesFor(site, from, to);
  const model = $("model").value;
  return {
    half, model, lat: site.lat, lon: site.lon,
    provenance: {
      site: site.id ?? siteId, site_name: site.name, period_start: from, period_end: to, hours: $("hours").value,
      model, model_citation: MODELS[model].citations.join(" | "),
      data_citation: sources.map((source) => source.citation).join(" | "),
      wind_direction: site.file ? `WD in the file, from ${site.file.windSensor ?? "an unnamed sensor"}` : sources.map((source) => (source.wind ? `sensor ${source.wind.sensor}` : "EC")).join(" | "),
      ...fileProvenance(site),
      made_with: "https://github.com/pedrohenriquecoimbra/FluxPrint web tool",
    },
  };
}

function periodNotes(summary) {
  const notes = [];
  if (summary.used < 10) notes.push("Very few half-hours: this is not a climatology.");
  if (summary.used < summary.records / 2 && summary.reason) {
    notes.push(`Only ${Math.round((100 * summary.used) / summary.records)}% of the half-hours passed the model's checks. Most common reason: ${summary.reason}`);
  }
  return notes;
}

async function runFootprint() {
  const dates = chosenDates();
  if (!dates) return;
  const { from, to } = dates;
  const site = catalogue.sites[siteId];
  closeSteps();
  clearResults();
  showBusy(true);
  $("status").textContent = "Loading site data";
  try {
    if (site.file) await prepareFile();
    const { rows, times } = await selectRows(siteId, from, to, $("hours").value);
    if (!times.length) {
      showBusy(false);
      showMessage("No half-hours with complete data in this period.");
      return;
    }
    $("status").textContent = workerReady ? "Computing footprints" : workerStatus;
    const options = runOptions(site, chosenHalf(rows), from, to);
    writeState(from, to);
    const result = await ask({ type: "run", rows: rows.buffer, options }, [rows.buffer]);
    showBusy(false);
    const { summary } = result;
    if (!summary.used) {
      showMessage(`None of the ${times.length.toLocaleString("en")} half-hours passed the model's validity checks. Most common reason: ${summary.reason ?? "unknown"}`);
      return;
    }
    current = { site: siteId, name: site.id ?? siteId, from, to, label: `${from}_${to}_${summary.model}`, summary };
    drawFootprint(site, result);
    fitFootprint(site, summary);
    showResults({
      site, from, to, summary,
      title: `${site.name}, ${from === to ? from : `${from} to ${to}`}`,
      rows: [["Half-hours used", `${summary.used.toLocaleString("en")} of ${summary.records.toLocaleString("en")}`], ...footprintRows(summary)],
      notes: periodNotes(summary),
    });
  } catch (error) {
    finishWithError(error.message);
  }
}

// ---- half-hour by half-hour ----

const wait = (milliseconds) => new Promise((done) => { setTimeout(done, milliseconds); });
const stepRow = (stepper, index) => stepper.rows.subarray(index * WIDTH, (index + 1) * WIDTH);

function describeStep(stepper, index) {
  const [, speed, , , length, , direction] = stepRow(stepper, index);
  return `${stamp(stepper.times[index])} ${zone(catalogue.sites[stepper.site])} · wind ${Math.round(direction)}° at ${speed.toFixed(1)} m/s`
    + ` · L ${Math.round(length).toLocaleString("en")} m · ${(index + 1).toLocaleString("en")} of ${stepper.times.length.toLocaleString("en")}`;
}

function closeSteps() {
  if (!steps) return;
  steps.playing = false;
  steps = null;
  $("timeline").hidden = true;
  map.invalidateSize();
}

function rememberStep() {
  if (steps) writeState(steps.from, steps.to, stamp(steps.times[steps.at]).replace(" ", "T"));
}

async function showStep(stepper, index) {
  const site = catalogue.sites[stepper.site];
  const when = stamp(stepper.times[index]);
  const row = stepRow(stepper, index).slice();
  const [height, speed, ustar, pblh, length, , direction] = row;
  const options = runOptions(site, stepper.half, when, when);
  const result = await ask({ type: "run", rows: row.buffer, options }, [row.buffer]);
  if (stepper !== steps) return;
  const { summary } = result;
  const estimated = site.file ? ", estimated" : "";
  const weather = [
    ["Wind", `${Math.round(direction)}° ${compass(direction)}, ${speed.toFixed(1)} m/s`],
    ["u*", `${ustar.toFixed(2)} m/s`],
    [`Obukhov length${estimated}`, `${Math.round(length).toLocaleString("en")} m`],
    [`Boundary layer${estimated}`, `${Math.round(pblh).toLocaleString("en")} m`],
  ];
  const view = { site, from: when, to: when, summary, title: `${site.name}, ${when}` };
  if (summary.used) {
    current = { site: stepper.site, name: site.id ?? stepper.site, from: when, to: when, label: `${when.replace(" ", "T").replace(":", "")}_${summary.model}`, summary };
    drawFootprint(site, result);
    showResults({ ...view, rows: [...weather, ...footprintRows(summary)], notes: [] });
  } else {
    current = null;
    clearFootprint();
    $("when").textContent = `${describeStep(stepper, index)} · no footprint`;
    showResults({
      ...view,
      rows: [...weather, ["Measurement height z - d", `${height.toFixed(1)} m`], ...roughnessRow(summary)],
      notes: [`No footprint for this half-hour: ${summary.reason ?? "the model rejected it."}`],
    });
  }
}

// Dragging asks for half-hours faster than they compute, so only the latest one runs.
async function computeSteps(stepper) {
  try {
    while (stepper === steps && stepper.shown !== stepper.at) {
      const index = stepper.at;
      await showStep(stepper, index);
      stepper.shown = index;
    }
  } catch (error) {
    finishWithError(error.message);
  } finally {
    stepper.working = null;
  }
}

function goTo(index) {
  const stepper = steps;
  stepper.at = index;
  $("scrub").value = index;
  $("when").textContent = describeStep(stepper, index);
  if (!stepper.working && stepper.shown !== index) stepper.working = computeSteps(stepper);
  return stepper.working;
}

function setPlaying(stepper, playing) {
  stepper.playing = playing;
  $("play").classList.toggle("playing", playing);
  $("play").setAttribute("aria-label", playing ? "Pause" : "Play");
}

async function play() {
  const stepper = steps;
  const last = stepper.times.length - 1;
  setPlaying(stepper, true);
  if (stepper.at === last) await goTo(0);
  while (stepper.playing && stepper.at < last) {
    const shortest = wait(FRAME_TIME);
    await goTo(stepper.at + 1);
    await shortest;
  }
  setPlaying(stepper, false);
  rememberStep();
}

async function step(offset) {
  setPlaying(steps, false);
  await goTo(Math.min(Math.max(steps.at + offset, 0), steps.times.length - 1));
  rememberStep();
}

async function startSteps(at) {
  const dates = chosenDates();
  if (!dates) return;
  const { from, to } = dates;
  closeSteps();
  clearResults();
  showBusy(true);
  $("status").textContent = "Loading site data";
  try {
    if (catalogue.sites[siteId].file) await prepareFile();
    const { rows, times } = await selectRows(siteId, from, to, $("hours").value);
    if (!times.length) {
      showBusy(false);
      showMessage("No half-hours with complete data in this period.");
      return;
    }
    $("status").textContent = workerReady ? "Computing footprints" : workerStatus;
    steps = { site: siteId, from, to, rows, times, half: chosenHalf(rows), at: 0, shown: -1, playing: false, working: null };
    $("scrub").max = times.length - 1;
    $("timeline").hidden = false;
    setPlaying(steps, false);
    map.invalidateSize();
    map.fitBounds(square(catalogue.sites[siteId], steps.half));
    const start = at ? times.findIndex((time) => stamp(time).replace(" ", "T") >= at) : 0;
    await goTo(Math.max(start, 0));
    showBusy(false);
    rememberStep();
  } catch (error) {
    finishWithError(error.message);
  }
}

// ---- downloads ----

function save(name, bytes, type) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([bytes], { type }));
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
}

function contoursAsGeoJson() {
  const site = catalogue.sites[current.site];
  const features = current.summary.contours.flatMap((contour) => contour.lines.map((line) => ({
    type: "Feature",
    properties: { site: current.name, from: current.from, to: current.to, fraction: contour.fraction },
    geometry: {
      type: "LineString",
      coordinates: line.map((point) => {
        const [lat, lon] = toLatLng(site, point);
        return [Number(lon.toFixed(6)), Number(lat.toFixed(6))];
      }),
    },
  })));
  const citation = sourcesFor(site, current.from, current.to).map((source) => source.citation).join(" | ");
  const { model } = current.summary;
  return JSON.stringify({ type: "FeatureCollection", citation, model, model_citation: MODELS[model].citations.join(" | "), ...fileProvenance(site), features });
}

async function download(kind, button) {
  if (steps) {      // Python exports its latest footprint, so that must be the one on the map.
    setPlaying(steps, false);
    await steps.working;
  }
  if (!current) return;
  if (kind === "geojson") {
    save(`${current.name}_${current.label}.geojson`, contoursAsGeoJson(), "application/geo+json");
    return;
  }
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Preparing";
  try {
    const file = await ask({ type: "export", kind });
    const extension = file.name.slice(file.name.indexOf("."));
    const suffix = kind === "shapefile" ? "_shp" : "";
    save(`${current.name}_${current.label}${suffix}${extension}`, file.bytes, "application/octet-stream");
  } catch (error) {
    showMessage(`Could not prepare the file: ${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

// ---- files and the FLUXNET Shuttle ----

function stopReader() {
  reader?.terminate();
  reader = null;
}

function readFile(input, name) {
  return new Promise((resolve, reject) => {
    reader = new Worker("archive.js");
    reader.onmessage = ({ data }) => {
      if (data.type === "progress") {
        $("status").textContent = `Reading ${name}: ${Math.round(data.share * 100)}%`;
        $("fill").style.width = `${data.share * 100}%`;
        return;
      }
      stopReader();
      if (data.type === "done") resolve(data);
      else reject(new Error(data.text));
    };
    reader.onerror = (event) => {
      stopReader();
      reject(new Error(event.message || "The file could not be read."));
    };
    reader.postMessage(input);
  });
}

// The page's site for a file: what its metadata says, else what the Shuttle's list says. The columns stay in
// `archive` until the height and place are known, when prepareFile turns them into rows like a baked site's.
function useArchive(read, info) {
  const id = info.site ?? read.name.match(SITE_CODE)?.[0] ?? read.name.replace(/\.\w+$/, "");
  const entry = shuttle?.sites[id];
  const listed = [info.product, read.name].includes(entry?.product);     // TERN's files carry another name inside
  const [lat, lon] = [info.lat ?? entry?.lat ?? null, info.lon ?? entry?.lon ?? null];
  const policy = "Its DATA_POLICY_LICENSE_AND_INSTRUCTIONS.txt says what else to acknowledge.";
  const citation = listed ? `${entry.citation} ${policy}` : info.product ? `${info.product}. ${policy}` : `${read.name}, the visitor's own file.`;
  archive = { raw: read.rows, sensor: info.sensor, canopy: info.canopy, span: info.height_span, prepared: null };
  catalogue.sites[FILE] = {
    id, lat, lon, name: info.name ?? entry?.name ?? read.name, ecosystem: info.igbp ?? entry?.igbp, years: {},
    sources: [{ kind: "file", start: isoDate(read.first), end: isoDate(read.last), citation }],
    file: {
      name: read.name, used: read.used, count: read.count, total: read.total, missing: read.missing,
      unit: read.step === 2 * HALF_HOUR ? "hours" : "half-hours", offset: info.utc_offset, listed, lacksPlace: lat === null || lon === null,
      windSensor: info.wind_sensor,
    },
  };
  $("lat").value = lat ?? "";
  $("lon").value = lon ?? "";
  $("zm").value = linked?.get("shuttle") === id ? linked.get("zm") ?? "" : "";
  $("height").textContent = heightHint(info);
  fileMarker?.remove();
  fileMarker = null;
  if (lat !== null) placeFileMarker(catalogue.sites[FILE]);
}

function placeFileMarker({ id, lat, lon }) {
  fileMarker ??= L.circleMarker([lat, lon], {
    radius: 8, color: "#ffffff", weight: 1.5, fillColor: "#ff9f1c", fillOpacity: 1,
  }).bindTooltip(`${id} from your file`).addTo(map);
  fileMarker.setLatLng([lat, lon]);
}

function heightHint({ sensor, canopy, height_span: span }) {
  const shown = (value) => `${Number(value.toFixed(1))} m`;
  if (span) {
    const [low, high] = span.map(shown);
    return `Worked out from the file: ${low === high ? low : `${low} to ${high}`}, sensor height minus 0.67 of canopy height. Type a value to use it instead.`;
  }
  if (sensor.length && !canopy.length) return `The sensor is at ${shown(sensor.at(-1)[1])}, but the file gives no canopy height, so enter z - d.`;
  return "The file does not give the heights needed, so enter z - d: the sensor height minus the displacement height.";
}

async function loadArchive(input, name) {
  closeSteps();
  clearResults();
  showMessage();
  showBusy(true);
  $("status").textContent = `Reading ${name}`;
  startWorker();    // Python downloads while the file is read
  try {
    const [read] = await Promise.all([readFile(input, name), loadShuttle().catch(() => null)]);
    if (!read.count) throw new Error(`No half-hour in ${read.name} has every input. ${read.used[read.missing.indexOf(Math.max(...read.missing))]} is missing most often.`);
    $("status").textContent = workerReady ? "Reading the site's metadata" : workerStatus;
    useArchive(read, await ask({ type: "describe", bif: read.bif, varinfo: read.varinfo }));
    const { id } = catalogue.sites[FILE];
    const keepDates = linked?.get("shuttle") === id;
    showBusy(false);
    if (source === "shuttle" && shuttle?.sites[id]) {
      selectShuttle(id, { keepDates });
    } else {
      setTab("file");
      showLoader();
      selectSite(FILE, { keepDates });
    }
    if (!keepDates) clearState();
    linked = null;
  } catch (error) {
    stopReader();
    showBusy(false);
    showMessage(input.url ? `${error.message} Download the archive and load it here instead.` : error.message);
  }
}

// Turns the file's columns into rows once its place and height are known, and again if they change.
async function prepareFile() {
  const site = catalogue.sites[FILE];
  site.lat = Number($("lat").value);
  site.lon = Number($("lon").value);
  placeFileMarker(site);
  const zm = Number($("zm").value) || null;
  const key = `${site.lat} ${zm}`;
  if (archive.prepared === key) return;
  $("status").textContent = workerReady ? "Estimating the missing inputs" : workerStatus;
  const { raw, sensor, canopy } = archive;
  const { counts, table } = await ask({ type: "prepare", raw, options: { latitude: site.lat, zm, sensor, canopy } });
  [...years.keys()].filter((year) => year.startsWith(`${FILE}/`)).forEach((year) => years.delete(year));
  let row = 0;
  for (const [year, count] of Object.entries(counts)) {
    years.set(`${FILE}/${year}`, new Float32Array(table, row * COLUMNS * 4, count * COLUMNS));
    row += count;
  }
  site.years = counts;
  archive.prepared = key;
}

function describeEntry(entry) {
  const size = entry.size ? `, ${Math.round(entry.size / 1e6).toLocaleString("en")} MB` : "";
  return `${entry.years[0]} to ${entry.years[1]}, ${entry.hub} hub${size}. ${entry.igbp ?? ""}`;
}

function showLoader(entry) {
  const direct = HUBS[entry?.hub]?.direct;
  $("loader").hidden = source === "icos" || (source === "shuttle" && !entry);
  $("read").hidden = !direct;
  $("get").hidden = !entry;
  if (!entry) {
    $("route").textContent = OWN_FILE;
    return;
  }
  $("get").href = entry.link;
  $("read").textContent = `Read from ${entry.hub}`;
  $("route").textContent = direct
    ? `This page can read the archive straight from ${entry.hub}. ${HUBS[entry.hub].terms} You can also download it and load it here.`
    : `${entry.hub} does not let a web page read its files, so this takes one download. ${HUBS[entry.hub].terms} Then load the ZIP here. It stays in this browser.`;
}

function selectShuttle(id, options) {
  $("shuttle-site").value = id;
  paintMarkers(shuttleMarkers, id, SHUTTLE_FILL);
  const entry = shuttle.sites[id];
  const loaded = id && catalogue.sites[FILE]?.id === id;
  selectSite(loaded ? FILE : "", options);
  showLoader(loaded ? null : entry);
  if (!entry || loaded) return;
  $("coverage").textContent = describeEntry(entry);
  flyTo(entry);
  startWorker();    // Python starts downloading while the visitor gets the archive.
}

async function loadShuttle() {
  if (shuttle) return;
  const response = await fetch("data/shuttle.json");
  if (!response.ok) throw new Error("no list");
  shuttle = await response.json();
  addSites(shuttle.sites, {
    select: $("shuttle-site"), layer: layers.shuttle, group: shuttleMarkers, fill: SHUTTLE_FILL,
    country: (id) => id.slice(0, 2), pick: selectShuttle,
  });
}

function setTab(next) {
  source = next;
  $("tabs").querySelectorAll("button").forEach((button) => button.setAttribute("aria-pressed", button.dataset.source === next));
  $("icos-field").hidden = next !== "icos";
  $("shuttle-field").hidden = next !== "shuttle";
  Object.entries(layers).forEach(([tab, layer]) => (tab === next ? layer.addTo(map) : layer.remove()));
}

async function openTab(next) {
  setTab(next);
  clearResults();
  clearState();
  showMessage();
  if (next !== "shuttle") {
    showLoader();
    selectSite(next === "icos" ? $("site").value : catalogue.sites[FILE] ? FILE : "");
    return;
  }
  try {
    await loadShuttle();
    selectShuttle($("shuttle-site").value);
  } catch {
    selectSite("");
    showMessage("The list of FLUXNET Shuttle sites did not load. A file you have still works under Your file.");
  }
}

// ---- start ----

function addSites(sites, { select, layer, group, fill, country, pick }) {
  const byCountry = {};
  for (const [id, site] of Object.entries(sites)) {
    (byCountry[country(id, site)] ??= []).push([id, site]);
    const marker = L.circleMarker([site.lat, site.lon], {
      radius: 6, color: "#ffffff", weight: 1.5, fillColor: fill, fillOpacity: 1,
    }).addTo(layer);
    marker.bindTooltip(`${id} ${site.name}`);
    marker.on("click", () => pick(id));
    group[id] = marker;
  }
  for (const name of Object.keys(byCountry).sort()) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = name;
    for (const [id, site] of byCountry[name]) {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = `${id} ${site.name}`;
      optgroup.append(option);
    }
    select.append(optgroup);
  }
}

function restoreForm(state) {
  if (state.get("hours")) $("hours").value = state.get("hours");
  if (state.get("area")) $("half").value = state.get("area");
  if (MODELS[state.get("model")]) $("model").value = state.get("model");
  if (!state.get("from") || !state.get("to")) return false;
  $("from").value = state.get("from");
  $("to").value = state.get("to");
  return true;
}

async function restoreShuttle(state) {
  await openTab("shuttle");
  const id = state.get("shuttle");
  const entry = shuttle?.sites[id];
  if (!entry) return;
  selectShuttle(id);
  history.replaceState(null, "", `#${state}`);
  linked = state;
  restoreForm(state);
  const loading = HUBS[entry.hub].direct ? `Read ${id} from ${entry.hub}` : `Load the archive of ${id}`;
  showMessage(`${loading} to run this link. The page cannot keep FLUXNET Shuttle data, so a link keeps only the site and form.`);
}

function restoreState() {
  const state = new URLSearchParams(location.hash.slice(1));
  if (state.get("shuttle")) {
    restoreShuttle(state);
    return;
  }
  const id = state.get("site");
  if (!catalogue.sites[id]) return;
  selectSite(id, { keepDates: true });
  if (!restoreForm(state)) setDates(30);
  else if (state.get("at")) startSteps(state.get("at"));
  else runFootprint();
}

$("form").addEventListener("submit", (event) => {
  event.preventDefault();
  runFootprint();
});
$("form").addEventListener("input", closeSteps);    // the timeline no longer matches the form
$("site").addEventListener("change", (event) => selectSite(event.target.value));
$("shuttle-site").addEventListener("change", (event) => selectShuttle(event.target.value));
$("tabs").addEventListener("click", (event) => {
  if (event.target.dataset.source) openTab(event.target.dataset.source);
});
$("read").addEventListener("click", () => {
  const entry = shuttle.sites[$("shuttle-site").value];
  loadArchive({ url: entry.link }, entry.product);
});
$("file").addEventListener("change", (event) => {
  const [file] = event.target.files;
  event.target.value = "";    // so that picking the same file again still counts
  if (file) loadArchive({ file }, file.name);
});
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => {
  event.preventDefault();
  const [file] = event.dataTransfer.files;
  if (file) loadArchive({ file }, file.name);
});
$("presets").addEventListener("click", (event) => {
  if (event.target.dataset.days) setDates(Number(event.target.dataset.days));
});
$("steps").addEventListener("click", () => {
  if ($("form").reportValidity()) startSteps();
});
$("back").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));
$("play").addEventListener("click", () => {
  if (steps.playing) setPlaying(steps, false);
  else play();
});
$("scrub").addEventListener("input", (event) => {
  setPlaying(steps, false);
  goTo(Number(event.target.value));
});
$("scrub").addEventListener("change", rememberStep);
$("downloads").addEventListener("click", (event) => {
  if (event.target.dataset.kind) download(event.target.dataset.kind, event.target);
});
$("cancel").addEventListener("click", () => {
  closeSteps();
  if (reader) {
    stopReader();
  } else {
    pending.clear();
    stopWorker();
    startWorker();
  }
  showBusy(false);
  showMessage("Cancelled.");
});

Object.entries(MODELS).forEach(([id, model]) => $("model").add(new Option(model.name, id)));

try {
  catalogue = await (await fetch("data/sites.json")).json();
  addSites(catalogue.sites, {
    select: $("site"), layer: layers.icos, group: markers, fill: "#0b6e4f",
    country: (id, site) => site.country, pick: selectSite,
  });
  $("copied").textContent = `${Object.keys(catalogue.sites).length} sites, copied on ${catalogue.built}.`;
  restoreState();
} catch (error) {
  showMessage(`Could not load the site list: ${error.message}`);
}
