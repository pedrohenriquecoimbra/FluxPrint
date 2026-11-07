"""Broken and odd files for the web page's "Your file" tab, to run through a real browser.

The page reads files with JavaScript, so pytest cannot reach that part. To check it:

    python tests/file_cases.py _site/cases
    python -m http.server 8000 --directory _site

then open the page and run this in the browser console:

    (await import("./cases/check.js")).run()

Each case says what the visitor should be told: ``says`` must appear on the page, and
``loads`` says whether the file should then be usable. All data here is made up.
"""
import gzip
import io
import json
import random
import shutil
import struct
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

SITE = "XX-Tst"
HH_NAME = f"XX_{SITE}_FLUXNET_FLUXMET_HH_2020-2020_v1.3_r1.csv"
ARCHIVE_NAME = f"XX_{SITE}_FLUXNET_2020-2020_v1.3_r1.zip"
COLUMNS = ["TIMESTAMP_START", "TIMESTAMP_END", "TA_F", "PA_F", "WS_F", "WD", "USTAR", "H_F_MDS"]
START = datetime(2020, 6, 1)


def records(count=1440, step=30):
    """Plausible half-hours with wind from every side."""
    rng = random.Random(1)
    for index in range(count):
        time = START + timedelta(minutes=step * index)
        yield dict(
            TIMESTAMP_START=time.strftime("%Y%m%d%H%M"),
            TIMESTAMP_END=(time + timedelta(minutes=step)).strftime("%Y%m%d%H%M"),
            TA_F=f"{rng.uniform(8, 24):.3f}", PA_F=f"{rng.uniform(98, 102):.3f}", WS_F=f"{rng.uniform(1, 6):.3f}",
            WD=f"{rng.uniform(0, 360):.1f}", USTAR=f"{rng.uniform(0.15, 0.8):.3f}", H_F_MDS=f"{rng.uniform(-40, 300):.2f}")


def text(rows=None, columns=COLUMNS, sep=",", end="\n", header=None):
    rows = list(records() if rows is None else rows)
    lines = [sep.join(header or columns)] + [sep.join(str(row.get(name, -9999)) for name in columns) for row in rows]
    return (end.join(lines) + end).encode()


def changed(**edits):
    """Records with some columns replaced, each by a function of the row number and the old value."""
    rows = list(records())
    for index, row in enumerate(rows):
        for name, edit in edits.items():
            row[name] = edit(index, row[name])
    return text(rows)


def bif(lat=48.5, lon=2.5, canopy=20.0, name=ARCHIVE_NAME):
    lines = ["SITE_ID,GROUP_ID,VARIABLE_GROUP,VARIABLE,DATAVALUE", f"{SITE},1,GRP_ONEFLUX,PRODUCT_NAME,{name}",
             f"{SITE},2,GRP_HEADER,SITE_NAME,Test forest", f"{SITE},3,GRP_LOCATION,LOCATION_LAT,{lat}",
             f"{SITE},3,GRP_LOCATION,LOCATION_LONG,{lon}", f"{SITE},4,GRP_UTC_OFFSET,UTC_OFFSET,1", f"{SITE},5,GRP_IGBP,IGBP,ENF"]
    readings = canopy if isinstance(canopy, list) else [("20180101", canopy)] if canopy is not None else []
    for number, (date, height) in enumerate(readings):
        lines += [f"{SITE},{6 + number},GRP_HEIGHTC,HEIGHTC,{height}", f"{SITE},{6 + number},GRP_HEIGHTC,HEIGHTC_DATE,{date}",
                  f"{SITE},{6 + number},GRP_HEIGHTC,HEIGHTC_STATISTIC,Mean"]
    return "\n".join(lines) + "\n"


def varinfo(height=30, model="SA-Gill R3-50"):
    lines = ["SITE_ID,GROUP_ID,VARIABLE_GROUP,VARIABLE,DATAVALUE"]
    for number, variable in enumerate(("USTAR", "WD")):
        lines += [f"{SITE},{number},GRP_VAR_INFO,VAR_INFO_VARNAME,{variable}", f"{SITE},{number},GRP_VAR_INFO,VAR_INFO_DATE,201501010000",
                  f"{SITE},{number},GRP_VAR_INFO,VAR_INFO_HEIGHT,{height}", f"{SITE},{number},GRP_VAR_INFO,VAR_INFO_MODEL,{model}"]
    return "\n".join(lines) + "\n"


def archive(files=None, data=None, site=None, heights=None, method=zipfile.ZIP_DEFLATED):
    """A ZIP laid out like the Shuttle's."""
    entries = files if files is not None else {
        HH_NAME: text() if data is None else data, "XX_XX-Tst_FLUXNET_BIF_2020-2020_v1.3_r1.csv": bif() if site is None else site,
        "XX_XX-Tst_FLUXNET_BIFVARINFO_HH_2020-2020_v1.3_r1.csv": varinfo() if heights is None else heights, "README.txt": "A test."}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


def damaged():
    """A real archive with bytes overwritten in the middle of the data file."""
    data = bytearray(archive())
    middle = len(data) // 3
    data[middle:middle + 400] = random.Random(2).randbytes(400)
    return bytes(data)


def encrypted():
    """An archive whose entries are flagged as encrypted."""
    data = bytearray(archive())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = data.find(signature)
        while at >= 0:
            data[at + offset] |= 1
            at = data.find(signature, at + 4)
    return bytes(data)


def zip64():
    """An archive whose end record points to a ZIP64 one, as tools do above 4 GB."""
    data = bytearray(archive())
    end = data.rfind(b"PK\x05\x06")
    data[end + 12:end + 20] = b"\xff" * 8
    return bytes(data)


def spreadsheet():
    return archive({"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<workbook/>", "xl/worksheets/sheet1.xml": "<worksheet/>"})


def mac_archive():
    entries = {f"__MACOSX/._{HH_NAME}": b"\x00\x05\x16\x07 resource fork", f"data/{HH_NAME}": text(), "data/.DS_Store": b"\x00\x00"}
    return archive(entries)


def two_sites():
    other = HH_NAME.replace(SITE, "XX-Two")
    return archive({HH_NAME: text(), other: text()})


def table_case(name, content, says, loads=False):
    return dict(file=name, content=content, says=says, loads=loads)


def cases():
    plain = text()
    yield from [
        # Not a FLUXNET file at all.
        table_case("report.pdf", b"%PDF-1.7\n" + bytes(200), "a PDF"),
        table_case("saved_page.zip", b"<!DOCTYPE html><html><head><title>Licence</title></head><body>Accept</body></html>", "a web page"),
        table_case("data.csv.gz", gzip.compress(plain), "gzip"),
        table_case("data.rar", b"Rar!\x1a\x07\x00" + bytes(100), "RAR"),
        table_case("data.7z", b"7z\xbc\xaf\x27\x1c" + bytes(100), "7-Zip"),
        table_case("data.nc", b"\x89HDF\r\n\x1a\n" + bytes(100), "NetCDF"),
        table_case("data3.nc", b"CDF\x01" + bytes(100), "NetCDF"),
        table_case("book.xlsx", spreadsheet(), "Excel"),
        table_case("book.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(100), "Excel"),
        table_case("empty.csv", b"", "is empty"),
        table_case("empty.zip", b"", "is empty"),
        table_case("binary.dat", random.Random(3).randbytes(3000), "not a text file"),
        table_case("notes.txt", b"Tower visit on 12 May.\nAll sensors fine.\n", "does not follow the FLUXNET format"),
        # ZIPs that cannot be read.
        table_case("nothing.zip", archive({}), "no files"),
        table_case("incomplete.zip", archive()[:9000], "incomplete"),
        table_case("damaged.zip", damaged(), "damaged"),
        table_case("locked.zip", encrypted(), "password"),
        table_case("big.zip", zip64(), "ZIP64"),
        table_case("nested.zip", archive({ARCHIVE_NAME: archive()}), "another ZIP"),
        table_case("daily_only.zip", archive({HH_NAME.replace("_HH_", "_DD_"): text()}), "no half-hourly"),
        table_case("readme_only.zip", archive({"README.txt": "Nothing here."}), "no half-hourly"),
        # ZIPs that should work.
        table_case("stored.zip", archive(method=zipfile.ZIP_STORED), "", True),
        table_case("from_a_mac.zip", mac_archive(), "", True),
        table_case("in_a_folder.zip", archive({f"site/{HH_NAME}": plain, f"site/XX_{SITE}_FLUXNET_BIF_2020-2020_v1.3_r1.csv": bif()}), "", True),
        table_case("old_fullset.zip", archive({f"FLX_{SITE}_FLUXNET2015_FULLSET_HH_2020-2020_beta-3.csv": plain}), "", True),
        table_case("two_sites.zip", two_sites(), "2 data files", True),
        # CSVs saved the wrong way.
        table_case("semicolons.csv", text(sep=";"), "semicolons"),
        table_case("tabs.csv", text(sep="\t"), "tabs"),
        table_case("utf16.csv", plain.decode().encode("utf-16"), "UTF-16"),
        table_case("no_header.csv", b"\n".join(plain.split(b"\n")[1:]), "column names"),
        table_case("header_only.csv", plain.split(b"\n")[0] + b"\n", "no rows"),
        table_case("no_wd.csv", text(columns=[c for c in COLUMNS if c != "WD"]), "no WD"),
        table_case("no_wd_no_ustar.csv", text(columns=[c for c in COLUMNS if c not in ("WD", "USTAR")]), "no USTAR, no WD"),
        table_case("qualified.csv", text(header=[{"WD": "WD_1_1_1", "USTAR": "USTAR_1_1_1"}.get(c, c) for c in COLUMNS]), "WD_1_1_1"),
        table_case("only_timestamp.csv", text(header=["TIMESTAMP" if c == "TIMESTAMP_START" else c for c in COLUMNS]), "daily or coarser"),
        table_case("iso_dates.csv", changed(TIMESTAMP_START=lambda i, v: f"{v[:4]}-{v[4:6]}-{v[6:8]} {v[8:10]}:{v[10:]}"), "YYYYMMDDHHMM"),
        table_case("from_excel.csv", changed(TIMESTAMP_START=lambda i, v: "2.02E+11"), "spreadsheet"),
        table_case("ten_digits.csv", changed(TIMESTAMP_START=lambda i, v: v[:10]), "YYYYMMDDHHMM"),
        # The other files of an archive.
        table_case(f"XX_{SITE}_FLUXNET_BIF_2020-2020_v1.3_r1.csv", bif().encode(), "metadata"),
        table_case(f"XX_{SITE}_FLUXNET_BIFVARINFO_HH_2020-2020_v1.3_r1.csv", varinfo().encode(), "metadata"),
        table_case(f"XX_{SITE}_FLUXNET_ERA5_HH_1981-2025_v1.3_r1.csv", text(columns=["TIMESTAMP_START", "TIMESTAMP_END", "TA_ERA", "PA_ERA"]), "ERA5"),
        table_case(HH_NAME.replace("_HH_", "_DD_"), text(columns=["TIMESTAMP", "TA_F", "PA_F", "WS_F", "USTAR", "H_F_MDS"]), "daily"),
        # CSVs a person may have that should work.
        table_case("excel_bom.csv", b"\xef\xbb\xbf" + plain, "", True),
        table_case("windows.csv", text(end="\r\n"), "", True),
        table_case("quoted.csv", text(header=[f'"{c}"' for c in COLUMNS]), "", True),
        table_case("lowercase.csv", text(header=[c.lower() for c in COLUMNS]), "", True),
        table_case("spaces.csv", text(header=[f" {c} " for c in COLUMNS]), "", True),
        table_case("comments.csv", b"# Site: XX-Tst\n# Version: 1\n" + plain, "", True),
        table_case("plain_names.csv", text(header=[c.replace("WS_F", "WS").replace("H_F_MDS", "H").replace("TA_F", "TA").replace("PA_F", "PA") for c in COLUMNS]), "", True),
        table_case("floats.csv", changed(TIMESTAMP_START=lambda i, v: f"{v}.0"), "", True),
        table_case("nan_missing.csv", changed(WD=lambda i, v: "NaN" if i % 9 == 0 else v), "", True),
        table_case("blank_missing.csv", changed(WD=lambda i, v: "" if i % 9 == 0 else v), "", True),
        table_case("words_missing.csv", changed(WD=lambda i, v: "n/a" if i % 9 == 0 else v), "", True),
        table_case("newest_first.csv", text(reversed(list(records()))), "", True),
        # Numbers that cannot be right.
        table_case("pa_hpa.csv", changed(PA_F=lambda i, v: f"{float(v) * 10:.2f}"), "kPa"),
        table_case("pa_pa.csv", changed(PA_F=lambda i, v: f"{float(v) * 1000:.0f}"), "kPa"),
        table_case("ta_kelvin.csv", changed(TA_F=lambda i, v: f"{float(v) + 273.15:.2f}"), "degrees C"),
        table_case("ustar_cm.csv", changed(USTAR=lambda i, v: f"{float(v) * 100:.1f}"), "m/s"),
        table_case("wd_radians.csv", changed(WD=lambda i, v: f"{float(v) / 57.29578:.3f}"), "radians"),
        table_case("code_999.csv", changed(USTAR=lambda i, v: "-999" if i % 2 else v), "-999", True),
        table_case("code_6999.csv", changed(TA_F=lambda i, v: "-6999" if i % 3 == 0 else v), "-6999", True),
        table_case("some_bad_wd.csv", changed(WD=lambda i, v: "400" if i % 50 == 0 else v), "29 values of WD", True),
        table_case("stuck_vane.csv", changed(WD=lambda i, v: f"{120 + i % 7}"), "one 45", True),
        table_case("no_wd_values.csv", changed(WD=lambda i, v: "-9999"), "WD has no values"),
        table_case("never_together.csv", changed(WD=lambda i, v: v if i < 700 else "-9999", USTAR=lambda i, v: "-9999" if i < 700 else v), "same half-hour"),
        # Times that need a word.
        table_case("three_rows.csv", text(list(records())[:3]), "Only 3", True),
        table_case("repeated.csv", text(list(records()) + list(records())[:100]), "Left out: 100 rows", True),
        table_case("ten_minutes.csv", text(records(step=10)), "10 minutes", True),
        table_case("daily_steps.csv", text(records(count=60, step=1440)), "a day apart"),
        table_case("cut_off.csv", plain[:-40], "", True),
        table_case("some_bad_times.csv", changed(TIMESTAMP_START=lambda i, v: "soon" if i in (5, 6) else v), "Left out: 2 rows", True),
        table_case("second_header.csv", plain + plain, "Left out: 1 row", True),
        # Metadata that does not help.
        table_case("no_metadata.zip", archive({HH_NAME: plain}), "where the site is", True),
        table_case("bad_place.zip", archive(site=bif(lat=999)), "where the site is", True),
        table_case("zero_place.zip", archive(site=bif(lat=0, lon=0)), "where the site is", True),
        table_case("broken_metadata.zip", archive(site="this is not, a table\n\x00\x01", heights="neither is this\n"), "where the site is", True),
        table_case("no_sensor_height.zip", archive(heights=varinfo(height="")), "enter z - d", True),
        table_case("no_canopy.zip", archive(site=bif(canopy=None)), "no canopy height", True),
        table_case("canopy_above_sensor.zip", archive(site=bif(canopy=50.0)), "nothing above", True),
        table_case("canopy_part.zip", archive(site=bif(canopy=[("20200601", 10.0), ("20200610", 60.0), ("20200620", 10.0)])), "zero or less", True),
        table_case("heights_in_cm.zip", archive(heights=varinfo(height=3000), site=bif(canopy=2000.0)), "metres", True),
        table_case("vane.zip", archive(heights=varinfo(model="WIND-2DSA")), "WIND-2DSA", True),
    ]


CHECK = """// Runs every case through the page. See tests/file_cases.py.
const $ = (id) => document.getElementById(id);
const pause = (ms) => new Promise((done) => setTimeout(done, ms));

async function load(url, name) {
  const blob = await (await fetch(url)).blob();
  const transfer = new DataTransfer();
  transfer.items.add(new File([blob], name));
  $("file").files = transfer.files;
  $("file").dispatchEvent(new Event("change", { bubbles: true }));
  await pause(50);
  while (!$("progress").hidden) await pause(50);
}

export async function run(onlyFailures = true) {
  const folder = new URL(".", import.meta.url);
  document.querySelector('[data-source="file"]').click();
  const cases = await (await fetch(new URL("cases.json", folder))).json();
  const results = [];
  for (const { file, says, loads } of cases) {
    await load(new URL(encodeURIComponent(file), folder), file);
    const shown = [$("message"), $("origin"), $("coverage"), $("checks"), $("height"), $("details")].filter((box) => box && !box.hidden).map((box) => box.innerText).join(" | ");
    const loaded = !$("origin").hidden && $("origin").textContent.includes(file);
    results.push({ file, ok: loaded === loads && shown.includes(says), loaded, message: $("message").textContent, shown });
  }
  const failed = results.filter((result) => !result.ok);
  console.log(`${results.length - failed.length} of ${results.length} cases passed`);
  return onlyFailures ? failed : results;
}
"""


def main():
    folder = Path(sys.argv[1])
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    table = []
    for case in cases():
        (folder / case["file"]).write_bytes(case["content"])
        table.append({key: case[key] for key in ("file", "says", "loads")})
    (folder / "cases.json").write_text(json.dumps(table, indent=1), encoding="utf-8")
    (folder / "check.js").write_text(CHECK, encoding="utf-8")
    print("wrote", len(table), "cases to", folder)


if __name__ == "__main__":
    main()
