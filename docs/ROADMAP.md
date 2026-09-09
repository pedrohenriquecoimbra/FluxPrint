# FluxPrint roadmap

Where this library is going after **0.4.0**, and — just as importantly — where
it is deliberately not going.

This is a planning document, not a promise of dates. The binding record of what
actually shipped is [`CHANGELOG.md`](../CHANGELOG.md); the rules for
contributing to any of it are in [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Where the plan comes from

FluxPrint's first serious external integration embeds it as a compute kernel
inside an xarray/zarr/dask site-cube framework. Auditing that integration
against this codebase produced most of what follows, and it showed one thing
clearly:

> **The compute kernel is finished. What the first real integration had to
> build for itself was everything _around_ the kernel** — dataset-level input
> preparation, storage encoding, grid identity, and stack-level reductions.
> Roughly a fifth of that adapter is generic footprint code that any second
> consumer would have to write again.

Nothing on this roadmap touches the numeric path. The registered `kljun2015`
model stays pinned bitwise (`rtol=0.0`) to the vendored FFP 1.42 reference, and
`tests/test_reference_regression.py` stays green through every item below.

## Themes

| | Theme | What it is |
|---|---|---|
| **T1** | Silent wrongness | Paths that crash unhelpfully, or return a plausible wrong number with no signal. The only theme where inaction compounds: bad files outlive the release that wrote them. |
| **T2** | Input preparation as a first-class layer | `filler()` fills one key; `process_footprint_inputs()` fills a table but rejects an `xarray.Dataset` and cannot patch a NaN gap inside a present variable. A model's input requirements exist only imperatively, inside validators. |
| **T3** | Storage encoding | Integer packing already ships, and is unguarded. |
| **T4** | Grid identity and honest geometry | `resolve_grid()` silently discards arguments it does not like, and `GridSpec.dx` reports the *requested* spacing rather than the actual axis step. |
| **T5** | Mapped-path parity | `map_footprints()` diverges from the eager model call in what it accepts, what provenance it carries, and what it can express. |
| **T6** | Packaging and the release contract | The extras split, the deferred removals, and the 1.0 stability tiers. |

---

## 0.5.0 — the integration API

*Everything the first integration had to build for itself, promoted to public
API, plus the encoding bug it never reported.*

### Input preparation (T2)

- Move `ALIASES` out of `core` into a leaf module, so new modules can import it
  without a circular-import problem.
- **New `fluxprint.inputs`** — `resolve()`, `fill()`, `missing()`,
  `source_names()`, working over a `Mapping`, a `DataFrame` or an
  `xarray.Dataset`. numpy-only at module scope, so `import fluxprint` stays
  light. `process_footprint_inputs()` delegates to it rather than growing a
  sixth concern onto one pass.
  Three behaviours carried over from the downstream implementation: derive dims
  from the longest variable (a `Dataset`'s `sizes` is alphabetical, not dim
  order); apply crude constants only to *wholly absent* variables, never to NaN
  gaps; but let essential estimates patch gaps inside a variable that is
  present.
- **Declarative model inputs** — `ModelInputs` on the kernel protocol, and
  `model_inputs(model)`. Three incompatible definitions of "required" exist
  today; this reconciles them. It matters concretely: `hsieh2000` has no
  `umean` mode, yet the mapped path accepts either wind-profile input for any
  model and only the model's own validator objects, one record at a time.
- Public `resolve_model()` — the name/callable/module resolution a consumer
  currently has to copy out of a private function.

### Storage encoding (T3)

- **New `fluxprint.encoding`** — `encoding_headroom()`, `encode()`, `decode()`,
  numpy-only. An integer encoding loses a footprint at both ends: above
  `iinfo.max * 10**-decimals` cells clip flat, and below half of
  `10**-decimals` they round to zero for good. `encoding_headroom()` predicts
  both from the settings; `encode()` measures what a field actually paid.
  Loss must be measured against footprint *mass* (the sum of absolute values),
  not the signed integral — rounding errors are symmetric and cancel, so the
  obvious `1 - sum(rounded)/sum(raw)` check can never fire.
- **Make NetCDF packing safe.** `Footprint.to_netcdf(decimals=...)` currently
  sets `dtype`, `scale_factor` and `_FillValue` with no headroom check and no
  clip, so a saturating value lands on the fill sentinel and decodes back as
  NaN, and too-coarse `decimals` quantises the field toward zero — both
  silently. Values get clipped away from the sentinel, and both faults warn
  naming the workable `decimals` range.

### Grid (T4)

- Flip grid-argument coercion on, so a `domain` given as any 4-element sequence
  and `nx`/`ny` given as any integral type are honoured rather than discarded.
  **Breaking**, announced. `resolve_grid()` itself is still never changed — it
  is the verbatim reference, and coercing inside it would ship a divergence
  that nothing goes red for.
- `GridSpec.step_x` / `.step_y` / `.cell_area` / `.area` / `.is_exact`. Today
  `spec.dx` is the *requested* spacing, so on a domain that the spacing does
  not divide evenly, the reported `dx` and the real axis step disagree — and
  two parts of the library then disagree about cell area for the same grid.
- `GridSpec.to_attrs()` / `.from_attrs()` / `.matches()`, and `nx`/`ny` in the
  mapped output's attrs. Grid identity cannot come from the axes: halving `dx`
  and the domain together leaves pixel indices unchanged, so identity has to
  come from the stored `domain`/`dx`/`dy`.
- `grid_for(model, ...)` — "what grid would this model use", without running it.

### Parity and correctness (T5, T1)

- `smooth_field()` over the trailing two axes for stacked input, so smoothing a
  `(time, y, x)` cube does not need a per-record Python loop.
- Document and pin the mapped output's dimension order as contract.
- Narrow the blanket `except Exception` in the mapped per-record loop.
- `na_values=` on the input paths. Every met input is bounds-checked *except*
  `mo_length`, so a FLUXNET-style `-9999` there yields a finite, wrong field.
- Make the crude fallback tier self-consistent: a crude `umean` of 1 m/s
  produces a `ustar` below the validator's own rejection floor, so filling
  everything crudely can never produce a valid record.

---

## 0.6.0 — the cleanup, and the third model

*Every removal and install-shape change in the 0.x line in one release, so
downstream reads one migration note.*

- **The extras split.** Move the geo/plotting stack out of hard dependencies
  into the already-declared extras, so a minimal install becomes possible.
  Three prerequisites: `contourpy` becomes a real dependency (it is imported
  for contour extraction but declared nowhere, arriving only via matplotlib);
  the `all` extra gains `matplotlib`, so it genuinely reproduces the previous
  install; and the plotting entry point routes through the same `_require`
  helper as everything else.
  **The trap to defuse first:** several test modules — including the bitwise
  reference regression — guard themselves with `importorskip("rasterio")`. A
  bare-install CI job would *skip* the project's central guarantee rather than
  fail it. Re-gate those before splitting anything.
- **The coupled removal.** The legacy `io.write_to_*` writers must go together
  with `get_contour`: the legacy shapefile branch is that function's last
  caller. Also the deprecated `utils` helpers, `template.DEFAULT_ATTRS`, and
  the undocumented, unimplemented `icoscp` extra.
- **Footprint-weighted reductions over a stack** — a mapped `weighted_mean`.
  Note that there are two distinct coverage quantities and both need names: the
  fraction of the *unit-integral* footprint (absolute — needs true densities)
  and the fraction of the *on-grid* weight (relative, and therefore
  scale-invariant, which is what encoded integer weights require).
  `Footprint.weighted_mean` keeps the absolute one.
- **Integer encoding inside the mapped path**, once `fluxprint.encoding` has
  proven itself standalone. This is the one place the loss diagnostic is free,
  because the field is already an in-memory array there.
- Configurable output dimension names on the mapped path.
- **Kormann & Meixner (2001)** — the last promised model, and the thing the
  `@footprint_model` abstraction was built for. It has no vendored reference
  implementation, so it follows the `hsieh2000` precedent: a golden snapshot
  *plus* paper-derived invariants. That rule gets written into
  `CONTRIBUTING.md` rather than being rediscovered.

---

## 1.0.0 — the contract

*The surface stops moving, stated precisely enough that a downstream author can
pin `>=1,<2` and stop reading changelogs.*

A `docs/stability.md` with three tiers, machine-checked by a test that extends
the existing downstream tripwire.

- **Tier A — guaranteed, breaks only in a major.** The `Footprint` /
  `FootprintSeries` public surface and their serialization methods; the
  canonical model call signature (`smooth_data` and `verbosity` included); the
  registry and kernel protocol; the grid API; `map_footprints`,
  `calculate_footprint`, `empty_footprint`, `smooth_field`, `ALIASES`,
  `fluxprint.inputs`, `fluxprint.encoding`. Also the **module paths**
  themselves — integrations import `fluxprint.footprint`, `fluxprint.grid` and
  `fluxprint.micrometeorology` by path, so a table of bare symbol names
  promises nothing. And the **NetCDF on-disk layout**, which is the strongest
  guarantee here and the right one, because files outlive libraries.
- **Tier B — provisional.** The *values* returned by the micrometeorology
  estimators — deliberately, so approximations stay free to improve. Their
  names and signatures are Tier A: values may change, names may not.
- **Tier C — private.** Everything underscore-prefixed, engine internals beyond
  the decorator, `utils`, exception codes.

**Post-1.0 policy.** A Tier A symbol warns in release N, still works through
N+1, and is removed no earlier than N+2.

**A registered model's numbers never change** — new physics gets a new registry
name. This is stronger than most libraries promise, and the reason is concrete
rather than aesthetic: downstream caches key on the model name and compare
stored attributes, so a silent numeric change makes a stale precomputation
still *match*. Wrong data, no error, no way to detect it. The one exception is
a bug fix, which may change numbers only with a marker in `attrs` and its own
changelog section.

**Exit criterion worth keeping:** at least one external consumer pinned to a
released version in production. A stability promise made before anyone has
depended on you is a guess.

---

## Declined

Recorded here so they stop being re-litigated. A "no" is not permanent unless
it says so, but reopening one should come with new evidence.

- **zarr support.** `Footprint.to_xarray()` hands anyone a `Dataset` they can
  write to zarr in one line. Adopting it means owning chunking, consolidated
  metadata and zarr v2/v3 churn for no scientific gain.
- **A non-CF scale attribute and a sniffing decoder.** Reasonable for zarr,
  where xarray's encoding machinery consumes `scale_factor`; but FluxPrint's
  NetCDF path uses the real CF attribute, which every CF reader already
  unpacks. Upstreaming the alternative would create two competing conventions.
- **A lat/lon computation grid — permanently.** The models are formulated on a
  fixed, regular, tower-centred *metric* grid. A lon/lat grid is not
  equal-area, so `dx`/`dy` stop being constants and `total()`, `level_for()`,
  `contours()` and `weighted_mean()` all silently change meaning. `to_lonlat()`
  stays display-only; the supported path is `georeference()` to projected
  metres.
- **Wiring `compute_pblh` into the filler.** It is a bare `ustar/f` scaling
  with no Rossby coefficient, giving several times the accepted neutral value,
  and as an essential estimator it would apply without the crude tier's warning
  — silently replacing a warned constant with an unwarned wrong number.
  Revisit only once the coefficient is validated against a reference.
- **Model-name aliases in the registry.** They would serve one consumer's own
  history, and `get_model()` already names the available models when it fails.
  Aliases would either double `available_models()` — turning the
  every-model-has-an-oracle test red — or become metadata nothing consults.
- **Parsing config strings** such as `"10m"` into metres. A consumer concern.
- **Precomputation discovery and sidecar matching.** Only the grid-identity
  half generalises, and that is `GridSpec.matches()`. The rest is a data
  catalog.
- **Built-in caching of precomputed footprints.** The cache key is the model,
  every met input, and the grid; FluxPrint cannot know when the inputs changed,
  so any cache it owned would be either unsound or no cheaper than recomputing.
- **A provider/plugin framework, and entry-point model discovery.** Entry
  points would make `available_models()` depend on what else is installed,
  which breaks the test asserting every registered model has a reference
  oracle — a third-party model would fail *our* suite. `@footprint_model` is
  already the plugin API; 1.0 documents the protocol and waits for the first
  third party to ask.
- **GPU or numba acceleration — permanently.** The reference pin is bitwise
  equality against an in-process reference, and any accelerator changes float
  association order by construction. The parallelism that matters has shipped
  instead, and kept the pin: `map_footprints` parallelises across records, and
  since 0.4.0 the eager `run_climatology()` loop evaluates kernels on a thread
  pool while accumulating in record order (~8x on a multi-core machine,
  bitwise identical). Threads work here precisely because they change nothing
  about the arithmetic — which is what an accelerator cannot promise.
- **Time-series gap-filling of met inputs.** `inputs.fill()` fills missing
  *variables* and patches gaps *from physics*; interpolating a gap in time is
  the consumer's job, and doing it here would silently invent data.
- **Crude fills on by default.** A pipeline may reasonably opt into crude
  constants; a library may not choose them for you.
