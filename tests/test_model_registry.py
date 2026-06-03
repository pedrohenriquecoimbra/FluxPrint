"""Tests for the model convention: registry + the Kljun adapter.

The registry tests are pure Python. The Kljun adapter tests run the real
parameterisation (numpy + scipy) and assert it yields a valid local Footprint.
"""
from __future__ import annotations

import numpy as np
import pytest

from fluxprint.footprint import Footprint
from fluxprint.model import (
    FootprintModel, available_models, base, get_model, register_model)
from fluxprint.model.Kljun_et_al_2015 import calc as kljun_calc


# --------------------------------------------------------------------------- #
# Registry                                                                    #
# --------------------------------------------------------------------------- #
def test_kljun_is_registered():
    assert "kljun2015" in available_models()
    assert get_model("kljun2015") is kljun_calc


def test_get_unknown_model_lists_available():
    with pytest.raises(KeyError, match="kljun2015"):
        get_model("nope")


def test_register_and_retrieve_custom_model():
    @register_model("dummy_test", description="toy")
    def dummy(*, zm, ustar, pblh, mo_length, v_sigma, wind_dir, z0=None,
              umean=None, domain=None, dx=None, dy=None, tower=None,
              tower_crs=None, time=None, **kw):
        return Footprint.from_grid(np.ones((3, 3)), dx=dx or 1.0, time=time)

    try:
        assert "dummy_test" in available_models()
        fp = get_model("dummy_test")(
            zm=2, ustar=0.5, pblh=1000, mo_length=-50, v_sigma=0.5,
            wind_dir=0, dx=10)
        assert isinstance(fp, Footprint) and fp.dx == 10.0
    finally:
        base.MODELS.pop("dummy_test", None)  # keep the global registry clean


def test_kljun_satisfies_protocol():
    assert isinstance(kljun_calc, FootprintModel)


# --------------------------------------------------------------------------- #
# Kljun adapter (runs the real model)                                         #
# --------------------------------------------------------------------------- #
def test_kljun_adapter_returns_local_footprint():
    fp = kljun_calc(
        zm=2.0, z0=0.01, ustar=0.5, pblh=1000.0, mo_length=-50.0,
        v_sigma=0.5, wind_dir=0.0, dx=4.0, domain=[-300, 300, -300, 300],
        tower=(0.0, 0.0), tower_crs="EPSG:3035")

    nx = int(600 / 4)
    assert fp.f.shape == (nx + 1, nx + 1)
    assert fp.is_georeferenced is False     # model emits the local frame
    assert fp.n == 1
    assert np.all(fp.f >= 0) and fp.total() > 0
    xmin, xmax, ymin, ymax = fp.extent
    px, py = fp.peak_xy()
    assert xmin <= px <= xmax and ymin <= py <= ymax
    assert fp.attrs["model"] == "kljun2015"
    assert "flag_err" in fp.attrs


def test_kljun_adapter_is_georeferenceable():
    pytest.importorskip("pyproj")
    fp = kljun_calc(
        zm=2.0, z0=0.01, ustar=0.5, pblh=1000.0, mo_length=-50.0,
        v_sigma=0.5, wind_dir=0.0, dx=8.0, domain=[-200, 200, -200, 200],
        tower=(4321000.0, 3210000.0), tower_crs="EPSG:3035")
    g = fp.georeference("EPSG:3035")
    assert g.is_georeferenced is True
    assert np.isclose(g.x.mean(), 4321000.0, atol=1.0)  # recentred on the tower


def test_kljun_adapter_composites_a_sequence():
    fp = kljun_calc(
        zm=[2.0, 2.0], z0=[0.01, 0.01], ustar=[0.5, 0.5],
        pblh=[1000.0, 1000.0], mo_length=[-50.0, -50.0],
        v_sigma=[0.5, 0.5], wind_dir=[0.0, 0.0],
        dx=8.0, domain=[-200, 200, -200, 200])
    assert fp.n == 2


def test_kljun_adapter_accepts_numpy_arrays():
    # _listify must convert arrays to lists (calc would otherwise wrap them whole)
    n = 3
    fp = kljun_calc(
        zm=np.full(n, 2.0), z0=np.full(n, 0.01), ustar=np.full(n, 0.5),
        pblh=np.full(n, 1000.0), mo_length=np.full(n, -50.0),
        v_sigma=np.full(n, 0.5), wind_dir=np.zeros(n),
        dx=8.0, domain=[-200, 200, -200, 200])
    assert fp.n == n
