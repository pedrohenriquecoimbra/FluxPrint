"""Build the FluxPrint web tool into _site/, ready to serve as static files.

The page computes footprints in the visitor's browser, but it cannot read the
ICOS zarr stores itself: they send no CORS header. So this script reads them
and writes the footprint inputs as one small file per site and year.

    pip install xarray zarr fsspec aiohttp pandas requests
    python scripts/build_site.py            # every site
    python scripts/build_site.py FR-Fon     # only these sites

Each data/<SITE>/<YEAR>.bin is little-endian float32, one row per half-hour,
with the columns in COLUMNS. Column ``t`` counts half-hours from 1 January of
that year, in the site's local standard time.

Two sources are merged. Released L2 data covers the past, and near-real-time
data carries on from where it ends:

- ``zm`` is ``ZL * MO_LENGTH``, since ZL is (z - d) / L, so no tower metadata
  is needed. It is smoothed because ZL is rounded near neutral.
- Released data has no USTAR at most sites, so u* comes from TAU and air density.
- Released data has no eddy-covariance wind direction, only the wind sensors.
  The sensor is chosen by comparing the sensors with the eddy-covariance wind
  direction in the near-real-time window. A site where none agrees keeps its
  near-real-time data only.

The FLUXNET Shuttle's sites are listed in data/shuttle.json, from the three hubs the
Shuttle reads. Only the list is copied: the archives stay at their hubs.
"""
import csv
import io
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
OUT = ROOT / "_site"
NRT = "https://zarr.icos-cp.eu/icos-eco-nrt.zarr"
RELEASED = "https://zarr.icos-cp.eu/icos-fluxnet.zarr"
ICOS_SPARQL = "https://meta.icos-cp.eu/sparql"
AMERIFLUX_API = "https://amfcdn.lbl.gov/api/v2/"
TERN_FLUXNET = "https://dap.tern.org.au/thredds/fileServer/ecosystem_process/fluxnet/"

PACKAGES = ["fluxprint==0.4.1", "regorator==0.2.0"]     # served with the page, so a visitor never needs PyPI

COLUMNS = ["t", "zm", "umean", "ustar", "pblh", "mo_length", "v_sigma", "wind_dir"]
INPUTS = COLUMNS[1:]
MAX_V_SIGMA = 10.0      # m s-1; a few released records reach 155
MAX_PBLH = 8000.0       # m; a few released records reach 80 km
MAX_WD_DIFFERENCE = 20.0    # degrees between a sensor and the EC wind direction
MAX_SECTOR_SHARE = 0.6      # one 45-degree sector above this is a stuck sensor
MIN_RECORDS = 48
MIN_SITES = 20      # fewer means ICOS failed, so keep the page already online
ARCHIVE_NAME = re.compile(r"[A-Z]+_([A-Z]{2}-\w{3})_FLUXNET_(\d{4})-(\d{4})_v\d+\.\d+_r(\d+)\.zip")     # as the Shuttle requires
ICOS_ARCHIVES = """
prefix cpmeta: <http://meta.icos-cp.eu/ontologies/cpmeta/>
prefix prov: <http://www.w3.org/ns/prov#>
select ?dobj ?stationName ?fileName ?size ?lat ?lon ?ecosystemType ?citationString
where {
    ?dobj cpmeta:hasObjectSpec <http://meta.icos-cp.eu/resources/cpmeta/miscFluxnetArchiveProduct> .
    ?dobj cpmeta:wasAcquiredBy/prov:wasAssociatedWith ?station .
    ?dobj cpmeta:hasSizeInBytes ?size .
    ?dobj cpmeta:hasName ?fileName .
    ?dobj cpmeta:hasCitationString ?citationString .
    ?station cpmeta:hasName ?stationName .
    ?station cpmeta:hasLatitude ?lat .
    ?station cpmeta:hasLongitude ?lon .
    OPTIONAL { ?station cpmeta:hasEcosystemType ?ecosystemType . }
    FILTER NOT EXISTS {[] cpmeta:isNextVersionOf ?dobj}
}
"""


def sites_in(store):
    """Top-level site groups of a store."""
    names = requests.get(f"{store}/?list", timeout=60).json()
    return {n for n in names if re.fullmatch(r"[A-Z]{2}-\w{3}", n)}


def open_group(store, path):
    """One group, reading its own small metadata rather than the whole store's."""
    return xr.open_zarr(f"{store}/{path}", consolidated=True)


def smooth_zm(zl, mo_length):
    """Aerodynamic height from ZL and L, as a 5-day running median."""
    zm = (zl * mo_length).where(zl.abs() >= 0.005)
    zm = zm.where(zm > 0).rolling("5D", center=True, min_periods=10).median()
    return zm.interpolate("time", limit_direction="both")


def read_near_real_time(site):
    """The site's newest near-real-time window, with everything the model needs."""
    fluxes = open_group(NRT, f"{site}/fluxes")
    names = ["USTAR", "WS", "WD", "MO_LENGTH", "V_SIGMA", "PBLH", "ZL"]
    frame = fluxes[names].load().to_dataframe()[names]
    frame["zm"] = smooth_zm(frame.ZL, frame.MO_LENGTH)
    frame = frame.rename(columns={"USTAR": "ustar", "WS": "umean", "WD": "wind_dir", "MO_LENGTH": "mo_length",
                                  "V_SIGMA": "v_sigma", "PBLH": "pblh"})
    return frame[INPUTS], dict(fluxes.attrs)


def pick_wind_sensor(site, reference):
    """The wind sensor closest to the EC wind direction, or None if none is close."""
    sensors = open_group(NRT, f"{site}/meteosens")
    names = [v for v in sensors.data_vars if re.fullmatch(r"WD_\d+_\d+_\d+", v)]
    if not names:
        return None
    table = sensors[names].load().to_dataframe()[names].reindex(reference.index)
    best = None
    for name in names:
        difference = ((table[name] - reference + 180) % 360 - 180).dropna()
        if len(difference) < 100:
            continue
        spread = float(difference.abs().median())
        if best is None or spread < best["spread"]:
            best = dict(sensor=name[3:], spread=round(spread, 1), offset=round(float(difference.median()), 1))
    return best if best and best["spread"] <= MAX_WD_DIFFERENCE else None


def read_released(site, sensor, skipped):
    """Released half-hours from one wind sensor, or None if the site lacks an input."""
    data = open_group(RELEASED, f"{site}/meteosens")
    needed = ["WD", "MO_LENGTH", "V_SIGMA", "ZL", "PBLH", "WS_F", "TA_F", "PA_F"]
    missing = [v for v in needed if v not in data]
    if missing or not ({"USTAR", "TAU"} & set(data.variables)):
        skipped.append(f"released data lacks {', '.join(missing) or 'USTAR and TAU'}")
        return None, {}
    position = dict(zip(data.WD.dims[1:], (int(i) for i in sensor.split("_"))))
    wind = data.WD.sel(position).load().to_series()
    names = [v for v in ("USTAR", "TAU", *needed[1:]) if v in data]
    frame = data[names].load().to_dataframe()[names]
    density = frame.PA_F * 1000 / (287.05 * (frame.TA_F + 273.15))
    ustar = frame.USTAR if "USTAR" in frame else np.sqrt(frame.TAU.abs() / density)
    result = pd.DataFrame(dict(zm=smooth_zm(frame.ZL, frame.MO_LENGTH), umean=frame.WS_F, ustar=ustar, pblh=frame.PBLH,
                               mo_length=frame.MO_LENGTH, v_sigma=frame.V_SIGMA, wind_dir=wind))
    return result, dict(data.attrs)


def drop_stuck_years(frame, skipped):
    """Remove the years whose wind sensor sits in one sector."""
    keep = []
    for year, rows in frame.groupby(frame.index.year):
        counts, _ = np.histogram(rows.wind_dir.dropna() % 360, bins=8, range=(0, 360))
        if counts.sum() and counts.max() / counts.sum() > MAX_SECTOR_SHARE:
            skipped.append(f"{year}: released wind sensor looks stuck")
        else:
            keep.append(rows)
    return pd.concat(keep) if keep else frame.iloc[:0]


def clean(frame):
    """Rows with every input present and no implausible value."""
    frame = frame.dropna()
    return frame[(frame.v_sigma <= MAX_V_SIGMA) & (frame.pblh <= MAX_PBLH) & (frame.umean > 0)]


def period(frame):
    return dict(start=frame.index.min().strftime("%Y-%m-%d"), end=frame.index.max().strftime("%Y-%m-%d"))


def build_site(site, in_nrt, in_released):
    skipped, sources, parts = [], [], []
    info = {}
    wind = None
    recent = None
    if in_nrt:
        recent, info = read_near_real_time(site)
        wind = pick_wind_sensor(site, recent.wind_dir.dropna())
        if wind is None:
            skipped.append("no wind sensor agrees with the EC wind direction, so released data is left out")
    if in_released and wind:
        past, attrs = read_released(site, wind["sensor"], skipped)
        if past is not None:
            info = info or attrs
            past = clean(drop_stuck_years(past, skipped))
            if len(past) >= MIN_RECORDS:
                parts.append(past)
                sources.append(dict(kind="released", **period(past), citation=attrs.get("citation"),
                                    doi=attrs.get("collection_doi"), wind=wind))
    if recent is not None:
        recent = clean(recent)
        if parts:
            recent = recent[recent.index > parts[0].index.max()]
        if len(recent) >= MIN_RECORDS:
            parts.append(recent)
            attrs = open_group(NRT, f"{site}/fluxes").attrs
            sources.append(dict(kind="near-real-time", **period(recent), citation=attrs.get("citation"),
                                doi=attrs.get("source_doi")))
    if not parts:
        return None
    frame = pd.concat(parts)
    folder = OUT / "data" / site
    folder.mkdir(parents=True, exist_ok=True)
    years = {}
    for year, rows in frame.groupby(frame.index.year):
        start = pd.Timestamp(year=year, month=1, day=1)
        table = rows[INPUTS].copy()
        table.insert(0, "t", (rows.index - start) / pd.Timedelta(minutes=30))
        table.to_numpy(dtype="<f4").tofile(folder / f"{year}.bin")
        years[str(year)] = len(rows)
    return dict(name=info.get("station_name"), country=info.get("country"), ecosystem=info.get("ecosystem"),
                lat=round(float(info.get("latitude")), 6), lon=round(float(info.get("longitude")), 6),
                url=info.get("station_url") or info.get("icos_landing_page"), years=years, sources=sources,
                skipped=skipped)


def shuttle_entry(hub, product, name, lat, lon, igbp, link, citation, size=None):
    """A site for the page's list, or None for an archive the Shuttle would skip."""
    match = ARCHIVE_NAME.fullmatch(product)
    if not match or not citation:
        return None
    site, first, last, _ = match.groups()
    return site, dict(name=name, lat=round(float(lat), 6), lon=round(float(lon), 6), igbp=igbp, hub=hub,
                      years=[int(first), int(last)], product=product, link=link, citation=citation.strip(), size=size)


def icos_hub():
    """Sites from the ICOS hub, which also holds the European, Asian and African networks."""
    response = requests.post(ICOS_SPARQL, data=dict(query=ICOS_ARCHIVES), headers=dict(Accept="application/json"), timeout=120)
    response.raise_for_status()
    for row in response.json()["results"]["bindings"]:
        value = {key: item["value"] for key, item in row.items()}
        identifier = value["dobj"].rsplit("/", 1)[-1]
        yield shuttle_entry(
            "ICOS", value["fileName"], value["stationName"], value["lat"], value["lon"],
            value.get("ecosystemType", "").rsplit("igbp_", 1)[-1],
            f"https://data.icos-cp.eu/licence_accept?ids=%5B%22{identifier}%22%5D",
            value["citationString"], int(value["size"]))


def ameriflux_hub():
    """Sites from AmeriFlux, asked for the way the Shuttle asks."""
    response = requests.get(f"{AMERIFLUX_API}site_info_display/AmeriFlux", timeout=120)
    response.raise_for_status()
    sites = {site["site_id"]: site for site in response.json()["values"] if site.get("grp_publish_fluxnet")}
    files = requests.post(f"{AMERIFLUX_API}amf_shuttle_data_files_and_manifest", timeout=120, json=dict(
        user_id="fluxnetshuttle", data_product="FLUXNET", data_variant="FULLSET", site_ids=list(sites)))
    files.raise_for_status()
    citations = requests.post(f"{AMERIFLUX_API}citations/FLUXNET", json=dict(site_ids=list(sites)), timeout=120)
    citations.raise_for_status()
    citation = {item["site_id"]: item.get("citation") for item in citations.json()["values"]}
    for file in files.json()["data_urls"]:
        site = sites[file["site_id"]]
        place = site["grp_location"]
        yield shuttle_entry(
            "AmeriFlux", file["url"].split("?")[0].rsplit("/", 1)[-1], site["site_name"], place["location_lat"],
            place["location_long"], site["grp_igbp"].get("igbp"), file["url"], citation.get(file["site_id"]),
            file["download_size"])


def tern_hub():
    """Sites from TERN, each with its newest run."""
    def table(name):
        response = requests.get(f"{TERN_FLUXNET}{name}", timeout=120)
        response.raise_for_status()
        return csv.DictReader(io.StringIO(response.text))

    about = {}
    for row in table("BIF_all_sites.csv"):
        about.setdefault(row["SITE_ID"], {})[row["VARIABLE"]] = row["DATAVALUE"]
    products = sorted(table("TERN_THREDDS_catalogue.csv"), key=lambda row: row["PRODUCT_URL"])
    for row in products:
        site = about.get(row["SITE_ID"])
        if site:
            yield shuttle_entry(
                "TERN", row["PRODUCT_URL"].rsplit("/", 1)[-1], site.get("SITE_NAME"), site["LOCATION_LAT"],
                site["LOCATION_LONG"], site.get("IGBP"), row["PRODUCT_URL"],
                f"{row['PRODUCT_CITATION'].strip()} {row['PRODUCT_ID']}")


def shuttle_sites():
    """Every site the Shuttle offers. A hub that fails only leaves its sites out."""
    sites = {}
    for hub in (icos_hub, ameriflux_hub, tern_hub):
        try:
            found = dict(entry for entry in hub() if entry)
        except Exception as error:
            print(f"{hub.__name__}: skipped ({type(error).__name__}: {error})", flush=True)
            continue
        print(hub.__name__, len(found), "sites", flush=True)
        sites.update(found)
    return dict(sorted(sites.items()))


def download_wheels():
    """Put the packages next to the page, listed for the worker to install."""
    folder = OUT / "wheels"
    subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps", "--only-binary=:all:", "--dest", str(folder),
                    *PACKAGES], check=True)
    names = sorted(path.name for path in folder.glob("*.whl"))
    (folder / "index.json").write_text(json.dumps(names), encoding="utf-8")


def main():
    nrt, released = sites_in(NRT), sites_in(RELEASED)
    wanted = sys.argv[1:] or sorted(nrt | released)
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(WEB, OUT)
    (OUT / "data").mkdir()
    download_wheels()
    sites = {}
    for site in wanted:
        try:
            result = build_site(site, site in nrt, site in released)
        except Exception as error:     # one broken site must not stop the build
            print(f"{site}: skipped ({type(error).__name__}: {error})", flush=True)
            continue
        if result is None:
            print(f"{site}: nothing usable", flush=True)
            continue
        sites[site] = result
        print(site, sum(result["years"].values()), "half-hours,", min(result["years"]), "to", max(result["years"]),
              *result["skipped"], flush=True)
    if len(sys.argv) == 1 and len(sites) < MIN_SITES:
        sys.exit(f"Only {len(sites)} sites could be read, so the build stops here.")
    catalogue = dict(built=datetime.now(timezone.utc).strftime("%Y-%m-%d"), columns=COLUMNS, sites=sites)
    (OUT / "data" / "sites.json").write_text(json.dumps(catalogue, separators=(",", ":")), encoding="utf-8")
    listed = dict(built=catalogue["built"], sites=shuttle_sites())
    (OUT / "data" / "shuttle.json").write_text(json.dumps(listed, separators=(",", ":")), encoding="utf-8")
    print("wrote", len(sites), "sites and", len(listed["sites"]), "Shuttle sites to", OUT)


if __name__ == "__main__":
    main()
