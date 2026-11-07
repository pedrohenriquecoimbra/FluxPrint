"""Analytic invariants tying the hsieh2000 port to Hsieh (2000) / Detto (2006).

There is no vendored original for this model, so the regression pin is a
golden snapshot (tests/data/hsieh2000_reference.npz, wired into
test_reference_regression.py). THESE tests are the tie to the paper itself:
every check below is derived from the published equations, not from the
implementation — the cumulative footprint ``F(x) = exp(-A/x)``, the peak
distance ``A/2`` (Eq 19), and the Detto Eq B4 crosswind spread.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("rasterio")  # the package import pulls the geo stack

from fluxprint.model import available_models, get_model  # noqa: E402
from fluxprint.model.Hsieh_et_al_2000 import peak_distance  # noqa: E402

MET = dict(zm=20.0, z0=0.1, ustar=0.5, pblh=1000.0, v_sigma=0.5)
GRID = dict(domain=[-500.0, 500.0, -500.0, 500.0], dx=10.0)


def _calc(wind_dir=0.0, mo_length=-100.0, **kw):
    params = {**MET, **GRID, "wind_dir": wind_dir, "mo_length": mo_length,
              "smooth_data": 0, "verbosity": 0, **kw}
    return get_model("hsieh2000")(**params)


# --------------------------------------------------------------------------- #
# Registration / integration                                                  #
# --------------------------------------------------------------------------- #
def test_hsieh_is_registered_with_the_kernel_protocol():
    assert "hsieh2000" in available_models()
    model = get_model("hsieh2000")
    for attr in ("kernel", "resolve_grid", "validate", "model_options",
                 "option_defaults"):
        assert hasattr(model, attr)
    fp = _calc()
    assert fp.attrs["model"] == "hsieh2000"
    assert fp.attrs["model_doi"] == "10.1016/S0309-1708(99)00042-1"
    assert "captured_fraction" in fp.attrs


def test_hsieh_flows_through_batch_and_mapped_paths():
    xr = pytest.importorskip("xarray")

    from fluxprint import calculate_footprint, map_footprints

    data = {k: [v] * 2 for k, v in MET.items()}
    data["mo_length"] = [-100.0, -50.0]
    data["wind_dir"] = [30.0, 210.0]
    series = calculate_footprint(data=data, model="hsieh2000", **GRID)
    assert series[0].n == 2

    ds = xr.Dataset({k: ("time", np.asarray(v)) for k, v in data.items()})
    mapped = map_footprints(ds, model="hsieh2000", **GRID)
    single = _calc(wind_dir=30.0, mo_length=-100.0)
    assert np.array_equal(mapped.isel(time=0).values, single.f)


# --------------------------------------------------------------------------- #
# Paper-derived invariants                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mo_length", [-100.0, 1.0e6, 200.0],
                         ids=["unstable", "neutral", "stable"])
def test_captured_fraction_matches_cumulative_footprint(mo_length):
    # Hsieh Eq 17 integrates to F(X) = exp(-A/X) up to fetch X; with the wind
    # along +y and a domain reaching 500 m upwind, the field's integral must
    # equal that cumulative value (crosswind extent is ~10 sigma, negligible
    # truncation).
    fp = _calc(mo_length=mo_length)
    A = 2.0 * peak_distance(zm=MET["zm"], z0=MET["z0"], mo_length=mo_length)
    assert fp.captured_fraction == pytest.approx(np.exp(-A / 500.0), abs=0.02)


@pytest.mark.parametrize("mo_length", [-100.0, 1.0e6],
                         ids=["unstable", "neutral"])
def test_crosswind_integrated_peak_sits_at_eq19_distance(mo_length):
    # Eq 19 gives the peak of the crosswind-INTEGRATED footprint (the 2-D
    # density peaks nearer the tower, where sigma_y is still narrow), so
    # integrate across the wind first: wind from north -> sum over x.
    fp = _calc(mo_length=mo_length)
    f_ci = fp.f.sum(axis=1) * fp.dx
    y_peak = float(fp.y[int(np.argmax(f_ci))])
    expected = peak_distance(zm=MET["zm"], z0=MET["z0"], mo_length=mo_length)
    assert y_peak == pytest.approx(expected, abs=GRID["dx"])


def test_crosswind_spread_matches_detto_eq_b4():
    fp = _calc()  # wind from north: along-wind = y, crosswind = x
    iy = int(np.argmin(np.abs(fp.y - 200.0)))
    profile = fp.f[iy, :]
    x = fp.x
    sigma = np.sqrt(np.sum(profile * x**2) / np.sum(profile))
    expected = (0.3 * MET["z0"] * (MET["v_sigma"] / MET["ustar"])
                * (200.0 / MET["z0"]) ** 0.86)
    assert sigma == pytest.approx(expected, rel=0.05)


def test_stability_ordering_of_peak_distances():
    # A stable boundary layer pushes the source area farther upwind than a
    # convective one (Hsieh 2000, Fig. 5).
    kw = dict(zm=MET["zm"], z0=MET["z0"])
    assert (peak_distance(mo_length=-100.0, **kw)
            < peak_distance(mo_length=1.0e6, **kw)
            < peak_distance(mo_length=200.0, **kw))


def test_footprint_lies_upwind_of_the_tower():
    # Wind FROM the east (90 deg): the fetch — and the peak — is east.
    fp = _calc(wind_dir=90.0)
    x_peak, y_peak = fp.peak_xy()
    assert x_peak > 0
    assert abs(y_peak) <= GRID["dx"] / 2 + 1e-9


@pytest.mark.parametrize("wind_dir", [0.0, 37.0, 90.0, 135.0, 222.5, 300.0])
def test_field_is_finite_and_nonnegative_at_any_angle(wind_dir):
    fp = _calc(wind_dir=wind_dir)
    assert np.isfinite(fp.f).all()
    assert (fp.f >= 0).all()


# --------------------------------------------------------------------------- #
# Contract edges                                                              #
# --------------------------------------------------------------------------- #
def test_pblh_participates_only_in_validation():
    a = _calc(pblh=1000.0)
    b = _calc(pblh=2000.0)
    assert np.array_equal(a.f, b.f)
    # ... but zm above the boundary layer is still rejected.
    rejected = _calc(pblh=15.0)
    assert rejected.n == 0


def test_umean_only_records_are_rejected():
    met = {k: v for k, v in MET.items() if k != "z0"}
    fp = get_model("hsieh2000")(**met, umean=3.0, wind_dir=0.0,
                                mo_length=-100.0, **GRID, verbosity=0)
    assert fp.n == 0
    assert fp.attrs["flag_err"] == 1


def test_smoothing_defaults_and_generic_kernel():
    from fluxprint.footprint import smooth_field

    raw = _calc(smooth_data=0)
    smoothed = _calc(smooth_data=1)
    assert np.array_equal(smoothed.f, smooth_field(raw.f))


# --------------------------------------------------------------------------- #
# rslayer: the zm > 12.5 * z0 check is inherited from Kljun's FFP             #
# --------------------------------------------------------------------------- #
#: Inside the roughness sublayer, as at a forest.
SUBLAYER = dict(zm=20.0, z0=4.0)


def test_rslayer_is_off_by_default():
    model = get_model("hsieh2000")
    assert model.model_options == ("rslayer",)
    assert model.option_defaults == {"rslayer": 0}
    fp = _calc(**SUBLAYER)
    assert (fp.n, fp.attrs["rslayer"]) == (0, 0)


def test_rslayer_computes_a_record_inside_the_sublayer():
    fp = _calc(rslayer=1, **SUBLAYER)
    assert (fp.n, fp.attrs["flag_err"], fp.attrs["rslayer"]) == (1, 0, 1)
    # The equations are the same as above the sublayer, so Eq 17 still holds.
    A = 2.0 * peak_distance(mo_length=-100.0, **SUBLAYER)
    assert fp.captured_fraction == pytest.approx(np.exp(-A / 500.0), abs=0.02)


def test_rslayer_leaves_records_above_the_sublayer_alone():
    assert np.array_equal(_calc(rslayer=1).f, _calc().f)


@pytest.mark.parametrize("wind_dir", [0.0, 37.0, 222.5])
@pytest.mark.parametrize("ratio", [0.5, 0.9, 0.99, 1 - 1e-6])
def test_field_stays_a_density_as_z0_nears_zm(ratio, wind_dir):
    fp = _calc(rslayer=1, zm=20.0, z0=20.0 * ratio, wind_dir=wind_dir)
    assert fp.n == 1
    assert np.isfinite(fp.f).all()
    assert (fp.f >= 0).all()
    assert fp.total() <= 1.05


def test_footprint_collapses_onto_the_tower_as_z0_nears_zm():
    # zu -> 0, so the peak moves inside the first grid cell and the grid
    # samples almost none of the mass, which captured_fraction reports.
    peaks = [peak_distance(zm=20.0, z0=20.0 * ratio, mo_length=-100.0)
             for ratio in (0.5, 0.9, 0.99, 0.999)]
    assert (np.diff(peaks) < 0).all()
    assert peaks[-1] < 1e-3
    assert _calc(rslayer=1, zm=20.0, z0=19.8).captured_fraction < 0.01


@pytest.mark.parametrize("z0", [19.99999999999999, 20.0, 30.0, 60.0, 200.0],
                         ids=["rounding", "equal", "z0-1.5zm", "z0-3zm",
                              "z0-10zm"])
def test_no_footprint_at_or_below_z0(z0):
    # Unguarded, Eq 13.5 gives zu <= 0 up to z0 == zm (a zero or negative
    # field) and then grows again: z0 = 3 zm would pass for a real footprint.
    assert np.isnan(peak_distance(zm=20.0, z0=z0, mo_length=-100.0))
    fp = _calc(rslayer=1, zm=20.0, z0=z0)
    assert (fp.n, fp.attrs["flag_err"]) == (0, 1)
    assert not fp.f.any()


def _sublayer_series():
    """A record inside the sublayer, then one with no footprint."""
    met = {k: [v, v] for k, v in MET.items()}
    return {**met, "zm": [20.0, 20.0], "z0": [4.0, 30.0],
            "mo_length": [-100.0, -100.0], "wind_dir": [0.0, 0.0]}


def test_record_below_z0_leaves_the_climatology_untouched():
    both = _calc(rslayer=1, **_sublayer_series())
    assert (both.n, both.attrs["flag_err"]) == (1, 3)
    assert np.array_equal(both.f, _calc(rslayer=1, **SUBLAYER).f)


def test_rslayer_reaches_the_batch_and_mapped_paths():
    xr = pytest.importorskip("xarray")

    from fluxprint import calculate_footprint, map_footprints

    data = _sublayer_series()
    series = calculate_footprint(data=data, model="hsieh2000", rslayer=1,
                                 **GRID)
    assert series[0].n == 1

    ds = xr.Dataset({k: ("time", np.asarray(v)) for k, v in data.items()})
    assert np.isnan(map_footprints(ds, model="hsieh2000", **GRID).values).all()
    mapped = map_footprints(ds, model="hsieh2000", rslayer=1, **GRID)
    assert np.array_equal(mapped.isel(time=0).values,
                          _calc(rslayer=1, **SUBLAYER).f)
    assert np.isnan(mapped.isel(time=1).values).all()
