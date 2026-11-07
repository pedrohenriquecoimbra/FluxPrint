// Reads a file in FLUXNET format off the main thread: the columns a footprint needs and the site's metadata.
// A ZIP is read through its directory, so only the entries needed are touched, and the data is streamed, never held
// whole. A whole archive is over 200 MB, and 700 MB once unpacked. A file and a hub's URL go the same way.

const COLUMNS = [["USTAR"], ["WS_F", "WS"], ["WD"], ["H_F_MDS", "H"], ["TA_F", "TA"], ["PA_F", "PA"]];
const WIDTH = COLUMNS.length + 1;       // the time comes first
const MISSING = -9999;
const COMMA = 44;
const NEWLINE = 10;
const RETURN = 13;
const END_OF_DIRECTORY = 0x06054b50;
const DIRECTORY_ENTRY = 0x02014b50;
const LONGEST_ZIP_COMMENT = 65535;
const DATA_FILE = /FLUXMET_(HH|HR)_[^/]*\.csv$/i;
const SITE_FILE = /_BIF_[^/]*\.csv$/i;
const HEIGHTS_FILE = /BIFVARINFO_(HH|HR)_[^/]*\.csv$/i;

const fileSource = (file) => ({ name: file.name, size: file.size, stream: async (start, end) => file.slice(start, end).stream() });

async function urlSource(url) {
  const name = new URL(url).pathname.split("/").pop();
  const head = await fetch(url, { method: "HEAD" });
  const size = Number(head.headers.get("content-length"));
  if (!head.ok || !size) throw new Error(`The hub did not offer ${name}.`);
  return {
    name,
    size,
    stream: async (start, end) => {
      const response = await fetch(url, { headers: { Range: `bytes=${start}-${end - 1}` } });
      if (response.status !== 206) throw new Error("The hub would not send part of the file.");
      return response.body;
    },
  };
}

async function readBytes(source, start, end) {
  return new Uint8Array(await new Response(await source.stream(start, end)).arrayBuffer());
}

// The list of a ZIP's files, from the directory at its end.
async function readDirectory(source) {
  const start = Math.max(0, source.size - 22 - LONGEST_ZIP_COMMENT);
  const tail = await readBytes(source, start, source.size);
  const end = new DataView(tail.buffer);
  let at = tail.length - 22;
  while (at >= 0 && end.getUint32(at, true) !== END_OF_DIRECTORY) at -= 1;
  if (at < 0) throw new Error(`${source.name} is not a ZIP file.`);
  const length = end.getUint32(at + 12, true);
  const offset = end.getUint32(at + 16, true);
  if (length === 0xffffffff || offset === 0xffffffff) throw new Error("This ZIP is in ZIP64 format, which the page cannot read.");
  const table = await readBytes(source, offset, offset + length);
  const view = new DataView(table.buffer);
  const names = new TextDecoder();
  const entries = [];
  for (let i = 0; i + 46 <= table.length && view.getUint32(i, true) === DIRECTORY_ENTRY;) {
    const nameLength = view.getUint16(i + 28, true);
    entries.push({
      method: view.getUint16(i + 10, true),
      packed: view.getUint32(i + 20, true),
      size: view.getUint32(i + 24, true),
      offset: view.getUint32(i + 42, true),
      name: names.decode(table.subarray(i + 46, i + 46 + nameLength)),
    });
    i += 46 + nameLength + view.getUint16(i + 30, true) + view.getUint16(i + 32, true);
  }
  return entries;
}

async function openEntry(source, entry) {
  if (entry.method !== 0 && entry.method !== 8) throw new Error(`${entry.name} is packed in a way the page cannot read.`);
  const header = new DataView((await readBytes(source, entry.offset, entry.offset + 30)).buffer);
  const start = entry.offset + 30 + header.getUint16(26, true) + header.getUint16(28, true);
  const stream = await source.stream(start, start + entry.packed);
  return entry.method === 8 ? stream.pipeThrough(new DecompressionStream("deflate-raw")) : stream;
}

async function readText(source, entry) {
  return entry ? new Response(await openEntry(source, entry)).text() : "";
}

function findColumns(header) {
  const names = header.split(",").map((name) => name.trim().replace(/^﻿?"?|"$/g, ""));
  const wanted = [["TIMESTAMP_START"], ...COLUMNS];
  const found = wanted.map((options) => options.find((name) => names.includes(name)));
  const absent = wanted.filter((_, slot) => !found[slot]).map((options) => options.join(" or "));
  if (absent.length) {
    throw new Error(`This file does not follow the FLUXNET format: it has no ${absent.join(", no ")}.`);
  }
  const picks = found.map((name, slot) => ({ index: names.indexOf(name), slot }));
  return { used: found.slice(1), picks: picks.sort((a, b) => a.index - b.index) };
}

function parseTime(text) {
  const time = text.length === 12 ? Date.UTC(+text.slice(0, 4), +text.slice(4, 6) - 1, +text.slice(6, 8), +text.slice(8, 10), +text.slice(10)) : NaN;
  if (Number.isNaN(time)) throw new Error(`TIMESTAMP_START must read YYYYMMDDHHMM, as in the FLUXNET format, not "${text.slice(0, 20)}".`);
  return time;
}

function parseValue(text) {
  const value = text ? Number(text) : NaN;
  return value === MISSING ? NaN : value;
}

// Keeps the rows with every input. Fields after the last wanted column are not looked at.
async function readTable(stream, size, report) {
  const reader = stream.getReader();
  const values = new Float64Array(WIDTH);
  const missing = new Array(COLUMNS.length).fill(0);
  let rows = new Float64Array(WIDTH * 32768);
  let count = 0;
  let total = 0;
  let columns = null;
  let first = Infinity;
  let last = -Infinity;
  let previous = NaN;
  let step = Infinity;

  function readRow(chunk, start, end) {
    values.fill(NaN);
    const { picks } = columns;
    let field = 0;
    let from = start;
    let pick = 0;
    let wanted = picks[0].index;
    for (let i = start; i <= end; i += 1) {
      if (i < end && chunk[i] !== COMMA) continue;
      if (field === wanted) {
        const { slot } = picks[pick];
        const text = String.fromCharCode.apply(null, chunk.subarray(from, i));
        values[slot] = slot ? parseValue(text) : parseTime(text);
        pick += 1;
        if (pick === picks.length) break;
        wanted = picks[pick].index;
      }
      field += 1;
      from = i + 1;
    }
    total += 1;
    const time = values[0];
    if (time > previous) step = Math.min(step, time - previous);
    previous = time;
    let complete = true;
    for (let slot = 1; slot < WIDTH; slot += 1) {
      if (Number.isNaN(values[slot])) {
        missing[slot - 1] += 1;
        complete = false;
      }
    }
    if (!complete) return;
    if ((count + 1) * WIDTH > rows.length) {
      const longer = new Float64Array(rows.length * 2);
      longer.set(rows);
      rows = longer;
    }
    rows.set(values, count * WIDTH);
    count += 1;
    first = Math.min(first, time);
    last = Math.max(last, time);
  }

  function readLine(chunk, start, stop) {
    const end = stop > start && chunk[stop - 1] === RETURN ? stop - 1 : stop;
    if (end === start) return;
    if (columns) readRow(chunk, start, end);
    else columns = findColumns(new TextDecoder().decode(chunk.subarray(start, end)));
  }

  let seen = 0;
  let carry = null;     // the start of a line cut off by the end of a chunk
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    seen += value.length;
    report(seen / size);
    let chunk = value;
    if (carry) {
      chunk = new Uint8Array(carry.length + value.length);
      chunk.set(carry);
      chunk.set(value, carry.length);
    }
    let start = 0;
    for (let stop = chunk.indexOf(NEWLINE); stop >= 0; stop = chunk.indexOf(NEWLINE, start)) {
      readLine(chunk, start, stop);
      start = stop + 1;
    }
    carry = start < chunk.length ? chunk.slice(start) : null;
  }
  if (carry) readLine(carry, 0, carry.length);
  if (!columns) throw new Error("This file is empty.");
  return { rows: rows.slice(0, count * WIDTH).buffer, used: columns.used, count, total, missing, first, last, step };
}

async function read({ file, url }) {
  const source = file ? fileSource(file) : await urlSource(url);
  let opened;
  let metadata = { bif: "", varinfo: "" };
  if (/\.csv$/i.test(source.name)) {
    opened = { stream: await source.stream(0, source.size), size: source.size };
  } else {
    const entries = await readDirectory(source);
    const tables = entries.filter((entry) => /\.csv$/i.test(entry.name));
    const data = entries.find((entry) => DATA_FILE.test(entry.name)) ?? (tables.length === 1 ? tables[0] : null);
    if (!data) throw new Error("This ZIP has no half-hourly data file. FLUXNET names it ..._FLUXMET_HH_....csv.");
    metadata = {
      bif: await readText(source, entries.find((entry) => SITE_FILE.test(entry.name))),
      varinfo: await readText(source, entries.find((entry) => HEIGHTS_FILE.test(entry.name))),
    };
    opened = { stream: await openEntry(source, data), size: data.size };
  }
  let shown = 0;
  const table = await readTable(opened.stream, opened.size, (share) => {
    if (share - shown < 0.01) return;
    shown = share;
    postMessage({ type: "progress", share });
  });
  postMessage({ type: "done", name: source.name, ...metadata, ...table }, [table.rows]);
}

self.onmessage = ({ data }) => {
  read(data).catch((error) => postMessage({ type: "error", text: error.message }));
};
