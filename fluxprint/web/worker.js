// Runs Python off the main thread, so the map stays responsive during a run.
// It must stay a module worker: importScripts from the CDN failed in testing.
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

const EXPORT_PACKAGES = {
  netcdf: ["pyproj", "xarray", "netcdf4"],
  geotiff: ["pyproj", "rasterio"],
  shapefile: ["pyproj", "fiona", "shapely"],
};

globalThis.fpProgress = (done, total) => postMessage({ type: "progress", done, total });

async function start() {
  postMessage({ type: "status", text: "Downloading Python into this page (35 MB, kept by your browser after the first visit)" });
  const python = await loadPyodide({ stdout: () => {} });
  await python.loadPackage(["micropip", "numpy", "scipy", "pandas", "requests", "contourpy"], { messageCallback: () => {} });
  postMessage({ type: "status", text: "Loading FluxPrint" });
  const wheels = await (await fetch("wheels/index.json")).json();
  const urls = wheels.map((name) => new URL(`wheels/${name}`, self.location).href);
  await python.runPythonAsync(`
import micropip
await micropip.install(${JSON.stringify(urls)}, deps=False)
`);
  for (const module of ["fluxnet.py", "tool.py"]) {
    python.FS.writeFile(`/tmp/${module}`, await (await fetch(module)).text());
  }
  await python.runPythonAsync(`
import sys
sys.path.insert(0, "/tmp")
import tool
`);
  return python;
}

const ready = start();

function fail(id, error) {
  postMessage({ type: "error", id, text: String(error).slice(-600) });
}

async function run({ id, rows, options }) {
  const python = await ready;
  postMessage({ type: "status", text: "Computing footprints" });
  python.FS.writeFile("/tmp/rows.bin", new Uint8Array(rows));
  python.globals.set("options_text", JSON.stringify(options));
  const summary = JSON.parse(await python.runPythonAsync("tool.run(options_text)"));
  const field = summary.used ? python.FS.readFile("/tmp/field.bin").buffer : null;
  postMessage({ type: "result", id, summary, field }, field ? [field] : []);
}

async function exportFile({ id, kind }) {
  const python = await ready;
  await python.loadPackage(EXPORT_PACKAGES[kind], { messageCallback: () => {} });
  python.globals.set("export_kind", kind);
  const path = await python.runPythonAsync("tool.write_export(export_kind)");
  const bytes = python.FS.readFile(path);
  postMessage({ type: "file", id, name: path.split("/").pop(), bytes: bytes.buffer }, [bytes.buffer]);
}

async function describe({ id, raw, bif, varinfo, options }) {
  const python = await ready;
  python.FS.writeFile("/tmp/raw.bin", new Uint8Array(raw));
  python.globals.set("bif_text", bif);
  python.globals.set("varinfo_text", varinfo);
  python.globals.set("options_text", JSON.stringify(options));
  const info = JSON.parse(await python.runPythonAsync("tool.describe_file(bif_text, varinfo_text, options_text)"));
  postMessage({ type: "info", id, info });
}

async function prepare({ id, raw, options }) {
  const python = await ready;
  python.FS.writeFile("/tmp/raw.bin", new Uint8Array(raw));
  python.globals.set("options_text", JSON.stringify(options));
  const counts = JSON.parse(await python.runPythonAsync("tool.prepare(options_text)"));
  const table = python.FS.readFile("/tmp/table.bin").buffer;
  postMessage({ type: "table", id, counts, table }, [table]);
}

const JOBS = { run, export: exportFile, describe, prepare };

self.onmessage = ({ data }) => {
  JOBS[data.type](data).catch((error) => fail(data.id, error));
};

ready.then(
  () => postMessage({ type: "ready" }),
  (error) => fail(null, error),
);
