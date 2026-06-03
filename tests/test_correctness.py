"""Regression tests for the correctness fixes.

The ``exceptions`` and ``micrometeorology`` tests only need numpy. The ``core``
and ``io`` tests import modules that pull in the full geo stack (rasterio,
fiona, pyproj, xarray); they run wherever those are installed.
"""
from __future__ import annotations

import logging
import zipfile
from io import BytesIO

import numpy as np
import pytest

from fluxprint import exceptions, micrometeorology


# --------------------------------------------------------------------------- #
# exceptions.check_ffp_inputs                                                  #
# --------------------------------------------------------------------------- #
def _valid_kwargs(**overrides):
    base = dict(
        ustar=0.3, sigmav=0.5, h=1000.0, ol=-100.0, wind_dir=180.0,
        zm=10.0, z0=0.1, umean=None, rslayer=1, verbosity=0,
    )
    base.update(overrides)
    return base


def test_check_ffp_inputs_zero_ol_does_not_raise():
    """ol == 0 previously raised ZeroDivisionError; now it rejects the record."""
    assert exceptions.check_ffp_inputs(**_valid_kwargs(ol=0)) is False


def test_check_ffp_inputs_rslayer_one_continues():
    """Inside the roughness sublayer with rslayer == 1 -> alert, keep going."""
    # zm <= 12.5 * z0 puts us in the sublayer; rslayer == 1 should not reject.
    assert exceptions.check_ffp_inputs(**_valid_kwargs(zm=1.0)) is True


def test_check_ffp_inputs_rslayer_not_one_rejects():
    """Same sublayer condition but rslayer != 1 -> error, reject the record."""
    assert exceptions.check_ffp_inputs(**_valid_kwargs(zm=1.0, rslayer=0)) is False


def test_check_ffp_inputs_too_unstable_rejects():
    """zm/ol <= -15.5 is too unstable and must be rejected."""
    # zm=10, ol=-0.5 -> zm/ol = -20 <= -15.5
    assert exceptions.check_ffp_inputs(**_valid_kwargs(ol=-0.5)) is False


# --------------------------------------------------------------------------- #
# micrometeorology.caller                                                      #
# --------------------------------------------------------------------------- #
def test_caller_pblh_is_lazy_and_does_not_require_z0_inputs():
    """Requesting pblh used to KeyError because z0 was computed eagerly."""
    assert micrometeorology.caller({}, "pblh") == 1000.0


def test_caller_v_sigma_only_needs_ustar():
    out = micrometeorology.caller({"ustar": [0.2, 0.4]}, "v_sigma")
    assert np.allclose(out, [0.7, 1.4])  # sigma_v = 3.5 * ustar


def test_caller_unknown_variable_returns_none():
    assert micrometeorology.caller({}, "no_such_variable") is None


def test_crude_constant_emits_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="fluxprint.micrometeorology"):
        value = micrometeorology.filler({}, "zm", fill_all=True)
    assert value == 30.0
    assert any("crude" in r.message for r in caplog.records)


def test_fill_all_toggles_crude_estimators():
    # zm is a crude constant: available only when fill_all=True.
    assert micrometeorology.filler({}, "zm", fill_all=True) == 30.0
    assert micrometeorology.filler({}, "zm", fill_all=False) is None
    # v_sigma is an essential estimate: available in both tiers.
    assert micrometeorology.filler({"ustar": 0.3}, "v_sigma", fill_all=False) is not None


def test_filler_returns_none_when_inputs_unavailable():
    # mo_length needs ustar + H (+ theta or TA/PA); with nothing it can't compute.
    assert micrometeorology.filler({}, "mo_length", fill_all=True) is None


# --------------------------------------------------------------------------- #
# core.calculate_footprint  (full geo stack)                                   #
# --------------------------------------------------------------------------- #
def test_calculate_footprint_skips_failed_group():
    """A failed group is skipped, never backfilled with another group's footprint."""
    pytest.importorskip("rasterio")
    import pandas as pd
    from fluxprint import core
    from fluxprint.footprint import Footprint, FootprintSeries

    calls = {"n": 0}

    def fake(*, dx, time=None, **kw):
        calls["n"] += 1
        if calls["n"] == 2:          # groups iterate sorted: A ok, B fails
            raise RuntimeError("boom")
        return Footprint.from_grid(np.zeros((3, 3)), dx=dx, time=time, n=1)

    n = 2
    data = pd.DataFrame({
        "grp": ["A", "B"],
        "zm": [10.0] * n, "umean": [3.0] * n, "ustar": [0.3] * n,
        "pblh": [1000.0] * n, "mo_length": [-100.0] * n,
        "v_sigma": [0.5] * n, "wind_dir": [180.0] * n,
    })

    series = core.calculate_footprint(data, by="grp", model=fake)

    assert isinstance(series, FootprintSeries)
    assert series.nt == 1                       # B dropped, not duplicated
    assert series[0].attrs["group"] == "A"


def test_calculate_footprint_raises_when_all_groups_fail():
    pytest.importorskip("rasterio")
    import pandas as pd
    from fluxprint import core

    def failing(**kw):
        raise RuntimeError("boom")

    data = pd.DataFrame({
        "zm": [10.0], "umean": [3.0], "ustar": [0.3], "pblh": [1000.0],
        "mo_length": [-100.0], "v_sigma": [0.5], "wind_dir": [180.0],
    })

    with pytest.raises(ValueError, match="No footprints"):
        core.calculate_footprint(data, model=failing)


# --------------------------------------------------------------------------- #
# io.read_from_url  (full geo stack)                                           #
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


def test_read_from_url_raises_on_http_error(monkeypatch):
    pytest.importorskip("rasterio")
    from fluxprint import io

    monkeypatch.setattr(io.requests, "get",
                        lambda *a, **k: _FakeResponse(b"", status_code=404))
    with pytest.raises(OSError, match="404"):
        io.read_from_url("http://example.invalid/data.zip")


def test_read_from_url_raises_on_unparseable_payload(monkeypatch):
    pytest.importorskip("rasterio")
    from fluxprint import io

    monkeypatch.setattr(io.requests, "get",
                        lambda *a, **k: _FakeResponse(b"not a zip or netcdf"))
    with pytest.raises(ValueError, match="zip"):
        io.read_from_url("http://example.invalid/data.bin")


def test_read_from_url_reads_zipped_csv(monkeypatch):
    pytest.importorskip("rasterio")
    from fluxprint import io

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.csv", "a,b\n1,2\n3,4\n")
    monkeypatch.setattr(io.requests, "get",
                        lambda *a, **k: _FakeResponse(buf.getvalue()))

    df = io.read_from_url("http://example.invalid/data.zip")
    assert list(df.columns) == ["a", "b"]
    assert df["b"].tolist() == [2, 4]
