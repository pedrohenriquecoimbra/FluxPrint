"""Turns a file in FLUXNET format into the rows the web page computes footprints from.

The files lack mo_length, v_sigma, pblh and z - d, so they are estimated here with
fluxprint's own estimators. archive.js reads the file and tool.py calls this module,
which has no browser parts, so the tests can import it.
"""
import csv
import io
from datetime import datetime

import numpy as np

from fluxprint.micrometeorology import compute_displacement, compute_mo_length, compute_pblh, compute_std_v

RAW_COLUMNS = 7     # time, USTAR, WS, WD, H, TA, PA
DATE_FORMATS = {4: "%Y", 6: "%Y%m", 8: "%Y%m%d", 10: "%Y%m%d%H", 12: "%Y%m%d%H%M"}
UNDATED = "1900-01-01T00:00"    # in force from the start


def number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def when(text):
    """A BIF date, anything from YYYY to YYYYMMDDHHMM, as an ISO string, or None."""
    try:
        return datetime.strptime(text, DATE_FORMATS[len(text)]).isoformat(timespec="minutes")
    except (KeyError, TypeError, ValueError):
        return None


def records(bif, group):
    """One dict per entry of a BIF group, which networks call GRP_HEIGHTC or HEIGHTC."""
    found = []
    last = None
    for row in csv.DictReader(io.StringIO(bif)):
        if (row.get("VARIABLE_GROUP") or "").removeprefix("GRP_") != group:
            continue
        if row["GROUP_ID"] != last or row["VARIABLE"] in found[-1]:     # one group can hold several entries
            found.append({})
        last = row["GROUP_ID"]
        found[-1][row["VARIABLE"]] = row["DATAVALUE"]
    return found


def latest(bif, group, variable):
    """The last value of a variable in a BIF group, or None."""
    values = [entry[variable] for entry in records(bif, group) if entry.get(variable)]
    return values[-1] if values else None


def sensor_heights(varinfo):
    """The heights of the USTAR sensor, as [date, metres] pairs in time order."""
    heights = {}
    for entry in records(varinfo, "VAR_INFO"):
        height = number(entry.get("VAR_INFO_HEIGHT"))
        if entry.get("VAR_INFO_VARNAME") == "USTAR" and height and height > 0:
            heights[when(entry.get("VAR_INFO_DATE")) or UNDATED] = height
    return sorted(heights.items())


def wind_sensor(varinfo):
    """The latest instrument behind WD, or None. An ICOS sonic is named SA-..."""
    sensors = {when(entry.get("VAR_INFO_DATE")) or UNDATED: entry.get("VAR_INFO_MODEL")
               for entry in records(varinfo, "VAR_INFO") if entry.get("VAR_INFO_VARNAME") == "WD"}
    return sensors[max(sensors)] if sensors else None


def canopy_heights(bif):
    """Canopy height over time, as [date, metres] pairs in time order.

    Each date takes its largest mean, so a forest gets its tallest species rather than
    a stand mean that counts the understorey. At five forests in ICOS archives that gives
    17% below to 32% above ETC's own z - d, against 8% to 45% above with stand means.
    """
    heights = {}
    for entry in records(bif, "HEIGHTC"):
        date = when(entry.get("HEIGHTC_DATE") or entry.get("HEIGHTC_DATE_START")) or UNDATED
        height = number(entry.get("HEIGHTC"))
        if height is not None and entry.get("HEIGHTC_STATISTIC", "Mean") == "Mean" and entry.get("HEIGHTC_VEG_STATUS") != "Dead":
            heights[date] = max(height, heights.get(date, 0.0))
    return sorted(heights.items())


def in_force(readings, times, before=None):
    """The reading at each time: the latest one at or before it. Earlier times get ``before``, else the first."""
    dates = np.array([date for date, _ in readings], dtype="datetime64[m]")
    values = np.array([value for _, value in readings])
    latest_reading = np.searchsorted(dates, times, side="right") - 1
    found = values[np.maximum(latest_reading, 0)]
    return found if before is None else np.where(latest_reading < 0, before, found)


def measurement_height(times, sensor, canopy):
    """z - d at each time, NaN where the heights leave nothing above zero.

    Before the first canopy reading the median reading stands in: the first one can be
    a partial survey or bare soil, and archives often start decades earlier.
    """
    typical = float(np.median([height for _, height in canopy]))
    height = in_force(sensor, times) - compute_displacement(in_force(canopy, times, before=typical))
    return np.where(height > 0, height, np.nan)


def describe(bif, varinfo):
    """What a file's metadata says about its site. Whatever it does not say is None."""
    bif, varinfo = bif or "", varinfo or ""
    first = next(csv.DictReader(io.StringIO(bif)), {})
    sensor, canopy = sensor_heights(varinfo), canopy_heights(bif)
    span = None
    if sensor and canopy:
        changes = sorted({date for date, _ in sensor + canopy})
        heights = measurement_height(np.array(changes, dtype="datetime64[m]"), sensor, canopy)
        span = [float(np.nanmin(heights)), float(np.nanmax(heights))] if np.isfinite(heights).any() else None
    return dict(
        site=first.get("SITE_ID"), name=latest(bif, "HEADER", "SITE_NAME"), igbp=latest(bif, "IGBP", "IGBP"),
        lat=number(latest(bif, "LOCATION", "LOCATION_LAT")), lon=number(latest(bif, "LOCATION", "LOCATION_LONG")),
        utc_offset=number(latest(bif, "UTC_OFFSET", "UTC_OFFSET")), product=latest(bif, "ONEFLUX", "PRODUCT_NAME"),
        sensor=sensor, canopy=canopy, height_span=span, wind_sensor=wind_sensor(varinfo))


def rows(raw, latitude, sensor, canopy, zm=None):
    """The page's table for a file, in time order, and its row count per year.

    ``raw`` has one row per half-hour: time in ms since 1970, USTAR, WS, WD, H, TA, PA.
    The table has the columns of the baked sites: half-hours since 1 January, zm, umean,
    ustar, pblh, mo_length, v_sigma, wind_dir. A row that cannot be filled is dropped.
    ``zm`` replaces the height worked out from ``sensor`` and ``canopy``.
    """
    stamp, ustar, umean, wind_dir, heat, temperature, pressure = raw[np.unique(raw[:, 0], return_index=True)[1]].T
    time = stamp.astype("datetime64[ms]")
    year = time.astype("datetime64[Y]")
    height = np.full(len(time), float(zm)) if zm else measurement_height(time, sensor, canopy)
    with np.errstate(divide="ignore", invalid="ignore"):
        table = np.column_stack([
            (time - year) / np.timedelta64(30, "m"), height, umean, ustar,
            compute_pblh(ustar, abs(latitude)),     # with a negative latitude it returns a negative height
            compute_mo_length(ustar, heat, TA=temperature, PA=pressure), compute_std_v(ustar), wind_dir])
    keep = np.isfinite(table).all(axis=1)
    labels, counts = np.unique(year[keep].astype(int) + 1970, return_counts=True)
    return table[keep], {str(label): int(count) for label, count in zip(labels, counts)}
