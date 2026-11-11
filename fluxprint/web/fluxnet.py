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
# What each input can plausibly read, and the unit the FLUXNET format gives it.
RANGES = ((0, 10, "m/s"), (0, 60, "m/s"), (0, 360, "degrees"), (-800, 1500, "W m-2"), (-80, 60, "degrees C"), (40, 115, "kPa"))
LOW, HIGH = (np.array(column, dtype=float) for column in list(zip(*RANGES))[:2])
MAX_SECTOR_SHARE = 0.6      # one 45-degree sector above this suggests a stuck sensor
MIN_RECORDS = 48
ODD_SHARE = 0.01            # a smaller share of odd values is left out without a word: real archives have them
TALLEST_TOWER = 350.0       # m
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


def place(bif):
    """The site's latitude and longitude, or None for both if they cannot be right."""
    lat, lon = (number(latest(bif, "LOCATION", variable)) for variable in ("LOCATION_LAT", "LOCATION_LONG"))
    if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
        return None, None
    return lat, lon


def height_review(sensor, canopy):
    """The range of z - d the heights give, and what the visitor should know about it."""
    if not sensor or not canopy:
        return None, []
    changes = sorted({date for date, _ in sensor + canopy})
    heights = measurement_height(np.array(changes, dtype="datetime64[m]"), sensor, canopy)
    if not np.isfinite(heights).any():
        return None, []
    span = [float(np.nanmin(heights)), float(np.nanmax(heights))]
    notes = []
    if not np.isfinite(heights).all():
        first = changes[int(np.argmin(np.isfinite(heights)))][:10]
        notes.append(f"z - d comes out at zero or less on some dates, from {first} at first. Those half-hours are left out.")
    if span[1] > TALLEST_TOWER:
        notes.append(f"z - d reaches {span[1]:,.0f} m, so the heights in the file may not be in metres. Enter z - d if so.")
    return span, notes


def describe(bif, varinfo):
    """What a file's metadata says about its site. Whatever it does not say is None."""
    bif, varinfo = bif or "", varinfo or ""
    first = next(csv.DictReader(io.StringIO(bif)), {})
    sensor, canopy = sensor_heights(varinfo), canopy_heights(bif)
    span, notes = height_review(sensor, canopy)
    lat, lon = place(bif)
    return dict(
        site=first.get("SITE_ID"), name=latest(bif, "HEADER", "SITE_NAME"), igbp=latest(bif, "IGBP", "IGBP"), lat=lat, lon=lon,
        utc_offset=number(latest(bif, "UTC_OFFSET", "UTC_OFFSET")), product=latest(bif, "ONEFLUX", "PRODUCT_NAME"),
        sensor=sensor, canopy=canopy, height_span=span, height_notes=notes, wind_sensor=wind_sensor(varinfo))


def clean(raw):
    """A copy of the raw columns with values no sensor could give set to NaN."""
    values = raw.copy()
    with np.errstate(invalid="ignore"):
        values[:, 1:][(raw[:, 1:] < LOW) | (raw[:, 1:] > HIGH)] = np.nan
    return values


def count(number_of, thing):
    return f"{number_of:,} {thing}" + ("" if number_of == 1 else "s")


def shown(value):
    """A number as a person would quote it: 1,000 or 289.2."""
    return f"{value:,.1f}".removesuffix(".0")


def odd_values(name, given, low, high, unit):
    """What to say about the values of a column that fall outside its range, if anything."""
    outside = given[(given < low) | (given > high)]
    if not outside.size:
        return None
    values, counts = np.unique(outside, return_counts=True)
    if counts.max() > outside.size / 2 and values[counts.argmax()] < 0:
        return f"{name} has {values[counts.argmax()]:g} {count(int(counts.max()), 'time')}. The FLUXNET format marks missing values with -9999, so these count as missing."
    if outside.size > given.size / 2:
        return f"{name} reads around {shown(np.median(given))}. The FLUXNET format gives it in {unit}."
    if outside.size > ODD_SHARE * given.size:
        return f"Counted as missing: {count(outside.size, 'value')} of {name} outside {low} to {high} {unit}."
    return None


def time_step(stamps):
    """The usual minutes between records, or None for a single record."""
    return float(np.median(np.diff(stamps))) / 60e3 if len(stamps) > 1 else None


def review(raw, used, unreadable=0):
    """What a file's columns hold and what the visitor should be told.

    ``errors`` mean no footprint can come from the file, ``warnings`` that one can but
    needs a second look. ``raw`` has one row per readable time, NaN where a value is
    missing. ``used`` names the file's columns and ``unreadable`` counts rows dropped
    for their time.
    """
    errors, warnings = [], []
    good = clean(raw)
    stamps = np.unique(raw[:, 0])
    complete = np.isfinite(good[:, 1:]).all(axis=1)
    usable = np.unique(raw[complete, 0])
    held = np.isfinite(good[:, 1:]).mean(axis=0)
    odd = []
    for slot, (name, (low, high, unit)) in enumerate(zip(used, RANGES), 1):
        note = odd_values(name, raw[np.isfinite(raw[:, slot]), slot], low, high, unit)
        if note:
            odd.append(note)
    wind = good[np.isfinite(good[:, 3]), 3]
    if len(wind) >= MIN_RECORDS and wind.max() <= 2 * np.pi + 0.1:
        errors.append(f"{used[2]} stays below 6.3, so it looks like radians. The FLUXNET format gives it in degrees.")
    minutes = time_step(stamps)
    if minutes is not None and minutes >= 1440:
        days = round(minutes / 1440)
        errors.append(f"Records are {'a day' if days == 1 else f'{days} days'} apart. A footprint needs half-hourly or hourly data.")
    if not len(usable):
        errors += odd
        empty = [name for slot, name in enumerate(used, 1) if not np.isfinite(raw[:, slot]).any()]
        if empty:
            errors.append(f"{' and '.join(empty)} {'has' if len(empty) == 1 else 'have'} no values in this file, and a footprint needs them.")
        elif not odd:
            shares = ", ".join(f"{name} {share:.0%}" for name, share in zip(used, held))
            errors.append(f"Every input has values, but never in the same half-hour. Rows holding each: {shares}.")
    else:
        warnings += odd
        if minutes is not None and minutes not in (30, 60):
            warnings.append(f"Records are {minutes:g} minutes apart, and footprint models expect averages of 30 to 60 minutes.")
        if len(raw) > len(stamps):
            warnings.append(f"Left out: {count(len(raw) - len(stamps), 'row')} repeating an earlier time.")
        if unreadable:
            warnings.append(f"Left out: {count(unreadable, 'row')} with no readable TIMESTAMP_START.")
        if len(usable) < MIN_RECORDS:
            warnings.append(f"Only {len(usable)} of the records have every input, too few for a climatology.")
        elif len(usable) < len(stamps) / 2:
            warnings.append(f"Only {len(usable) / len(stamps):.0%} of records have every input, most of all for lack of {used[int(held.argmin())]}.")
        sectors = np.histogram(wind, bins=8, range=(0, 360))[0]
        if len(wind) >= MIN_RECORDS and sectors.max() / len(wind) > MAX_SECTOR_SHARE:
            warnings.append(f"{used[2]} sits in one 45-degree sector {sectors.max() / len(wind):.0%} of the time. Unless the site has such a wind, check the sensor.")
    first, last = (str(np.datetime64(int(stamp), "ms"))[:10] for stamp in (usable[[0, -1]] if len(usable) else stamps[[0, -1]]))
    return dict(total=len(stamps), usable=len(usable), first=first, last=last, minutes=minutes, errors=errors, warnings=warnings,
                columns=[dict(name=name, share=float(share)) for name, share in zip(used, held)])


def rows(raw, latitude, sensor, canopy, zm=None):
    """The page's table for a file, in time order, and its row count per year.

    ``raw`` has one row per half-hour: time in ms since 1970, USTAR, WS, WD, H, TA, PA.
    The table has the columns of the baked sites: half-hours since 1 January, zm, umean,
    ustar, pblh, mo_length, v_sigma, wind_dir. A row that cannot be filled is dropped.
    ``zm`` replaces the height worked out from ``sensor`` and ``canopy``.
    """
    stamp, ustar, umean, wind_dir, heat, temperature, pressure = clean(raw)[np.unique(raw[:, 0], return_index=True)[1]].T
    time = stamp.astype("datetime64[ms]")
    year = time.astype("datetime64[Y]")
    height = np.full(len(time), float(zm)) if zm else measurement_height(time, sensor, canopy)
    with np.errstate(divide="ignore", invalid="ignore"):
        table = np.column_stack([
            (time - year) / np.timedelta64(30, "m"), height, umean, ustar,
            compute_pblh(ustar, latitude),
            compute_mo_length(ustar, heat, TA=temperature, PA=pressure), compute_std_v(ustar), wind_dir])
    keep = np.isfinite(table).all(axis=1)
    labels, counts = np.unique(year[keep].astype(int) + 1970, return_counts=True)
    return table[keep], {str(label): int(count) for label, count in zip(labels, counts)}
