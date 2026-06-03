"""Estimators for missing micrometeorological inputs.

Approximations come in two tiers:

* **essential** - physically grounded estimates derived from other inputs
  (``z0``, ``mo_length``, ``pblh``, ``v_sigma``);
* **filler** - crude constant fallbacks (``zm``, ``umean``, ``wind_dir``) and a
  rough ``ustar``, applied only when ``fill_all`` is enabled.

:func:`filler` returns an estimated value for a variable, or ``None`` when no
estimator applies, an estimator's inputs are unavailable, or its tier is
disabled. This lets callers allow or disable approximation when inputs are
missing.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import numpy as np
from regorator import create_registry, register

logger = logging.getLogger("fluxprint.micrometeorology")

__all__ = ["filler", "caller", "ESTIMATORS"]

#: Registry of physically grounded estimators (name -> callable).
ESTIMATORS = create_registry("fluxprint micrometeorological estimators")


@register("z0", ESTIMATORS, "Roughness length from umean, ustar, zm, mo_length")
def compute_z0(umean, ustar, zm, psi_f=None, ol=None, k=0.4):
    """From Kljun.py (not yet validated)."""
    if psi_f is None:
        psi_f = compute_psi_f(zm, ol)
    exponent = (np.asarray(umean) / np.asarray(ustar)) * k + psi_f
    return np.asarray(zm) / np.exp(exponent)


@register("psi_f", ESTIMATORS, "Stability correction function for momentum")
def compute_psi_f(zm, ol):
    """From Kljun.py."""
    oln = 5000  # L limit for neutral scaling
    zm, ol = np.asarray(zm), np.asarray(ol)
    xx = (1 - 19.0 * zm / ol) ** 0.25
    psi_f = np.zeros_like(xx) * np.nan
    psi_f = np.where(
        (ol <= 0) | (ol >= oln),
        np.log((1 + xx**2) / 2.) + 2. * np.log((1 + xx) / 2.)
        - 2. * np.arctan(xx) + np.pi / 2, psi_f)
    psi_f = np.where((ol > 0) & (ol < oln), -5.3 * zm / ol, psi_f)
    return psi_f


@register("pblh", ESTIMATORS, "Boundary-layer height from ustar and latitude")
def compute_pblh(ustar, latitude_deg):
    """Not yet validated."""
    omega = 7.2921e-5  # Earth's angular velocity [rad s-1]
    f = 2 * omega * np.sin(np.radians(latitude_deg))
    return np.asarray(ustar) / f


def compute_virtual_potential_temperature(Ta, P, r=None, P0=100, R_cp=0.286, r_L=0):
    """Not yet validated."""
    theta = np.asarray(Ta) * (P0 / np.asarray(P)) ** R_cp
    if r is not None:
        return theta * (1 + 0.61 * r - r_L)
    return theta


@register("mo_length", ESTIMATORS, "Obukhov length from ustar, theta and heat flux")
def compute_mo_length(ustar, H, theta=None, TA=None, PA=None, k=0.4, g=9.81):
    """Not yet validated."""
    if theta is None:
        # TA in degC -> K (273.15).
        theta = compute_virtual_potential_temperature(np.asarray(TA) + 273.15, PA)
    return -(np.asarray(ustar) ** 3) * theta / (k * g * np.asarray(H))


@register("v_sigma", ESTIMATORS, "Std. dev. of lateral velocity from ustar")
def compute_std_v(ustar, a=3.5, b=0):
    """Not yet validated."""
    return a * np.asarray(ustar) + b


@register("ustar", ESTIMATORS, "Friction velocity from umean, zm and z0")
def compute_ustar(umean, zm, z0=0.1, k=0.4):
    """Not yet validated."""
    return (np.asarray(umean) * k) / np.log(np.asarray(zm) / z0)


def _essential() -> dict[str, tuple]:
    # variable -> (constant value | callable(data), required input keys)
    return {
        "z0": (lambda d: compute_z0(d["umean"], d["ustar"], d["zm"],
                                    ol=d.get("mo_length", 1)),
               ("umean", "ustar", "zm")),
        "mo_length": (lambda d: compute_mo_length(
            d["ustar"], d["H"], theta=d.get("theta"), TA=d.get("TA"),
            PA=d.get("PA")) if ("theta" in d or ("TA" in d and "PA" in d)) else None,
            ("ustar", "H")),
        "pblh": (1000.0, ()),
        "v_sigma": (lambda d: compute_std_v(d["ustar"]), ("ustar",)),
    }


def _filler() -> dict[str, tuple]:
    return {
        "zm": (30.0, ()),
        "umean": (1.0, ()),
        "ustar": (lambda d: compute_ustar(d["umean"], d["zm"], z0=d.get("z0", 0.1)),
                  ("umean", "zm")),
        "wind_dir": (0.0, ()),
    }


def filler(data: Mapping[str, Any], variable: str, fill_all: bool = True):
    """Estimate a missing variable, or return ``None`` if it can't be filled.

    Args:
        data: Mapping of inputs already available.
        variable: Name of the variable to estimate.
        fill_all: Also allow crude constant fallbacks (``zm``/``umean``/
            ``wind_dir``) and the rough ``ustar`` estimate. With ``False`` only
            physically grounded ("essential") estimates are used.

    Returns:
        The estimated value, or ``None`` when unavailable/disabled.
    """
    table = {**_filler(), **_essential()} if fill_all else _essential()
    entry = table.get(variable)
    if entry is None:
        return None

    spec, needs = entry
    if not all(key in data for key in needs):
        logger.debug("Cannot estimate %r: missing inputs %s.",
                     variable, [k for k in needs if k not in data])
        return None

    if callable(spec):
        return spec(data)
    logger.warning("Using crude fallback for missing %r: %s", variable, spec)
    return spec


def caller(data: Mapping[str, Any], variable: str):
    """Backwards-compatible alias for :func:`filler` (``fill_all=True``)."""
    return filler(data, variable, fill_all=True)
