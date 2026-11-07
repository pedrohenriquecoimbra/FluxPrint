# FluxPrint web tool

A static page where anyone picks a site from ICOS or the FLUXNET Shuttle, or loads
a file of their own in FLUXNET format, computes the footprint in their browser and
sees it on an online map. There is no server: the page runs the published
`fluxprint` wheel through [Pyodide](https://pyodide.org).

Status: built and tested locally on 2026-11-07. Not deployed yet.

The ICOS tab covers 37 sites and 1.35 million half-hours in 43 MB. 22 sites have
released data from as far back as 2019, carried on by near-real-time data to last
week. The other 15 have the near-real-time window only, about 63 days. The FLUXNET
Shuttle tab lists 784 sites, whose data stays at the hubs.

## How a static page runs this

GitHub Pages only hands out files. The work happens on the visitor's machine: the
browser downloads Python compiled to WebAssembly, loads `fluxprint` into a
background worker of that tab and runs it there. Nothing is installed on the
computer, and the browser keeps the download for later visits.

| Host | What the page gets from it |
| --- | --- |
| GitHub Pages | The page, the site data, the `fluxprint` and `regorator` wheels |
| `cdn.jsdelivr.net` | Pyodide, its packages and Leaflet |
| `server.arcgisonline.com`, `tile.openstreetmap.org` | Map tiles |
| `dap.tern.org.au` | TERN archives, read when a visitor asks for one |

PyPI is not on the list. A failed PyPI lookup stopped the page during testing
(`ERR_NAME_NOT_RESOLVED` in the browser, so a passing network failure, not a page
bug), so the build now puts the wheels next to the page. If Python cannot be
downloaded, the page says so and the run buttons try again.

## Two ways to run

- **Run whole period** computes one climatology for the chosen dates and hours.
- **Step half-hours** computes one half-hour at a time, so the first footprint
  appears at once. A timeline under the map has a slider, previous and next
  buttons, and play.

While stepping:

- A half-hour takes about 70 ms to compute and draw, and play shows 8 per second
  (`FRAME_TIME` in `app.js`). Dragging computes only the latest position.
- The map area stays fixed for the whole period, so half-hours can be compared. A
  stable night can reach past it, and the page says so.
- A half-hour the model rejects clears the map and shows the engine's reason, for
  example `ustar (friction velocity) must be >=0.1`.
- Downloads give the half-hour on the map, and a shared link keeps it (`at=`).

## Models

The page offers the two models `fluxprint` registers: `kljun2015`, the default, and
`hsieh2000`. Results, the Cite list, downloads and their file names all name the
model, and a shared link keeps it (`model=`).

`hsieh2000` has no `umean` mode, so it needs `z0`, which ICOS does not provide.
`tool.py` estimates it for each half-hour with `compute_z0`, the same estimate
`calculate_footprint` uses, from `umean`, `ustar`, `zm` and `mo_length`. The package
marks that estimate as not yet validated.

The estimate decides how much of a period survives, because the checks reject
`zm <= 12.5 * z0`. `kljun2015` with `umean` never meets that check. For 4 October
to 1 November 2026 (FR-Fon to 2 November):

| Site | Median `z - d` | Median `z0` | Used, Kljun | Used, Hsieh | 90% reach, Kljun | 90% reach, Hsieh |
| --- | --- | --- | --- | --- | --- | --- |
| FR-Fon, forest | 20.4 m | 3.79 m | 1,297 of 1,392 | 73 | 959 m | 2,263 m |
| FI-Hyy, forest | 11.2 m | 1.57 m | 1,319 of 1,364 | 119 | 538 m | 2,805 m |
| FR-Gri, crop | 0.9 m | 0.01 m | 1,026 of 1,355 | 867 | 78 m | 290 m |
| SE-Deg, mire | 2.9 m | 0.04 m | 1,197 of 1,367 | 1,168 | 255 m | 926 m |

- At forests only 5 to 9% of half-hours pass, and those are the ones with the lowest
  estimated `z0`, so the result is not a representative climatology. When fewer than
  half pass, the page says so and gives the most common reason.
- The page cannot relax this check yet. `hsieh2000` has an `rslayer` option on `main`,
  but the page runs the published 0.4.0. Using it takes a `fluxprint` release, the new
  version in `PACKAGES`, `rslayer=1` passed from `tool.py`, a new `reach` in `app.js`
  and a note that the result is a rough estimate at forests.
- Hsieh reaches 2 to 5 times further, so the automatic map area is wider for it
  (`reach` in `app.js`).

## FLUXNET Shuttle and your own file

The tabs above the site picker are ICOS, FLUXNET Shuttle and Your file. Everything
after them works the same for all three: both ways to run, the model choice and all
four downloads. A file is never uploaded. It is read in the browser and is gone when
the page closes.

### Getting an archive

`build_site.py` writes `data/shuttle.json`, the site list the Shuttle itself would
give, with each site's citation (394 KB, 70 KB gzipped, fetched when the tab opens).

| Hub | Sites | Archive size | What a visitor does |
| --- | --- | --- | --- |
| TERN | 52 | Not listed | Clicks Read from TERN |
| ICOS, with the European, Asian and African networks | 351 | 30 GB in all | Downloads the ZIP, then loads it |
| AmeriFlux | 381 | 28 GB in all | Downloads the ZIP, then loads it |

Whether a click is enough depends on the hub's CORS headers, checked on 2026-11-07:

- **TERN** sends `Access-Control-Allow-Origin: *` and honours `Range`, so `archive.js`
  fetches only the entries it needs. Its preflight fails with 403, but Chromium sends
  a simple `Range` without one. A browser that does preflight gets a message pointing
  to the download link. That case was not tried.
- **ICOS:** `data.icos-cp.eu/objects` sends no CORS header, and its download link sets
  a licence cookie first. `/zip/<id>/listContents` is open, but `/zip/<id>/extractFile`
  answers 401 without a portal login. The page says that its link accepts the licence.
- **AmeriFlux:** the site and link API is open to browsers, but the files on
  `ftp.fluxdata.org` send no CORS header. The list uses the link the Shuttle uses.

So 732 of the 784 sites take one download. The list leaves out 3 ICOS and 26 AmeriFlux
archives in the older FULLSET format, as the Shuttle does.

### Reading a file

`archive.js` runs in its own worker, so Python and the map carry on. What a file is
decides how it is read, not its name:

- In a ZIP it reads the directory at the end of the file, then inflates only the data
  file, `BIF` and `BIFVARINFO_HH` with `DecompressionStream`. Hourly sites such as
  US-Ha1 name them `FLUXMET_HR` and `BIFVARINFO_HR`. Older archives name the data file
  `FULLSET_HH`, and `__MACOSX` entries are skipped.
- The data file is streamed and never held whole. Only the columns needed are kept, at
  most 30 MB for 30 years. A 216 MB ZIP took 4.5 s, with the page's own thread stalling
  for at most 26 ms, and a 260 MB one, 765 MB unpacked, took 5.8 s.
- It keeps `TIMESTAMP_START`, `USTAR`, `WS_F`, `WD`, `H_F_MDS`, `TA_F` and `PA_F`, with
  `-9999`, an empty field or a word as missing. A visitor's file can have `WS`, `H`, `TA`
  and `PA` instead, in any order and in any case, after `#` comment lines. A row missing
  any input is not used.
- Site ID, latitude, longitude, ecosystem and `UTC_OFFSET` come from `BIF`. ICOS names
  its groups `GRP_LOCATION` and AmeriFlux `LOCATION`, so `fluxnet.py` reads both.
- A file with no metadata, like a bare CSV, makes the page ask for latitude, longitude
  and `z - d`. A site code in the file name that the Shuttle lists fills in the place.

`fluxnet.py` then checks the columns and fills the page's usual table, so runs, stepping
and exports need no special case. It does this again if the visitor changes `z - d`.

### What a visitor is told

A local file can be wrong in many ways, so the page says what it found and what to do.
Trouble that stops a footprint shows as a message, and the file loaded before stays in
use. Anything else stays listed above the form and goes into the exports (`file_checks`).
"What the file holds" gives each input's share of records and the time step.

| What is wrong | What the page does |
| --- | --- |
| PDF, NetCDF, RAR, 7-Zip, gzip, Excel, binary | Stops, naming what the file is |
| A saved web page, a ZIP cut short | Stops, and says to download it again |
| A damaged, locked or ZIP64 archive, or one holding only another ZIP | Stops and says why |
| The archive's `BIF`, `ERA5` or daily file | Stops, and points to `FLUXMET_HH` |
| Semicolons, tabs, UTF-16, no header, no rows | Stops and says how to save it |
| A column is missing | Stops and names it, and `WD_1_1_1` if that is there instead |
| Time stamps in another form, or changed by a spreadsheet | Stops, showing what it read |
| Most of a column outside its range: hPa, Pa, Kelvin, cm/s | Stops if nothing is left, naming the unit |
| `WD` in radians, or records a day or more apart | Stops |
| An input with no values, or inputs that never meet | Stops, with each input's share |
| Another missing code, such as -999 or -6999 | Treats it as missing and warns |
| Over 1% of a column outside its range | Treats those as missing and warns |
| `WD` in one 45-degree sector over 60% of the time | Warns of a stuck sensor |
| `WD` from a sensor other than a sonic | Warns, see below |
| Repeated times, unreadable times, a step other than 30 or 60 minutes | Warns |
| Under 48 usable records, or under half | Warns, naming the shortest input |
| Several data files in a ZIP | Reads the first and warns |
| No place in the metadata, or an impossible one | Asks for latitude and longitude |
| No heights, or heights that leave nothing above `d` | Asks for `z - d` |
| `z - d` zero or less on some dates, or over 350 m | Warns |
| A typed `z - d` above the sensor height | Notes it with the result |

The ranges are in `RANGES` in `fluxnet.py`. Under 1%, odd values count as missing without
a word, because real archives have them: `USTAR` reaches 189 m/s at FI-Hyy, and a value
of 49.9 m/s sat in FR-Gri's June 2023. None of the 36 real archives here stops, and they
raise two warnings only: too few records with every input (18) and a `WD` sensor other
than a sonic (7).

### Estimated inputs

| FluxPrint | From a file |
| --- | --- |
| `ustar` | `USTAR` |
| `umean` | `WS_F` |
| `wind_dir` | `WD`, see below |
| `mo_length` | `compute_mo_length` from `USTAR`, `H_F_MDS`, `TA_F`, `PA_F` |
| `v_sigma` | `compute_std_v`: 2 `USTAR`, crude |
| `pblh` | `compute_pblh`: `USTAR` / f, crude |
| `zm` | `VAR_INFO_HEIGHT` of `USTAR`, minus 0.67 of `HEIGHTC`, see below |

The page and every export say which inputs were estimated. Against ETC's own inputs
on the same half-hours, with `kljun2015`:

| Site, month | `z - d` | `L` | `v_sigma` | `pblh` | 80% reach, ETC to estimated | 90% reach |
| --- | --- | --- | --- | --- | --- | --- |
| FR-Gri, June 2023 | 1.09 | 1.03 | 0.83 | 5.3 | 61 to 68 m | 131 to 143 m |
| BE-Lon, July 2023 | 0.79 | 1.00 | 0.88 | 4.4 | 57 to 53 m | 118 to 127 m |
| FR-Pue, July 2023 | 1.02 | 1.02 | 0.88 | 3.3 | 151 to 152 m | 313 to 317 m |
| FI-Hyy, July 2021 | 1.09 | 1.02 | 1.16 | 3.4 | 244 to 253 m | 528 to 514 m |
| DK-Sor, July 2023 | 1.09 | 1.10 | 1.22 | 4.1 | 557 to 544 m | 1,164 to 1,137 m |

The first four columns are medians of estimated over ETC. `L` has the same sign in 96 to
98% of half-hours. `pblh` is far too high, but it barely moves the footprint: most of
the change in reach comes from `z - d`, and the rest partly cancels it. Five sites and
one month each is a spot check, not a validation.

`compute_pblh` in the published 0.4.0 gives a negative height south of the equator, so
`fluxnet.py` passes it the latitude's absolute value. `main` has the fix, so this can
go once a release reaches `PACKAGES`.

### `z - d`

- The sensor height is the `USTAR` entry in force on each date. Masts that move with
  the crop are followed: FR-Gri has 35 dated heights.
- Canopy height is the largest `HEIGHTC` mean on each date, the tallest species at a
  forest. Other statistics and dead trees are left out, and a date can be a year, a day
  or a minute.
- Each half-hour takes the latest reading. Before the first one it takes the median of
  all readings, because archives can start decades earlier and the first reading can
  be bare soil (FR-Gri) or a partial survey (DK-Sor). The page says when a period falls
  outside the readings, or when one reading stands for all dates.
- A visitor can type a value, which replaces all this. Where the file lacks a height,
  the page requires one.
- A half-hour with nothing left above zero is dropped.

At five forests this gives 17% below to 32% above ETC's own `z - d`. Stand means give 8%
to 45% above it, because they count the understorey. The heights can be worked out for
all 19 ICOS-hub archives here, and for 8 of 24 AmeriFlux and 6 of 8 TERN ones taken at
random. The rest lack `HEIGHTC`, or a sensor height at AU-Ya1.

### Wind direction in the files

`WD` is taken as reported. Against the wind data of the ICOS tab, by year to 2024:

| Site | `WD` comes from | Difference |
| --- | --- | --- |
| BE-Lon, BE-Vie, NL-Loo | Sonic | -2 to +5 degrees |
| FI-Hyy | Vane | -7 degrees |
| FR-Gri | 2D sonic | -6 to -14 degrees |
| DK-Sor | 2D sonic | -30 degrees |
| FR-Pue | 2D sonic | +121 to +129 degrees from 2022, 0 in 2021 |

So when `VAR_INFO_MODEL` names something other than a sonic (`SA-...`), the page warns
that the direction needs checking. Reach and area do not depend on it, but the map does.
Which of the two is right at FR-Pue is unknown, and it is worth asking the site team.

### Links and citations

- A link can name a Shuttle site, its dates and form (`shuttle=`). Opening it selects
  the site and asks for the archive, and the form stays once it is loaded.
- A link cannot carry a file. For a file the Shuttle does not list, the page clears its
  address and says so.
- A file that matches the Shuttle's current archive gets its citation. Any other
  archive names itself and its data policy, so an older download is not cited as the
  current one.

## Files

| Path | What it is |
| --- | --- |
| `web/` | The page: `index.html`, `app.js`, `worker.js`, `tool.py`, `style.css` |
| `web/archive.js`, `web/fluxnet.py` | Reading a file, and turning it into the page's rows |
| `tests/test_web_fluxnet.py` | Metadata, checks and estimates, run natively with the package's tests |
| `tests/file_cases.py` | Makes 82 broken and odd files and a check to run them in a browser |
| `scripts/build_site.py` | Reads ICOS zarr, the Shuttle's hubs and the wheels into `_site/` (not in git) |
| `.github/workflows/pages.yml` | Builds on push and daily, deploys to GitHub Pages |
| `.gitlab-ci.yml` | The same build for GitLab Pages on forge INRAE, see below |

Run it locally:

```bash
pip install xarray zarr fsspec aiohttp pandas requests
```

```bash
python scripts/build_site.py
```

```bash
python -m http.server 8000 --directory _site
```

To publish, push to `main` and set Settings > Pages > Source to GitHub Actions
once. The URL is then `https://pedrohenriquecoimbra.github.io/FluxPrint/`.

## Why the data is baked

The page cannot read zarr itself. `zarr.icos-cp.eu` sends no
`Access-Control-Allow-Origin` header, so a browser refuses its zarr keys and
`/query` (confirmed in Chromium: `Failed to fetch`). So `build_site.py` reads
zarr at build time, the way FluxSound does, and ships every date as one small
float32 file per site and year.

If ICOS Carbon Portal added `Access-Control-Allow-Origin: *`, the page could read
zarr directly and the baked files could go. Plain GET requests need nothing else.
Only ICOS can add it, because the header has to come from their server.

It would not make the data fresher. ICOS rebuilds `icos-eco-nrt.zarr` once a night,
at about 04:00 UTC, and the page's build runs at 05:17 UTC. FR-Fon's object arrives
at about 07:30 UTC with data up to the day before, so the page trails by about two
days when ETC keeps up. That object had 12 new versions in the 19 days to 3 November
2026, and none in the 4 days after that. The whole build took about 2 minutes on the
laptop.

## Data

The footer links the ICOS Carbon Portal and its licence. Picking a site shows its
station page and a link for each of its sources. The near-real-time link is that
site's object (`source_doi`), and the released link is the collection all sites
share (`collection_doi`).

Near-real-time data carries on from where released data ends. FI-Sod, FR-Lam and
IT-SR2 have no usable rows (FR-Lam has no `PBLH` at all). Released-only sites are
left out, because their wind sensor cannot be checked.

| FluxPrint | Near-real-time, `<SITE>/fluxes` | Released, `<SITE>/meteosens` |
| --- | --- | --- |
| `ustar` | `USTAR` | `USTAR`, else `sqrt(abs(TAU) / rho)` |
| `umean` | `WS` | `WS_F` |
| `wind_dir` | `WD` (EC) | `WD` from one sensor, see below |
| `mo_length` | `MO_LENGTH` | `MO_LENGTH` |
| `v_sigma` | `V_SIGMA` | `V_SIGMA` |
| `pblh` | `PBLH` | `PBLH` |
| `zm` | `ZL * MO_LENGTH` | `ZL * MO_LENGTH` |

- **`zm`** needs no tower metadata, because `ZL` is `(z - d) / L`. It is smoothed
  with a 5-day running median, because `ZL` is rounded near neutral.
- **u\*** from `TAU` matches `USTAR` to about 1.5% in near-real-time data (median
  ratio 0.99 at FR-Fon and DE-Tha).
- **`PBLH`** is ETC's own estimate. FR-Lam has none, so it is left out.
- **Outliers:** rows with `V_SIGMA` above 10 m/s or `PBLH` above 8 km are dropped.
  Released data reaches 155 m/s and 80 km.

### Wind direction: the main caveat

Released data has no eddy-covariance wind direction, only the wind sensors, and
these differ. The build compares each sensor with the EC wind direction in the
near-real-time window and keeps the closest. If none is within 20 degrees, the
site keeps its near-real-time data only. A year where one 45-degree sector holds
over 60% of records is dropped as a stuck sensor.

- Picking by coverage alone would be wrong: at FR-Fon the most complete sensor is
  32 degrees from EC, and the closest is 8 degrees.
- The closest sensor still carries an offset (FR-Fon -8.3, FI-Hyy +8.7 degrees).
  It is not corrected, because which of the two is right is unknown. The page
  states it.
- DE-Tha's only sensor reads 0 to 45 degrees throughout the latest window, so the
  site keeps near-real-time data only.

### Open data questions

- **FR-Fon `zm`** jumps from 17.7 m (2019 to 2021) to 30 m in 2023, then 20.4 m
  from 2025. Worth checking with the site team: the footprints follow it.
- **FR-Gri against ETC.** At forests the reference FFP lands near ETC's published
  distances. Median ratios to `FETCH_MAX` are 1.02 at FR-Fon, 1.18 at FR-Hes and
  1.33 at SE-Htm. FR-Gri gives 3.75 with no correlation, and the cause is unknown.
  Which model ETC uses for those columns was not checked, so this is a sanity
  check rather than a reference.

## Measured performance

FR-Fon, `kljun2015`, 401 x 401 grid, Chromium 152, Pyodide 314.0.7, on a 24-core
laptop with a fast connection. The compute is single-threaded.

| Step | Browser | Native, serial |
| --- | --- | --- |
| Page open to Python ready | 7 to 11 s | |
| 1 half-hour, with contours | about 70 ms, drawn | about 40 ms, not drawn |
| 44 half-hours (one day) | under 1 s | |
| 950 half-hours (one month) | about 12 s | |
| 2,952 half-hours (63 days) | 17 s | 10 s |
| 12,698 half-hours (a year) | 179 s | 97 s |

The browser field matches the native one to 3.5e-16 of the peak, with the same
contour levels. It is not bit-identical. Cost per record depends on the data, so
expect 6 to 14 ms.

## What was tested

- Records used, peak, reach and area shown by the page match a separate native
  run on the same baked files, for FR-Fon (October 2026, June 2021) and FR-Gri
  (15 July 2023).
- One half-hour matches a separate native run on the same row, for FR-Fon on
  31 October at 08:30 and 1 November at 13:00: peak, reach and area.
- Footprints point the right way: wind from the west puts the footprint west.
- A period across both sources shows both citations. Day and night runs differ
  as expected: at FR-Fon in June 2021 the 80% reach is 262 m by day, 554 m at night.
- Shared links, cancel, an empty period, reversed dates and the phone layout.
- Stepping: previous, next, play, pause, dragging, rejected half-hours at FR-Gri,
  a link to one half-hour, and the timeline closing when the form changes.
- The page served under `/FluxPrint/`, as GitHub Pages will serve it, and under
  `/fluxprint/`, as the forge would.
- With micropip pointed at a dead index the page still computes, so it does not
  use PyPI. A failed Python start shows a plain message, and Run then recovers.
- Aerial and street tiles: 15 of 15 loaded for each at FR-Fon.
- All four downloads were produced from the page, for a period and for one
  half-hour. NetCDF, GeoTIFF and Shapefile from a period were also reopened and
  read correctly in the earlier browser test.
- `build_site.py` in a clean environment with only the workflow's packages.
- `hsieh2000` at FR-Fon, 4 October to 2 November 2026: records used, median `z0`,
  peak, reach and area shown by the page match a separate native run on the same
  baked file. `kljun2015` gives the same numbers as before the model choice.
- `hsieh2000` while stepping at FR-Gri: a shared link with `model=` and `at=`, a
  rejected half-hour, and all four downloads, each naming the model and its citation.
  The timeline closes when the model changes. A day where nothing passes gives the
  reason.
- All 77 ICOS links on the page open.
- Loaded files match a separate native run that reads the CSV with pandas and works out
  `z - d` apart from `fluxnet.py`: records used, peak, reach and area for FR-Gri (June
  2023, and Hsieh by day), FI-Hyy (July 2021), US-PFL (August 2019, `z - d` typed) and
  AU-Ade (read from TERN, south of the equator), and one half-hour at FR-Gri.
- Those rows are float32, as baked sites are. On doubles a native run keeps fewer
  records where `USTAR` sits on the model's limit of 0.1: 1,050 against 1,055 at FR-Gri.
- FR-Fon still gives 1,297 of 1,392, peak 36 m SW, 80% 447 m and 42.2 ha, 90% 959 m and
  181.6 ha, also under `/FluxPrint/`.
- All 35 ZIPs at hand read: 19 from the ICOS hub and 16 from AmeriFlux, plus hourly
  US-Ha1. So did a bare CSV with no metadata, other column names and CRLF line ends,
  which gave the same footprint as its archive.
- All 82 cases in `tests/file_cases.py` give the expected message in Chromium. Before the
  checks, 26 did: pressure in hPa, Kelvin and radians loaded with no word, and a Mac's
  ZIP failed.
- By hand: a hub that is missing the file and one that cannot be reached, a browser
  without `DecompressionStream` (a ZIP stops, a CSV works), a gap inside a file, several
  files dropped at once, and a file where no half-hour passes the model's checks.
- Stepping through a file: previous, next, play, dragging and rejected half-hours, at 85
  ms a footprint.
- All four downloads from a loaded file, for a period and for one half-hour, reopened
  natively. Each says which inputs were estimated, where `z - d` came from and the time
  zone. The GeoJSON of a file with a warning carries it.
- Run stays blocked until `z - d`, latitude and longitude are given. Cancel during
  reading, dropping a file on the page and a `shuttle=` link. The phone layout.
- `build_site.py` for one site in a clean environment with only the workflow's packages.
- On the three branches merged: FR-Fon as above, all 82 cases, and AU-Ade read from TERN
  on the final code (1,258 of 1,400 with Kljun, 29 with Hsieh).
- Markup in a file's name stays text in the map's tooltip. It used to run as script
  when the marker was hovered, because Leaflet shows a string as HTML.

Not tested: the workflow on GitHub itself, the job on the forge, other browsers, a real
file dialog (the tests hand the page a `File`), a folder dropped on the page, a download
through the hubs' links in a browser, and a whole FLUXNET2015 archive (`FULLSET_HH` was
tried on a made-up one, and the file names and columns of a real AmeriFlux one were read).

First-visit download, cached afterwards: 35 MB to compute, and export libraries
load only when their button is clicked (69 MB with all of them).

## Limits

- A multi-year period takes several minutes per year. The page shows progress and
  can cancel. Splitting across workers is possible, because climatologies are
  sums, but is not built.
- WebAssembly is 32-bit, so the grid stays at 401 x 401 whatever the map area.
- GitHub stops scheduled workflows after 60 days without repository activity. The
  page keeps working and shows the dates it was built with.
- Dates and times are each site's local standard time, and a half-hour is named
  by its start. Released data states both. In near-real-time data `time_end` is
  30 minutes after `time`, and the daily sunshine centre sits within 0.2 h of solar
  noon on local standard time at FR-Fon, FI-Hyy and BE-Lon.
- A new `fluxprint` release reaches the page by changing `PACKAGES` in
  `build_site.py`.
- GitHub Pages lets browsers keep files for 10 minutes, so a returning visitor can
  get a mix of old and new files just after a code change. A reload after that
  time fixes it.
- Exports carry the data citation, as CC BY 4.0 requires: NetCDF attributes,
  GeoTIFF tags, a GeoJSON member, and `CITATION.txt` in the Shapefile zip.
- FLUXNET archives also ask for an acknowledgment. The citation points to the archive's
  `DATA_POLICY_LICENSE_AND_INSTRUCTIONS.txt` rather than repeating it.
- Only the columns above are read, so a `MO_LENGTH` or `V_SIGMA` column in a visitor's
  own file is ignored.
- A wrong unit is caught only if it leaves the plausible range. `WS` in km/h or a `TA` of
  mixed units can pass.
- Daylight saving in a visitor's time stamps cannot be seen. FLUXNET's are local standard
  time.
- ZIP64 files are refused with a message. No archive here needs it.
- One file at a time: loading another replaces it.
- The labels say half-hours for an hourly file, with a note under the result.
- Aerial tiles come from Esri and street tiles from OpenStreetMap, with attribution
  on the map. Their usage terms were not reviewed for a busy public page.

## Other data sources and hosts

Checked on 2026-11-07. The forge job is written but has not run.

### FLUXNET Shuttle

Built, see [FLUXNET Shuttle and your own file](#fluxnet-shuttle-and-your-own-file).
What it cannot match in the ICOS tab:

| | ICOS zarr | FLUXNET Shuttle |
| --- | --- | --- |
| Sites | 37 | 784 |
| Latest data | About two days old | Mostly 2024 to 2026, some far older |
| `mo_length`, `v_sigma`, `pblh` | Per half-hour from ETC | Estimated, `v_sigma` and `pblh` crudely |
| `z - d` | From `ZL * MO_LENGTH` | Worked out from metadata, or typed |
| Access | Baked, one click | One click at TERN, one download at the other hubs |

**Baking does not fit,** so the hubs' sites cannot be one click. The ICOS hub's 2,795
site-years come to as much as 1.6 GB in the page's format, over the 1 GB GitHub Pages
limit, from about 28 GB of archives per rebuild.

The 49 ICOS sites with released data but no near-real-time data are no shortcut. Only
GL-ZaF has every input. The others lack `PBLH`, and often `MO_LENGTH` or `V_SIGMA`. In
the FLUXNET Shuttle tab they do work, with the estimates above.

### GitHub free plan

- The site is 42 MB of the 1 GB limit, and its largest file is 449 KB.
- The data is not in git, so daily builds do not grow the repository.
- Python comes from jsDelivr, so a visit costs GitHub about 1 MB. The soft limit is
  100 GB a month.
- The name `fluxprint` is free on GitHub. A free organisation with that name would
  give `https://fluxprint.github.io/`.

### Forge INRAE

`.gitlab-ci.yml` publishes the same build with GitLab Pages. The page should run
there, because it is static and uses relative paths, but the job has not run yet.

`test.yml` already mirrors GitHub to the forge. Once the tests pass, it pushes to the
project in the `INRAE_FORGE_REPO` secret, as it last did on 2026-09-10. So the job
reaches that project with the next push to GitHub, and no second remote is needed.
GitHub does not show a secret's value, so find the project on the forge: it holds the
deploy key `github-actions-inrae-mirror`.

To set it up:

1. The project's group sets the link. For
   `https://icos_ecosystemes.pages-forge.inrae.fr/fluxprint/` it must be `fluxprint`
   in the `icos_ecosystemes` group. If it is elsewhere, create that project, give it
   the deploy key with write access and point the secret at it.
2. Follow the two setup lines at the top of `.gitlab-ci.yml`: public Pages and a
   daily schedule.

What is known so far:

- `https://icos_ecosystemes.pages-forge.inrae.fr/observatoire-des-flux/index.html`
  opens with no login. It sends no content security policy that could block Pyodide,
  and the same 10-minute cache as GitHub.
- A namespace with a dot in it fails TLS, so a personal namespace will not do.
- GitLab has no equivalent of GitHub's 60-day pause on schedules.
- Pages must be open to everyone. Another forge site redirected to a login.
- GitLab's default limit is 100 MB per site, and the data grows by at most 20 MB a
  year. The forge's own limit was not checked.
- Whether forge runners can reach `zarr.icos-cp.eu` was not checked. If they cannot,
  the build stops and whatever is published stays online.
- GitLab's pull mirroring is a paid feature, so the mirror stays a push from GitHub.

## Existing tools

- [FFP Online](https://footprint.kljun.net/): you upload a time series and get
  results by email.
- [LI-COR Tovi](https://www.licor.com/support/Tovi/topics/toolbox-footprint-analysis.html):
  picks stations on a map, but is commercial.

One web search found no free tool that starts from a site and real tower data.
That search was not exhaustive.
