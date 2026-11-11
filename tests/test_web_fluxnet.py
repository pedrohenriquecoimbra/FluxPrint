"""The web page's reading of FLUXNET metadata and its estimated inputs."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from fluxprint.micrometeorology import compute_mo_length

PAGE_MODULE = Path(__file__).parents[1] / "fluxprint" / "web" / "fluxnet.py"
if not PAGE_MODULE.exists():    # publish.yml tests the built wheel with a copy of tests/ alone
    pytest.skip("the web page is not next to these tests", allow_module_level=True)

spec = importlib.util.spec_from_file_location("fluxnet", PAGE_MODULE)
fluxnet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fluxnet)

HEADER = "SITE_ID,GROUP_ID,VARIABLE_GROUP,VARIABLE,DATAVALUE\n"


def bif(*groups):
    """BIF text from (group id, group name, {variable: value}) entries."""
    lines = [f"XX-Tst,{gid},{group},{variable},{value}\n"
             for gid, group, values in groups for variable, value in values.items()]
    return HEADER + "".join(lines)


def table(*records):
    """Raw columns for half-hours given as (stamp, ustar, ws, wd, h, ta, pa)."""
    rows = [(np.datetime64(stamp, "ms").astype(float), *rest) for stamp, *rest in records]
    return np.array(rows)


def test_records_reads_both_group_names_and_keeps_entries_apart():
    text = bif((1, "GRP_HEIGHTC", {"HEIGHTC": 2}), (2, "HEIGHTC", {"HEIGHTC": 3}), (3, "GRP_LAI", {"LAI": 5}))
    assert fluxnet.records(text, "HEIGHTC") == [{"HEIGHTC": "2"}, {"HEIGHTC": "3"}]


def test_records_splits_entries_sharing_a_group_id():
    text = HEADER + "XX-Tst,1,HEIGHTC,HEIGHTC,2\nXX-Tst,1,HEIGHTC,HEIGHTC,3\n"
    assert fluxnet.records(text, "HEIGHTC") == [{"HEIGHTC": "2"}, {"HEIGHTC": "3"}]


@pytest.mark.parametrize("text, expected", [
    ("2011", "2011-01-01T00:00"), ("20190420", "2019-04-20T00:00"), ("201707101230", "2017-07-10T12:30"),
    ("", None), (None, None), ("2019042", None), ("20191340", None),
])
def test_when_reads_every_date_form(text, expected):
    assert fluxnet.when(text) == expected


def test_canopy_takes_each_dates_largest_living_mean():
    text = bif(
        (1, "GRP_HEIGHTC", {"HEIGHTC": 20, "HEIGHTC_STATISTIC": "Mean", "HEIGHTC_SPP": "Pinus", "HEIGHTC_DATE": 20200101}),
        (2, "GRP_HEIGHTC", {"HEIGHTC": 9, "HEIGHTC_STATISTIC": "Mean", "HEIGHTC_SPP": "Alnus", "HEIGHTC_DATE": 20200101}),
        (3, "GRP_HEIGHTC", {"HEIGHTC": 15, "HEIGHTC_STATISTIC": "Mean", "HEIGHTC_DATE": 20200101}),
        (4, "GRP_HEIGHTC", {"HEIGHTC": 31, "HEIGHTC_STATISTIC": "Maximum", "HEIGHTC_DATE": 20200101}),
        (5, "GRP_HEIGHTC", {"HEIGHTC": 3, "HEIGHTC_STATISTIC": "Standard Deviation", "HEIGHTC_DATE": 20200101}),
        (6, "GRP_HEIGHTC", {"HEIGHTC": 40, "HEIGHTC_STATISTIC": "Mean", "HEIGHTC_VEG_STATUS": "Dead", "HEIGHTC_DATE": 20200101}),
        (7, "GRP_HEIGHTC", {"HEIGHTC_STATISTIC": "Mean", "HEIGHTC_DATE": 20210101}),
        (8, "GRP_HEIGHTC", {"HEIGHTC": 1.5, "HEIGHTC_DATE_START": 20190601, "HEIGHTC_DATE_END": 20190602}),
    )
    assert fluxnet.canopy_heights(text) == [("2019-06-01T00:00", 1.5), ("2020-01-01T00:00", 20.0)]


def test_sensor_heights_come_from_ustar_alone_in_time_order():
    text = bif(
        (1, "GRP_VAR_INFO", {"VAR_INFO_VARNAME": "USTAR", "VAR_INFO_DATE": 202001010000, "VAR_INFO_HEIGHT": 30}),
        (2, "GRP_VAR_INFO", {"VAR_INFO_VARNAME": "TA_F", "VAR_INFO_DATE": 201001010000, "VAR_INFO_HEIGHT": 2}),
        (3, "GRP_VAR_INFO", {"VAR_INFO_VARNAME": "USTAR", "VAR_INFO_DATE": 201501010000, "VAR_INFO_HEIGHT": 24}),
        (4, "GRP_VAR_INFO", {"VAR_INFO_VARNAME": "USTAR", "VAR_INFO_DATE": 201601010000}),
    )
    assert fluxnet.sensor_heights(text) == [("2015-01-01T00:00", 24.0), ("2020-01-01T00:00", 30.0)]


def test_height_follows_the_dated_readings():
    sensor = [("2015-01-01T00:00", 3.0), ("2020-01-01T00:00", 5.0)]
    canopy = [("2018-01-01T00:00", 0.0), ("2019-01-01T00:00", 1.0), ("2021-01-01T00:00", 3.0)]
    times = np.array(["2010-06-01", "2018-06-01", "2020-06-01", "2022-06-01"], dtype="datetime64[m]")
    assert fluxnet.measurement_height(times, sensor, canopy) == pytest.approx(
        [3 - 0.67, 3, 5 - 0.67, 5 - 0.67 * 3])    # the first time is before any canopy reading, so it gets the median


def test_height_above_nothing_is_missing():
    sensor = [("2015-01-01T00:00", 2.0)]
    canopy = [("2015-01-01T00:00", 4.0)]
    assert np.isnan(fluxnet.measurement_height(np.array(["2016-01-01"], dtype="datetime64[m]"), sensor, canopy)).all()


def test_describe_reads_a_network_without_group_prefixes():
    text = bif((1, "HEADER", {"SITE_NAME": "Test"}), (2, "LOCATION", {"LOCATION_LAT": 45.5, "LOCATION_LONG": -90.25}),
               (3, "UTC_OFFSET", {"UTC_OFFSET": -6}), (4, "IGBP", {"IGBP": "DBF"}))
    info = fluxnet.describe(text, "")
    assert (info["site"], info["name"], info["lat"], info["lon"], info["utc_offset"], info["igbp"]) == (
        "XX-Tst", "Test", 45.5, -90.25, -6.0, "DBF")
    assert (info["sensor"], info["canopy"], info["height_span"]) == ([], [], None)


def test_describe_of_a_file_without_metadata_is_empty():
    assert fluxnet.describe("", "") == dict(site=None, name=None, igbp=None, lat=None, lon=None, utc_offset=None, product=None,
                                            sensor=[], canopy=[], height_span=None, height_notes=[], wind_sensor=None)


def test_wind_sensor_is_the_latest_one():
    text = bif((1, "GRP_VAR_INFO", {"VAR_INFO_VARNAME": "WD", "VAR_INFO_DATE": 202102221300, "VAR_INFO_MODEL": "WIND-2DSA"}),
               (2, "GRP_VAR_INFO", {"VAR_INFO_VARNAME": "WD", "VAR_INFO_DATE": 199606010000, "VAR_INFO_MODEL": "WIND-VaneAn"}),
               (3, "GRP_VAR_INFO", {"VAR_INFO_VARNAME": "USTAR", "VAR_INFO_DATE": 202201010000, "VAR_INFO_MODEL": "SA-Gill R3-50"}))
    assert fluxnet.wind_sensor(text) == "WIND-2DSA"


def test_rows_match_the_baked_columns():
    raw = table(("2023-01-01T00:30", 0.4, 3.0, 200.0, 50.0, 10.0, 100.0), ("2023-07-01T12:00", 0.5, 2.0, 90.0, -20.0, 20.0, 99.0))
    rows, years = fluxnet.rows(raw, 48.0, [], [], zm=12.0)
    assert years == {"2023": 2}
    assert rows[:, 0] == pytest.approx([1, 181 * 48 + 24])
    assert rows[:, 1] == pytest.approx([12, 12])
    assert rows[:, 2] == pytest.approx([3, 2])
    assert rows[:, 3] == pytest.approx([0.4, 0.5])
    assert rows[:, 5] == pytest.approx(compute_mo_length(raw[:, 1], raw[:, 4], TA=raw[:, 5], PA=raw[:, 6]))
    assert rows[:, 6] == pytest.approx([0.8, 1.0])
    assert rows[:, 7] == pytest.approx([200, 90])
    assert rows[0, 5] < 0 < rows[1, 5]      # upward heat flux is unstable, downward stable


def test_rows_sort_split_by_year_and_drop_duplicates():
    raw = table(("2024-01-01T00:00", 0.4, 3.0, 200.0, 50.0, 10.0, 100.0), ("2023-12-31T23:30", 0.4, 3.0, 200.0, 50.0, 10.0, 100.0),
                ("2024-01-01T00:00", 0.9, 3.0, 200.0, 50.0, 10.0, 100.0))
    rows, years = fluxnet.rows(raw, 48.0, [], [], zm=12.0)
    assert years == {"2023": 1, "2024": 1}
    assert rows[:, 0] == pytest.approx([364 * 48 + 47, 0])


def month(ustar=None, ws=None, wd=None, heat=None, ta=None, pa=None, count=1440, step=30):
    """A month of plausible raw columns, with any column replaced."""
    rng = np.random.default_rng(1)
    time = (np.datetime64("2020-06-01T00:00", "ms") + np.arange(count) * np.timedelta64(step, "m")).astype(float)
    defaults = dict(ustar=rng.uniform(0.15, 0.8, count), ws=rng.uniform(1, 6, count), wd=rng.uniform(0, 360, count),
                    heat=rng.uniform(-40, 300, count), ta=rng.uniform(8, 24, count), pa=rng.uniform(98, 102, count))
    given = dict(ustar=ustar, ws=ws, wd=wd, heat=heat, ta=ta, pa=pa)
    return np.column_stack([time] + [defaults[key] if given[key] is None else given[key] + 0 * defaults[key] for key in defaults])


USED = ["USTAR", "WS_F", "WD", "H_F_MDS", "TA_F", "PA_F"]


def test_review_of_a_sound_file_says_nothing():
    result = fluxnet.review(month(), USED)
    assert (result["errors"], result["warnings"]) == ([], [])
    assert (result["total"], result["usable"], result["minutes"]) == (1440, 1440, 30)
    assert (result["first"], result["last"]) == ("2020-06-01", "2020-06-30")
    assert [column["share"] for column in result["columns"]] == [1] * 6


@pytest.mark.parametrize("column, scale, offset, name, unit", [
    ("pa", 10, 0, "PA_F", "kPa"), ("pa", 1000, 0, "PA_F", "kPa"), ("ta", 1, 273.15, "TA_F", "degrees C"), ("ustar", 100, 0, "USTAR", "m/s"),
])
def test_review_stops_a_file_in_the_wrong_unit(column, scale, offset, name, unit):
    raw = month()
    slot = dict(ustar=1, ta=5, pa=6)[column]
    raw[:, slot] = raw[:, slot] * scale + offset
    result = fluxnet.review(raw, USED)
    assert result["usable"] == 0
    assert len(result["errors"]) == 1
    assert name in result["errors"][0] and unit in result["errors"][0]


def test_review_stops_radians():
    raw = month()
    raw[:, 3] = np.radians(raw[:, 3])
    assert "radians" in fluxnet.review(raw, USED)["errors"][0]


def test_review_treats_another_missing_code_as_missing():
    raw = month()
    raw[::2, 1] = -999
    result = fluxnet.review(raw, USED)
    assert result["usable"] == 720
    assert "-999" in result["warnings"][0] and "-9999" in result["warnings"][0]
    assert len(fluxnet.rows(raw, 48.0, [], [], zm=12.0)[0]) == 720


def test_review_counts_some_odd_values():
    raw = month()
    raw[::50, 3] = 400
    result = fluxnet.review(raw, USED)
    assert result["usable"] == 1440 - 29
    assert result["warnings"] == ["Counted as missing: 29 values of WD outside 0 to 360 degrees."]


def test_review_passes_over_a_stray_value():
    raw = month()
    raw[7, 6] = 200
    result = fluxnet.review(raw, USED)
    assert (result["usable"], result["warnings"]) == (1439, [])


def test_review_names_an_empty_column():
    raw = month()
    raw[:, 3] = np.nan
    assert fluxnet.review(raw, USED)["errors"] == ["WD has no values in this file, and a footprint needs them."]


def test_review_explains_inputs_that_never_meet():
    raw = month()
    raw[:700, 1] = np.nan
    raw[700:, 3] = np.nan
    error = fluxnet.review(raw, USED)["errors"][0]
    assert "never in the same half-hour" in error and "USTAR 51%" in error and "WD 49%" in error


def test_review_stops_daily_data():
    assert fluxnet.review(month(count=60, step=1440), USED)["errors"][0].startswith("Records are a day apart")
    assert fluxnet.review(month(count=60, step=7 * 1440), USED)["errors"][0].startswith("Records are 7 days apart")


@pytest.mark.parametrize("step, says", [(10, "10 minutes apart"), (180, "180 minutes apart")])
def test_review_questions_an_unusual_step(step, says):
    assert says in fluxnet.review(month(step=step), USED)["warnings"][0]


def test_review_accepts_hourly_data():
    result = fluxnet.review(month(step=60), USED)
    assert (result["minutes"], result["warnings"]) == (60, [])


def test_review_reports_repeated_and_unreadable_rows():
    raw = month()
    result = fluxnet.review(np.vstack([raw, raw[:5]]), USED, unreadable=1)
    assert result["usable"] == 1440
    assert result["warnings"] == ["Left out: 5 rows repeating an earlier time.", "Left out: 1 row with no readable TIMESTAMP_START."]


def test_review_warns_of_a_short_or_patchy_file():
    assert "Only 3" in fluxnet.review(month(count=3), USED)["warnings"][0]
    raw = month()
    raw[:1000, 1] = np.nan
    assert fluxnet.review(raw, USED)["warnings"] == ["Only 31% of records have every input, most of all for lack of USTAR."]


def test_review_suspects_a_stuck_vane():
    raw = month(wd=120.0)
    assert "one 45-degree sector 100%" in fluxnet.review(raw, USED)["warnings"][0]


def test_place_refuses_what_cannot_be_right():
    def located(lat, lon):
        return fluxnet.place(bif((1, "LOCATION", {"LOCATION_LAT": lat, "LOCATION_LONG": lon})))

    assert located(48.5, 2.5) == (48.5, 2.5)
    assert located(-13.1, 131.1) == (-13.1, 131.1)
    assert located(999, 2.5) == (None, None)
    assert located(48.5, 999) == (None, None)
    assert located(0, 0) == (None, None)
    assert located("north", 2.5) == (None, None)


def test_height_review_flags_dates_with_nothing_left_and_odd_units():
    sensor = [("2015-01-01T00:00", 30.0)]
    span, notes = fluxnet.height_review(sensor, [("2020-06-01T00:00", 10.0), ("2020-06-10T00:00", 60.0)])
    assert span == pytest.approx([6.55, 23.3])     # the sensor's date comes before any canopy reading, so it takes their median
    assert "from 2020-06-10" in notes[0]
    span, notes = fluxnet.height_review([("2015-01-01T00:00", 3000.0)], [("2018-01-01T00:00", 2000.0)])
    assert span == pytest.approx([1660, 1660])
    assert "not be in metres" in notes[0]
    assert fluxnet.height_review(sensor, [("2020-06-01T00:00", 60.0)]) == (None, [])


def test_boundary_layer_is_positive_in_both_hemispheres():
    raw = table(("2023-01-01T00:30", 0.4, 3.0, 200.0, 50.0, 10.0, 100.0))
    north, _ = fluxnet.rows(raw, 35.0, [], [], zm=12.0)
    south, _ = fluxnet.rows(raw, -35.0, [], [], zm=12.0)
    assert north[0, 4] == pytest.approx(south[0, 4])
    assert south[0, 4] > 0


def test_rows_without_a_height_are_dropped():
    raw = table(("2023-01-01T00:30", 0.4, 3.0, 200.0, 50.0, 10.0, 100.0), ("2024-01-01T00:30", 0.4, 3.0, 200.0, 50.0, 10.0, 100.0))
    sensor = [("2020-01-01T00:00", 2.0)]
    canopy = [("2020-01-01T00:00", 1.0), ("2024-01-01T00:00", 4.0)]
    rows, years = fluxnet.rows(raw, 48.0, sensor, canopy)
    assert years == {"2023": 1}
    assert rows[0, 1] == pytest.approx(2 - 0.67)
