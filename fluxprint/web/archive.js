// Reads a file in FLUXNET format off the main thread: the columns a footprint needs and the site's metadata.
// A ZIP is read through its directory, so only the entries needed are touched, and the data is streamed, never held
// whole. A whole archive is over 200 MB, and 700 MB once unpacked. A file and a hub's URL go the same way.
// What the file is decides how it is read, not its name. Judging the numbers is left to fluxnet.py.

const COLUMNS = [["USTAR"], ["WS_F", "WS"], ["WD"], ["H_F_MDS", "H"], ["TA_F", "TA"], ["PA_F", "PA"]];
const WIDTH = COLUMNS.length + 1;       // the time comes first
const MISSING = -9999;
const COMMA = 44;
const NEWLINE = 10;
const RETURN = 13;
const END_OF_DIRECTORY = 0x06054b50;
const DIRECTORY_ENTRY = 0x02014b50;
const LONGEST_ZIP_COMMENT = 65535;
const NEEDED = "It needs TIMESTAMP_START, USTAR, WS_F, WD, H_F_MDS, TA_F and PA_F.";
const DATA_FILE = /_(FLUXMET|FULLSET|SUBSET)_(HH|HR)_[^/]*\.csv$/i;
const COARSER_FILE = /_(DD|WW|MM|YY)_[^/]*\.csv$/i;
const SITE_FILE = /_BIF_[^/]*\.csv$/i;
const HEIGHTS_FILE = /BIFVARINFO_(HH|HR)_[^/]*\.csv$/i;
const RESOLUTIONS = { DD: "daily", WW: "weekly", MM: "monthly", YY: "yearly" };
const ZIP_START = [[0x50, 0x4b, 0x03, 0x04], [0x50, 0x4b, 0x05, 0x06]];
const FOREIGN = [
  [[0x25, 0x50, 0x44, 0x46], "a PDF"],
  [[0x1f, 0x8b], "compressed with gzip. Unpack it first"],
  [[0x52, 0x61, 0x72, 0x21], "a RAR archive. Unpack it, then load the CSV inside"],
  [[0x37, 0x7a, 0xbc, 0xaf], "a 7-Zip archive. Unpack it, then load the CSV inside"],
  [[0x89, 0x48, 0x44, 0x46], "a NetCDF file"],
  [[0x43, 0x44, 0x46], "a NetCDF file"],
  [[0xd0, 0xcf, 0x11, 0xe0], "an Excel workbook. Save the sheet as a CSV with commas first"],
  [[0xff, 0xfe], "text saved as UTF-16. Save it as a CSV in UTF-8 first"],
  [[0xfe, 0xff], "text saved as UTF-16. Save it as a CSV in UTF-8 first"],
];

// An error written for the visitor. Any other kind is translated by plainError.
class Problem extends Error {}

const fileSource = (file) => ({ name: file.name, size: file.size, stream: async (start, end) => file.slice(start, end).stream() });

async function urlSource(url) {
  const name = new URL(url).pathname.split("/").pop();
  const head = await fetch(url, { method: "HEAD" });
  const size = Number(head.headers.get("content-length"));
  if (!head.ok || !size) throw new Problem(`The hub did not offer ${name}.`);
  return {
    name,
    size,
    remote: true,
    stream: async (start, end) => {
      const response = await fetch(url, { headers: { Range: `bytes=${start}-${end - 1}` } });
      if (response.status !== 206) throw new Problem("The hub would not send part of the file.");
      return response.body;
    },
  };
}

async function readBytes(source, start, end) {
  return new Uint8Array(await new Response(await source.stream(start, end)).arrayBuffer());
}

const startsWith = (bytes, signature) => signature.every((byte, index) => bytes[index] === byte);

// "zip" or "text", or a Problem saying what the file is instead.
async function identify(source) {
  if (!source.size) throw new Problem(`${source.name} is empty.`);
  const head = await readBytes(source, 0, Math.min(source.size, 4096));
  if (ZIP_START.some((signature) => startsWith(head, signature))) return "zip";
  const foreign = FOREIGN.find(([signature]) => startsWith(head, signature));
  if (foreign) throw new Problem(`${source.name} is ${foreign[1]}. The page reads a ZIP or CSV in FLUXNET format.`);
  if (head.includes(0)) throw new Problem(`${source.name} is not a text file. The page reads a ZIP or CSV in FLUXNET format.`);
  const start = new TextDecoder().decode(head).trimStart().slice(0, 15).toLowerCase();
  if (start.startsWith("<!doctype") || start.startsWith("<html")) {
    throw new Problem(`${source.name} is a web page, not data, so the download probably went wrong. Download it again.`);
  }
  return "text";
}

// The list of a ZIP's files, from the directory at its end.
async function readDirectory(source) {
  const start = Math.max(0, source.size - 22 - LONGEST_ZIP_COMMENT);
  const tail = await readBytes(source, start, source.size);
  const end = new DataView(tail.buffer);
  let at = tail.length - 22;
  while (at >= 0 && end.getUint32(at, true) !== END_OF_DIRECTORY) at -= 1;
  if (at < 0) throw new Problem(`${source.name} is incomplete, probably from a download that stopped. Download it again.`);
  const length = end.getUint32(at + 12, true);
  const offset = end.getUint32(at + 16, true);
  if (length === 0xffffffff || offset === 0xffffffff) throw new Problem(`${source.name} is in ZIP64 format, which the page cannot read. Unpack it, then load the CSV inside.`);
  const table = await readBytes(source, offset, offset + length);
  const view = new DataView(table.buffer);
  const names = new TextDecoder();
  const entries = [];
  for (let i = 0; i + 46 <= table.length && view.getUint32(i, true) === DIRECTORY_ENTRY;) {
    const nameLength = view.getUint16(i + 28, true);
    entries.push({
      locked: Boolean(view.getUint16(i + 8, true) & 1),
      method: view.getUint16(i + 10, true),
      packed: view.getUint32(i + 20, true),
      size: view.getUint32(i + 24, true),
      offset: view.getUint32(i + 42, true),
      name: names.decode(table.subarray(i + 46, i + 46 + nameLength)),
    });
    i += 46 + nameLength + view.getUint16(i + 30, true) + view.getUint16(i + 32, true);
  }
  return entries.filter((entry) => !/(^|\/)(__MACOSX\/|\._)/.test(entry.name));     // a Mac adds these to a ZIP
}

async function openEntry(source, entry) {
  if (entry.locked) throw new Problem(`${source.name} is protected by a password. Unzip it first, then load the CSV inside.`);
  if (entry.method !== 0 && entry.method !== 8) throw new Problem(`${source.name} is packed in a way the page cannot read. Unzip it first, then load the CSV inside.`);
  if (entry.method === 8 && typeof DecompressionStream === "undefined") {
    throw new Problem("This browser cannot unpack a ZIP. Use a recent one, or unzip the file and load the ..._FLUXMET_HH_....csv inside.");
  }
  const header = new DataView((await readBytes(source, entry.offset, entry.offset + 30)).buffer);
  const start = entry.offset + 30 + header.getUint16(26, true) + header.getUint16(28, true);
  const stream = await source.stream(start, start + entry.packed);
  return entry.method === 8 ? stream.pipeThrough(new DecompressionStream("deflate-raw")) : stream;
}

async function readText(source, entry) {
  if (!entry) return "";
  try {
    return await new Response(await openEntry(source, entry)).text();
  } catch (error) {
    if (error instanceof Problem) throw error;
    return "";      // the metadata is optional, so the page asks for what it lacks
  }
}

function wrongFile(name, names) {
  if (names.includes("VARIABLE_GROUP") || /_BIF(VARINFO)?_/i.test(name)) return "is an archive's metadata table, not its data.";
  if (/_ERA[I5]?_/i.test(name)) return "holds ERA5 weather, not the site's measurements.";
  const resolution = name.match(COARSER_FILE)?.[1].toUpperCase();
  if (resolution) return `holds ${RESOLUTIONS[resolution]} data, and a footprint needs half-hourly data.`;
  return null;
}

function findColumns(header, name) {
  const separator = [[";", "semicolons"], ["\t", "tabs"]].find(([mark]) => !header.includes(",") && header.includes(mark));
  if (separator) throw new Problem(`${name} has ${separator[1]} between its columns. The FLUXNET format uses commas, with a point for decimals, so save it that way.`);
  const names = header.split(",").map((text) => text.trim().replace(/^﻿?"?|"$/g, "").trim().toUpperCase());
  const wrong = wrongFile(name, names);
  if (wrong) throw new Problem(`${name} ${wrong} Load the ZIP, or the ..._FLUXMET_HH_....csv inside it.`);
  if (/^\d{6,}/.test(names[0])) throw new Problem(`The first line of ${name} holds numbers, but it must give the column names. ${NEEDED}`);
  const wanted = [["TIMESTAMP_START"], ...COLUMNS];
  const found = wanted.map((options) => options.find((option) => names.includes(option)));
  const absent = wanted.filter((_, slot) => !found[slot]).map(([first]) => first);
  if (absent.length) {
    const hints = absent.flatMap((column) => {
      const similar = names.find((option) => option.startsWith(`${column}_`));
      return similar ? [`It does have ${similar}, so rename the column to use to ${column}.`] : [];
    });
    if (names.includes("TIMESTAMP")) hints.unshift("A file with TIMESTAMP alone is usually daily or coarser, and a footprint needs half-hourly data.");
    throw new Problem(`${name} does not follow the FLUXNET format: it has no ${absent.join(", no ")}. ${hints.join(" ") || NEEDED}`);
  }
  const picks = found.map((column, slot) => ({ index: names.indexOf(column), slot }));
  return { used: found.slice(1), picks: picks.sort((a, b) => a.index - b.index) };
}

function parseTime(text) {
  const match = /^(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(\.0*)?$/.exec(text);
  if (!match) return NaN;
  const [year, month, day, hour, minute] = match.slice(1, 6).map(Number);
  return month >= 1 && month <= 12 && day >= 1 && day <= 31 && hour < 24 && minute < 60 ? Date.UTC(year, month - 1, day, hour, minute) : NaN;
}

function parseValue(text) {
  const value = text ? Number(text) : NaN;
  return value === MISSING ? NaN : value;
}

function badTimes(example, name) {
  if (/^\d(\.\d+)?e\+?\d+$/i.test(example)) {
    return `TIMESTAMP_START in ${name} was turned into a number like ${example}, probably by a spreadsheet. Keep it as text, YYYYMMDDHHMM.`;
  }
  return `TIMESTAMP_START in ${name} must read YYYYMMDDHHMM, as in the FLUXNET format, not "${example.slice(0, 24)}".`;
}

// Every row with a readable time, with NaN where a value is missing. Fields after the last wanted column are not looked at.
async function readTable(stream, size, name, report) {
  const reader = stream.getReader();
  const values = new Float64Array(WIDTH);
  let rows = new Float64Array(WIDTH * 32768);
  let count = 0;
  let columns = null;
  let unreadable = 0;
  let example = "";

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
        const text = String.fromCharCode.apply(null, chunk.subarray(from, i)).replaceAll('"', "").trim();
        values[slot] = slot ? parseValue(text) : parseTime(text);
        if (!slot && Number.isNaN(values[0])) {
          unreadable += 1;
          example ||= text;
          return;
        }
        pick += 1;
        if (pick === picks.length) break;
        wanted = picks[pick].index;
      }
      field += 1;
      from = i + 1;
    }
    if (Number.isNaN(values[0])) {     // the row stops before its time
      unreadable += 1;
      return;
    }
    if ((count + 1) * WIDTH > rows.length) {
      const longer = new Float64Array(rows.length * 2);
      longer.set(rows);
      rows = longer;
    }
    rows.set(values, count * WIDTH);
    count += 1;
  }

  function readLine(chunk, start, stop) {
    const end = stop > start && chunk[stop - 1] === RETURN ? stop - 1 : stop;
    if (end === start) return;
    if (columns) readRow(chunk, start, end);
    else if (chunk[start] !== 35) columns = findColumns(new TextDecoder().decode(chunk.subarray(start, end)), name);    // 35 is #
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
  if (!columns) throw new Problem(`${name} holds no table. ${NEEDED}`);
  if (seen !== size) throw new Problem(`The data in ${name} is damaged. Download it again.`);
  if (!count && !unreadable) throw new Problem(`${name} has column names but no rows.`);
  if (!count) throw new Problem(badTimes(example, name));
  return { rows: rows.slice(0, count * WIDTH).buffer, used: columns.used, unreadable };
}

function pickData(source, entries) {
  if (!entries.length) throw new Problem(`${source.name} has no files in it.`);
  if (entries.some((entry) => entry.name === "xl/workbook.xml")) throw new Problem(`${source.name} is an Excel workbook. Save the sheet as a CSV with commas first.`);
  const tables = entries.filter((entry) => /\.csv$/i.test(entry.name) && !COARSER_FILE.test(entry.name));
  const data = entries.filter((entry) => DATA_FILE.test(entry.name));
  if (!data.length && tables.length === 1) data.push(tables[0]);
  if (data.length) return data;
  const inner = entries.find((entry) => /\.zip$/i.test(entry.name));
  if (inner) throw new Problem(`${source.name} holds another ZIP, ${inner.name}. Unpack it, then load that one.`);
  throw new Problem(`${source.name} has no half-hourly data, which FLUXNET names ..._FLUXMET_HH_....csv.`);
}

async function read({ file, url }) {
  const source = file ? fileSource(file) : await urlSource(url);
  const notices = [];
  let opened = { stream: null, size: source.size };
  let metadata = { bif: "", varinfo: "" };
  if (await identify(source) === "text") {
    opened.stream = await source.stream(0, source.size);
  } else {
    const entries = await readDirectory(source);
    const data = pickData(source, entries);
    if (data.length > 1) notices.push(`This ZIP has ${data.length} data files. This reads ${data[0].name.split("/").pop()}, so load the others one at a time.`);
    metadata = {
      bif: await readText(source, entries.find((entry) => SITE_FILE.test(entry.name))),
      varinfo: await readText(source, entries.find((entry) => HEIGHTS_FILE.test(entry.name))),
    };
    opened = { stream: await openEntry(source, data[0]), size: data[0].size };
  }
  let shown = 0;
  const table = await readTable(opened.stream, opened.size, source.name, (share) => {
    if (share - shown < 0.01) return;
    shown = share;
    postMessage({ type: "progress", share });
  }).catch((error) => {
    if (error instanceof Problem || source.remote) throw error;
    throw new Problem(`The data in ${source.name} is damaged. Download it again.`);
  });
  postMessage({ type: "done", name: source.name, notices, ...metadata, ...table }, [table.rows]);
}

function plainError(error, { file }) {
  if (error instanceof Problem) return error.message;
  if (file) return `The browser could not read ${file.name}. It may have moved, or it may be a folder.`;
  return "The hub could not be reached.";
}

self.onmessage = ({ data }) => {
  read(data).catch((error) => postMessage({ type: "error", text: plainError(error, data) }));
};
