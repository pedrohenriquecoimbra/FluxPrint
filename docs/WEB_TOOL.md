# FluxPrint web tool

A static page where anyone picks an ICOS site and dates, computes the footprint
in their browser and sees it on an online map. There is no server: the page runs
the published `fluxprint` wheel through [Pyodide](https://pyodide.org).

Status: built and tested locally on 2026-11-07. Not deployed yet.

It covers 37 sites and 1.35 million half-hours in 43 MB. 22 sites have released
data from as far back as 2019, carried on by near-real-time data to last week.
The other 15 have the near-real-time window only, about 63 days.

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
- `hsieh2000` has no `rslayer` option, so the page cannot relax this check. Changing
  that needs a `fluxprint` release.
- Hsieh reaches 2 to 5 times further, so the automatic map area is wider for it
  (`reach` in `app.js`).

## Files

| Path | What it is |
| --- | --- |
| `web/` | The page: `index.html`, `app.js`, `worker.js`, `tool.py`, `style.css` |
| `scripts/build_site.py` | Reads ICOS zarr and fetches the wheels into `_site/` (not in git) |
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

Not tested: the workflow on GitHub itself, the job on the forge, and other browsers.

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
- Exports carry the ICOS citation, as CC BY 4.0 requires: NetCDF attributes,
  GeoTIFF tags, a GeoJSON member, and `CITATION.txt` in the Shapefile zip.
- Aerial tiles come from Esri and street tiles from OpenStreetMap, with attribution
  on the map. Their usage terms were not reviewed for a busy public page.

## Other data sources and hosts

Checked on 2026-11-07. The forge job is written but has not run, and the rest is not
built.

### FLUXNET Shuttle

A switch on the page would be easy, but the data behind it is not like ICOS zarr.

| | ICOS zarr, used now | FLUXNET Shuttle |
| --- | --- | --- |
| Sites | 37 on the page | 338 through the ICOS hub (June 2026 snapshot), plus AmeriFlux and TERN |
| Latest data | About two days old | Archives checked end in 2024 |
| `mo_length`, `v_sigma`, `pblh` | Per half-hour from ETC | Not in the files, so estimated, `v_sigma` and `pblh` crudely |
| `z - d` | From `ZL * MO_LENGTH` | Not in the files, see below |
| Access | Zarr, column by column | One ZIP per site, about 10 MB per site-year |

- **`z - d`:** sensor heights are in the archive metadata (`VAR_INFO_HEIGHT`). Canopy
  height (`HEIGHTC`) is in the 18 European and JapanFlux archives checked, and not in
  the 16 AmeriFlux ones, which all come from one site cluster.
- **Baking does not fit.** The ICOS hub's 2,795 site-years come to as much as 1.6 GB
  in the page's format, over the 1 GB GitHub Pages limit, from about 28 GB of archives
  per rebuild.
- **A browser cannot fetch it.** `data.icos-cp.eu` sends no CORS header, and its
  download link sets a licence cookie first. AmeriFlux downloads can also take a
  name and an intended use.

The route that fits a static page is a file option. A visitor downloads a site with
the Shuttle and drops the ZIP on the page, which reads `FLUXMET_HH`, estimates the
missing inputs with `fluxprint` and asks for `z - d` when it cannot work it out.
Nothing is hosted, so size and redistribution stop mattering, and AmeriFlux and TERN
sites work too.

The 49 ICOS sites with released data but no near-real-time data are no shortcut. Only
GL-ZaF has every input. The others lack `PBLH`, and often `MO_LENGTH` or `V_SIGMA`.

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

To set it up:

1. Create a project `fluxprint` in the `icos_ecosystemes` group. The link is then
   `https://icos_ecosystemes.pages-forge.inrae.fr/fluxprint/`.
2. Push the repository to it. This machine has no SSH key, so use HTTPS.
3. Follow the two setup lines at the top of `.gitlab-ci.yml`: public Pages and a
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
- GitLab's pull mirroring is a paid feature and push mirroring is free. So either
  push to both remotes, or work on the forge and mirror to GitHub.

## Existing tools

- [FFP Online](https://footprint.kljun.net/): you upload a time series and get
  results by email.
- [LI-COR Tovi](https://www.licor.com/support/Tovi/topics/toolbox-footprint-analysis.html):
  picks stations on a map, but is commercial.

One web search found no free tool that starts from a site and real tower data.
That search was not exhaustive.
