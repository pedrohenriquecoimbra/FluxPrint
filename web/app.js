// The page: picks a site and dates, sends the half-hours to the worker, draws the result.

const $ = (id) => document.getElementById(id);
const HALF_HOUR = 1800e3;
const DAY = 86400e3;
const COLUMNS = 8;
const WIDTH = COLUMNS - 1;      // model inputs per row, without the time column
const FRAME_TIME = 120;         // ms; the shortest time a half-hour stays on screen while playing
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

let catalogue;
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
    $("status").textContent = text;
  },
  ready: () => { workerReady = true; },
  progress: ({ done, total }) => {
    $("status").textContent = `Computing footprints: ${done.toLocaleString("en")} of ${total.toLocaleString("en")} half-hours`;
    $("fill").style.width = `${(100 * done) / total}%`;
  },
  result: ({ id, summary, field }) => { pending.get(id)?.resolve({ summary, field }); pending.delete(id); },
  file: ({ id, name, bytes }) => { pending.get(id)?.resolve({ name, bytes }); pending.delete(id); },
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

function describeCoverage(site) {
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

function selectSite(id, { keepDates = false } = {}) {
  closeSteps();
  siteId = id;
  $("site").value = id;
  $("run").disabled = !id;
  $("steps").disabled = !id;
  Object.entries(markers).forEach(([key, marker]) => {
    marker.setStyle({ fillColor: key === id ? "#ff9f1c" : "#0b6e4f", radius: key === id ? 8 : 6 });
  });
  const site = catalogue.sites[id];
  showOrigin(site);
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
  map.flyTo([site.lat, site.lon], Math.max(map.getZoom(), 13), { duration: 0.6 });
  startWorker();    // Python starts downloading while the visitor picks dates.
}

function writeState(from, to, at) {
  const state = new URLSearchParams({ site: siteId, from, to, hours: $("hours").value, area: $("half").value, model: $("model").value });
  if (at) state.set("at", at);
  history.replaceState(null, "", `#${state}`);
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
      site: siteId, site_name: site.name, period_start: from, period_end: to, hours: $("hours").value,
      model, model_citation: MODELS[model].citations.join(" | "),
      data_citation: sources.map((source) => source.citation).join(" | "),
      wind_direction: sources.map((source) => (source.wind ? `sensor ${source.wind.sensor}` : "EC")).join(" | "),
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
    current = { site: siteId, from, to, label: `${from}_${to}_${summary.model}`, summary };
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
  return `${stamp(stepper.times[index])} local standard time · wind ${Math.round(direction)}° at ${speed.toFixed(1)} m/s`
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
  const weather = [
    ["Wind", `${Math.round(direction)}° ${compass(direction)}, ${speed.toFixed(1)} m/s`],
    ["u*", `${ustar.toFixed(2)} m/s`],
    ["Obukhov length", `${Math.round(length).toLocaleString("en")} m`],
    ["Boundary layer", `${Math.round(pblh).toLocaleString("en")} m`],
  ];
  const view = { site, from: when, to: when, summary, title: `${site.name}, ${when}` };
  if (summary.used) {
    current = { site: stepper.site, from: when, to: when, label: `${when.replace(" ", "T").replace(":", "")}_${summary.model}`, summary };
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
    properties: { site: current.site, from: current.from, to: current.to, fraction: contour.fraction },
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
  return JSON.stringify({ type: "FeatureCollection", citation, model, model_citation: MODELS[model].citations.join(" | "), features });
}

async function download(kind, button) {
  if (steps) {      // Python exports its latest footprint, so that must be the one on the map.
    setPlaying(steps, false);
    await steps.working;
  }
  if (!current) return;
  if (kind === "geojson") {
    save(`${current.site}_${current.label}.geojson`, contoursAsGeoJson(), "application/geo+json");
    return;
  }
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Preparing";
  try {
    const file = await ask({ type: "export", kind });
    const extension = file.name.slice(file.name.indexOf("."));
    const suffix = kind === "shapefile" ? "_shp" : "";
    save(`${current.site}_${current.label}${suffix}${extension}`, file.bytes, "application/octet-stream");
  } catch (error) {
    showMessage(`Could not prepare the file: ${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

// ---- start ----

function fillSites() {
  const byCountry = {};
  for (const [id, site] of Object.entries(catalogue.sites)) {
    (byCountry[site.country] ??= []).push([id, site]);
    const marker = L.circleMarker([site.lat, site.lon], {
      radius: 6, color: "#ffffff", weight: 1.5, fillColor: "#0b6e4f", fillOpacity: 1,
    }).addTo(map);
    marker.bindTooltip(`${id} ${site.name}`);
    marker.on("click", () => selectSite(id));
    markers[id] = marker;
  }
  for (const country of Object.keys(byCountry).sort()) {
    const group = document.createElement("optgroup");
    group.label = country;
    for (const [id, site] of byCountry[country]) {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = `${id} ${site.name}`;
      group.append(option);
    }
    $("site").append(group);
  }
}

function restoreState() {
  const state = new URLSearchParams(location.hash.slice(1));
  const id = state.get("site");
  if (!catalogue.sites[id]) return;
  selectSite(id, { keepDates: true });
  if (state.get("hours")) $("hours").value = state.get("hours");
  if (state.get("area")) $("half").value = state.get("area");
  if (MODELS[state.get("model")]) $("model").value = state.get("model");
  if (state.get("from") && state.get("to")) {
    $("from").value = state.get("from");
    $("to").value = state.get("to");
    if (state.get("at")) startSteps(state.get("at"));
    else runFootprint();
  } else {
    setDates(30);
  }
}

$("form").addEventListener("submit", (event) => {
  event.preventDefault();
  runFootprint();
});
$("form").addEventListener("input", closeSteps);    // the timeline no longer matches the form
$("site").addEventListener("change", (event) => selectSite(event.target.value));
$("presets").addEventListener("click", (event) => {
  if (event.target.dataset.days) setDates(Number(event.target.dataset.days));
});
$("steps").addEventListener("click", () => startSteps());
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
  pending.clear();
  stopWorker();
  showBusy(false);
  showMessage("Cancelled.");
  startWorker();
});

Object.entries(MODELS).forEach(([id, model]) => $("model").add(new Option(model.name, id)));

try {
  catalogue = await (await fetch("data/sites.json")).json();
  fillSites();
  $("copied").textContent = `${Object.keys(catalogue.sites).length} sites, copied on ${catalogue.built}.`;
  restoreState();
} catch (error) {
  showMessage(`Could not load the site list: ${error.message}`);
}
