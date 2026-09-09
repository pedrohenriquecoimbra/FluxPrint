"""Tests for fluxprint.model.engine: the generic model pipeline.

A toy Gaussian kernel registered via ``@footprint_model`` must get the whole
driver for free — input normalization, grid, climatology loop, smoothing,
provenance, ``captured_fraction`` — and flow through ``calculate_footprint``
like any built-in model.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("rasterio")  # the package import pulls the geo stack

from fluxprint.exceptions import InputValidationError  # noqa: E402
from fluxprint.footprint import Footprint, smooth_field  # noqa: E402
from fluxprint.grid import resolve_grid  # noqa: E402
from fluxprint.model import (MODELS, FootprintModel, available_models,  # noqa: E402
                             footprint_model, get_model)
from fluxprint.model.engine import (listify, normalize_inputs,  # noqa: E402
                                    resolve_workers)

SIGMA = 60.0

MET = dict(zm=20.0, ustar=0.5, pblh=1000.0, mo_length=-100.0, v_sigma=0.5,
           wind_dir=0.0, z0=0.1)
GRID = dict(domain=[-300.0, 300.0, -300.0, 300.0], dx=10.0)


def _gauss_field(ctx):
    return (np.exp(-(ctx.x_2d**2 + ctx.y_2d**2) / (2 * SIGMA**2))
            / (2 * np.pi * SIGMA**2))


@pytest.fixture
def toy_model():
    @footprint_model("toy_gauss", description="toy Gaussian",
                     meta={"model_doi": "10.0000/toy"},
                     options=("width",), defaults={"width": 1.0})
    def kernel(ctx, rec, opts):
        return _gauss_field(ctx), 0, 1

    try:
        yield get_model("toy_gauss")
    finally:
        MODELS.pop("toy_gauss", None)


@pytest.fixture
def flaky_model():
    # Stable records (mo_length > 0) turn invalid mid-computation (flag 3).
    @footprint_model("toy_flaky", validate=lambda rec, opts, verbosity: True)
    def kernel(ctx, rec, opts):
        if rec.mo_length > 0:
            return np.zeros(ctx.x_2d.shape), 3, 0
        return _gauss_field(ctx), 0, 1

    try:
        yield get_model("toy_flaky")
    finally:
        MODELS.pop("toy_flaky", None)


# --------------------------------------------------------------------------- #
# normalize_inputs / listify units                                            #
# --------------------------------------------------------------------------- #
def test_listify_converts_sequences_and_passes_scalars():
    assert listify(np.array([1.0, 2.0])) == [1.0, 2.0]
    assert listify((1.0, 2.0)) == [1.0, 2.0]
    assert listify(3.0) == 3.0
    assert listify(None) is None


def test_normalize_inputs_broadcasts_zm_and_prefers_z0():
    out = normalize_inputs(zm=[20.0], ustar=[0.5, 0.4], pblh=[1000.0] * 2,
                           mo_length=[-100.0] * 2, v_sigma=[0.5] * 2,
                           wind_dir=[0.0, 90.0], z0=[0.1], umean=[3.0, 3.0],
                           verbosity=0)
    assert out.ts_len == 2
    assert out.zm == [20.0, 20.0]
    assert out.z0 == [0.1, 0.1]
    assert out.umean == [None, None]  # z0 wins when both are given


def test_normalize_inputs_rejects_unequal_lengths():
    with pytest.raises(InputValidationError):
        normalize_inputs(zm=[20.0], ustar=[0.5, 0.4], pblh=[1000.0],
                         mo_length=[-100.0], v_sigma=[0.5], wind_dir=[0.0],
                         z0=[0.1], verbosity=0)


# --------------------------------------------------------------------------- #
# @footprint_model: registration and the free driver                          #
# --------------------------------------------------------------------------- #
def test_decorator_registers_a_protocol_conformant_model(toy_model):
    assert "toy_gauss" in available_models()
    assert isinstance(toy_model, FootprintModel)
    assert toy_model.resolve_grid is resolve_grid
    assert callable(toy_model.kernel)


def test_toy_model_gets_grid_provenance_and_captured_fraction(toy_model):
    fp = toy_model(**MET, **GRID, smooth_data=0, verbosity=0)
    assert isinstance(fp, Footprint)
    assert fp.f.shape == (61, 61)
    assert fp.n == 1
    assert fp.attrs["model"] == "toy_gauss"
    assert fp.attrs["model_doi"] == "10.0000/toy"
    assert "fluxprint_version" in fp.attrs
    assert "toy_gauss" in fp.attrs["history"]
    assert fp.attrs["captured_fraction"] == pytest.approx(fp.total())
    assert fp.attrs["smooth_data"] == 0


def test_toy_model_smoothing_uses_the_generic_kernel(toy_model):
    raw = toy_model(**MET, **GRID, smooth_data=0, verbosity=0)
    smoothed = toy_model(**MET, **GRID, smooth_data=1, verbosity=0)
    assert np.array_equal(smoothed.f, smooth_field(raw.f))
    assert smoothed.attrs["smooth_data"] == 1


def test_toy_model_composites_records(toy_model):
    met = {k: [v] * 3 for k, v in MET.items()}
    fp = toy_model(**met, **GRID, smooth_data=0, verbosity=0)
    assert fp.n == 3
    # mean of three identical fields is the field itself
    single = toy_model(**MET, **GRID, smooth_data=0, verbosity=0)
    assert np.allclose(fp.f, single.f)


def test_default_validation_skips_implausible_records(toy_model):
    met = {k: [v] * 3 for k, v in MET.items()}
    met["ustar"] = [0.5, 0.05, 0.5]  # ustar <= 0.1 fails the FFP checks
    fp = toy_model(**met, **GRID, smooth_data=0, verbosity=0)
    assert fp.n == 2


def test_model_options_reach_kernel_and_attrs():
    seen = {}

    @footprint_model("toy_opts", validate=lambda rec, opts, verbosity: True,
                     options=("width",), defaults={"width": 1.0})
    def kernel(ctx, rec, opts):
        seen.update(opts)
        return _gauss_field(ctx) * opts["width"], 0, 1

    try:
        model = get_model("toy_opts")
        fp = model(**MET, **GRID, smooth_data=0, verbosity=0, width=2.0)
        assert seen["width"] == 2.0
        assert fp.attrs["width"] == 2.0
        assert fp.attrs["captured_fraction"] == pytest.approx(2.0, rel=0.05)
    finally:
        MODELS.pop("toy_opts", None)


# --------------------------------------------------------------------------- #
# flag_err bookkeeping (reference-exact precedence)                           #
# --------------------------------------------------------------------------- #
def test_flag3_latches_when_other_records_are_valid(flaky_model):
    met = {k: [v] * 2 for k, v in MET.items()}
    met["mo_length"] = [-100.0, 200.0]  # second record goes flag-3 invalid
    fp = flaky_model(**met, **GRID, smooth_data=0, verbosity=0)
    assert fp.n == 1
    assert fp.attrs["flag_err"] == 3


def test_all_invalid_overwrites_flag_to_1(flaky_model):
    fp = flaky_model(**{**MET, "mo_length": 200.0}, **GRID, smooth_data=0,
                     verbosity=0)
    assert fp.n == 0
    assert fp.attrs["flag_err"] == 1  # n==0 overwrites the latched 3
    assert "captured_fraction" not in fp.attrs


# --------------------------------------------------------------------------- #
# End to end through the batch layer                                          #
# --------------------------------------------------------------------------- #
def test_toy_model_flows_through_calculate_footprint(toy_model):
    from fluxprint import calculate_footprint

    data = {k: [v] * 4 for k, v in MET.items()}
    series = calculate_footprint(data=data, model="toy_gauss", **GRID)
    assert len(series) == 1
    assert series[0].n == 4
    assert series[0].attrs["model"] == "toy_gauss"


# --------------------------------------------------------------------------- #
# smooth / smooth_data: one knob, two spellings                               #
# --------------------------------------------------------------------------- #
def test_smooth_spellings_are_equivalent_on_kljun():
    kljun = get_model("kljun2015")
    via_generic = kljun(**MET, **GRID, smooth=0, verbosity=0)
    via_ffp = kljun(**MET, **GRID, smooth_data=0, verbosity=0)
    assert np.array_equal(via_generic.f, via_ffp.f)
    assert via_generic.attrs["smooth_data"] == 0


def test_smooth_wins_over_smooth_data(toy_model):
    both = toy_model(**MET, **GRID, smooth=0, smooth_data=1, verbosity=0)
    off = toy_model(**MET, **GRID, smooth_data=0, verbosity=0)
    assert np.array_equal(both.f, off.f)


# --------------------------------------------------------------------------- #
# calc_ffp_climatology: deprecated crop/rs kwargs                             #
# --------------------------------------------------------------------------- #
def test_shim_warns_on_deprecated_crop_and_rs():
    from fluxprint.model.Kljun_et_al_2015 import calc_ffp_climatology

    met = {k: [v] for k, v in MET.items()}
    with pytest.warns(DeprecationWarning, match="crop"):
        out = calc_ffp_climatology(**met, **GRID, crop=1, verbosity=0)
    assert out.n == 1  # crop is ignored, the field is still computed
    with pytest.warns(DeprecationWarning, match="rs"):
        calc_ffp_climatology(**met, **GRID, rs=[0.8], verbosity=0)


# --- the threaded record loop --------------------------------------------
# test_reference_regression.py pins the serial path to the vendored FFP code;
# these pin every other worker count to the serial path.

_WORKER_MET = dict(
    zm=[20.0] * 6, z0=[0.1] * 6, ustar=[0.5, 0.4, 0.3, 0.45, 0.35, 0.55],
    pblh=[1000.0] * 6, mo_length=[-100.0, -50.0, 200.0, 1.0e6, -300.0, 80.0],
    v_sigma=[0.5] * 6, wind_dir=[30.0, 150.0, 270.0, 0.0, 190.0, 340.0])


@pytest.mark.parametrize("workers", [2, 3, 5, 6, 8, 16, None])
def test_workers_leave_the_climatology_bitwise_unchanged(workers):
    calc = get_model("kljun2015")
    serial = calc(**_WORKER_MET, **GRID, verbosity=0, workers=1)
    threaded = calc(**_WORKER_MET, **GRID, verbosity=0, workers=workers)
    assert np.array_equal(serial.f, threaded.f)
    assert np.array_equal(np.isnan(serial.f), np.isnan(threaded.f))
    assert serial.n == threaded.n


def test_workers_preserve_rejection_bookkeeping():
    """Records the validator rejects must still be skipped, and only those."""
    met = {k: list(v) for k, v in _WORKER_MET.items()}
    met["ustar"][1] = 0.01     # below the validator's floor
    met["v_sigma"][4] = -1.0   # non-positive
    calc = get_model("kljun2015")
    serial = calc(**met, **GRID, verbosity=0, workers=1)
    threaded = calc(**met, **GRID, verbosity=0, workers=4)
    assert serial.n == threaded.n == 4
    assert np.array_equal(serial.f, threaded.f)


def test_workers_are_capped_by_the_record_count_and_never_below_one():
    assert resolve_workers(None, ts_len=1, field_bytes=8) == 1
    assert resolve_workers(64, ts_len=3, field_bytes=8) == 3
    assert resolve_workers(0, ts_len=10, field_bytes=8) == 1
    assert resolve_workers(-4, ts_len=10, field_bytes=8) == 1


def test_auto_workers_stay_serial_on_a_small_machine(monkeypatch):
    """<= 2 cores must take the untouched serial path, pool and all."""
    monkeypatch.delenv("FLUXPRINT_WORKERS", raising=False)
    for cpu in (1, 2):
        monkeypatch.setattr("os.cpu_count", lambda c=cpu: c)
        assert resolve_workers(None, ts_len=1000, field_bytes=8) == 1


def test_auto_workers_are_trimmed_to_the_memory_budget(monkeypatch):
    """A grid too big to hold several copies auto-selects fewer threads."""
    monkeypatch.delenv("FLUXPRINT_WORKERS", raising=False)
    monkeypatch.setattr("os.cpu_count", lambda: 64)
    from fluxprint.model import engine
    huge = engine.WORKER_MEMORY_BUDGET  # one field already fills the budget
    assert resolve_workers(None, ts_len=1000, field_bytes=huge) == 1
    assert resolve_workers(None, ts_len=1000, field_bytes=1024) ==         engine.MAX_AUTO_WORKERS
    # An explicit request is trusted, not trimmed.
    assert resolve_workers(32, ts_len=1000, field_bytes=huge) == 32


def test_env_var_sets_workers_and_a_bad_value_falls_back(monkeypatch, caplog):
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    monkeypatch.setenv("FLUXPRINT_WORKERS", "12")
    assert resolve_workers(None, ts_len=1000, field_bytes=8) == 12
    # An explicit argument still wins over the environment.
    assert resolve_workers(2, ts_len=1000, field_bytes=8) == 2
    monkeypatch.setenv("FLUXPRINT_WORKERS", "not-a-number")
    with caplog.at_level("WARNING"):
        assert resolve_workers(None, ts_len=1000, field_bytes=8) == 8
    assert "FLUXPRINT_WORKERS" in caplog.text
