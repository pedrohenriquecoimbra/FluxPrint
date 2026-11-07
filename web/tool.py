"""The Python half of the web tool: runs fluxprint inside the browser's worker.

The worker writes the selected half-hours to /tmp/rows.bin, then calls run().
Footprint fields stay in this module so the export functions can reach them.
"""
import glob
import json
import logging
import os
import warnings
import zipfile
from collections import Counter

import numpy as np
from js import fpProgress

from fluxprint import __version__
from fluxprint.micrometeorology import compute_z0
from fluxprint.model import get_model

COLUMNS = 7     # zm, umean, ustar, pblh, mo_length, v_sigma, wind_dir
CELLS = 400     # grid cells per side, whatever the domain
FRACTIONS = (0.5, 0.8, 0.9)

footprint = None
provenance = {}
rejections = Counter()      # the engine's reasons for skipping records


class EngineLog(logging.Handler):
    """Forwards the engine's progress to the page and counts its rejections."""

    def emit(self, record):
        message = record.getMessage()
        if str(record.msg).startswith("Calculating footprint"):
            fpProgress(int(record.args[0]), int(record.args[1]))
        elif message.startswith("Error(") and not message.startswith("Error(0016)"):    # 0016 only says "skipped"
            rejections[message.splitlines()[1].strip()] += 1


logger = logging.getLogger("fluxprint")
logger.setLevel(logging.INFO)
logger.addHandler(EngineLog())


def run(options):
    """Compute one climatology and return what the page draws, as JSON."""
    global footprint, provenance
    options = json.loads(options)
    rows = np.fromfile("/tmp/rows.bin", dtype="<f4").reshape(-1, COLUMNS).astype(float)
    zm, umean, ustar, pblh, mo_length, v_sigma, wind_dir = rows.T
    half = float(options["half"])
    model = options["model"]
    provenance = options["provenance"]
    rejections.clear()
    warnings.simplefilter("ignore")
    profile = dict(umean=umean)
    if model == "hsieh2000":    # it has no umean mode, so it gets z0 as calculate_footprint would estimate it
        profile = dict(z0=compute_z0(umean, ustar, zm, ol=mo_length))
        provenance["z0"] = "estimated per half-hour from umean, ustar, zm and mo_length"
    footprint = get_model(model)(
        zm=zm, ustar=ustar, pblh=pblh, mo_length=mo_length, v_sigma=v_sigma, wind_dir=wind_dir, **profile,
        dx=2 * half / CELLS, domain=[-half, half, -half, half], verbosity=2, pulse=max(1, len(rows) // 50),
        tower=(options["lon"], options["lat"]), tower_crs="EPSG:4326")
    summary = dict(used=int(footprint.n), records=len(rows), version=__version__, model=model,
                   reason=rejections.most_common(1)[0][0] if rejections else None)
    if "z0" in profile:
        z0 = float(np.nanmedian(profile["z0"]))
        summary["z0"] = z0 if np.isfinite(z0) else None     # JSON has no NaN
    if not summary["used"]:
        return json.dumps(summary)

    # North-up rows, west to east, the way a canvas draws them.
    field = np.asarray(footprint.f, dtype="float64")
    if footprint.y[0] < footprint.y[-1]:
        field = field[::-1]
    if footprint.x[0] > footprint.x[-1]:
        field = field[:, ::-1]
    field.astype("<f4").tofile("/tmp/field.bin")

    x, y = footprint.meshgrid()
    distance = np.hypot(x, y)
    cell = footprint.dx * footprint.dy
    density = np.asarray(footprint.f)
    contours = []
    for contour in footprint.contours(FRACTIONS):
        level = float(contour["level"])
        if not np.isfinite(level):
            continue
        inside = density >= level
        contours.append(dict(
            fraction=float(contour["r"]), level=level, closed=bool(contour["closed"]),
            area_ha=float(inside.sum() * cell / 1e4), reach_m=float(distance[inside].max()),
            lines=[np.round(v, 1).tolist() for v in contour["vertices"]]))
    peak_x, peak_y = (float(v) for v in footprint.peak_xy())
    return json.dumps(dict(
        summary, captured=float(footprint.total()), size=[field.shape[1], field.shape[0]],
        half=half, peak_m=float(np.hypot(peak_x, peak_y)), peak_bearing=float(np.degrees(np.arctan2(peak_x, peak_y)) % 360),
        peak_density=float(np.nanmax(field)), zm=float(np.median(zm)), contours=contours))


def write_export(kind):
    """Write the current footprint in one format and return the file's path."""
    georeferenced = footprint.georeference()
    georeferenced.attrs.update(provenance)
    if kind == "netcdf":
        path = "/tmp/fluxprint_footprint.nc"
        georeferenced.to_netcdf(path)
    elif kind == "geotiff":
        path = "/tmp/fluxprint_footprint.tif"
        georeferenced.to_tiff(path)
        import rasterio
        with rasterio.open(path, "r+") as tiff:
            tiff.update_tags(**provenance)
    elif kind == "shapefile":
        os.makedirs("/tmp/shp", exist_ok=True)
        for old in glob.glob("/tmp/shp/*"):
            os.remove(old)
        georeferenced.to_shapefile("/tmp/shp/fluxprint_footprint.shp", rs=list(FRACTIONS))
        with open("/tmp/shp/CITATION.txt", "w", encoding="utf-8") as notes:
            notes.writelines(f"{key}: {value}\n" for key, value in provenance.items())
        path = "/tmp/fluxprint_footprint_shp.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for part in glob.glob("/tmp/shp/*"):
                archive.write(part, os.path.basename(part))
    else:
        raise ValueError(f"unknown export {kind}")
    return path
