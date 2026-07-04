"""Reconciliation tests for the micrometeorology estimators.

These pin three physically-motivated corrections (ported from fluxcom's
flux_footprint module) so the fluxcom footprint provider keeps its behaviour
once it consumes fluxprint as the single source of truth:

* ``compute_mo_length`` uses the *kinematic* buoyancy flux ``H/(rho*cp)`` and
  ``TA + 273.15`` -- the plain ``H`` form was ~1200x too small;
* ``compute_std_v`` defaults to ``a = 2.0`` (was ``a = 3.5``);
* ``compute_pblh`` floors ``|f|`` so ``h = ustar / f`` stays finite at the
  equator.

Runnable under pytest or directly:
``python tests/test_micromet_reconciliation.py``.
"""
import numpy as np

from fluxprint.micrometeorology import (
    compute_mo_length,
    compute_pblh,
    compute_std_v,
)


def test_mo_length_physical_magnitude():
    # Daytime unstable: H=150 W m-2, ustar=0.3, TA=20 C, PA=100 kPa -> L ~ -16 m.
    L = float(compute_mo_length(ustar=0.3, H=150.0, TA=20.0, PA=100.0))
    assert L < 0, f"unstable (H>0) should give L<0, got {L}"
    assert 5.0 < abs(L) < 60.0, f"|L| out of physical range: {L}"
    assert abs(L - (-16.0)) < 4.0, f"L not near the expected -16 m: {L}"


def test_mo_length_not_offbyfactor():
    # H must be normalised by ~rho*cp (~1200), not used raw.
    L_fixed = abs(float(compute_mo_length(ustar=0.3, H=150.0, TA=20.0, PA=100.0)))
    L_raw = abs(-(0.3 ** 3) * 293.15 / (0.4 * 9.81 * 150.0))  # old buggy form
    assert L_fixed / L_raw > 100, (
        f"expected ~1200x larger |L| after the rho*cp fix, ratio was "
        f"{L_fixed / L_raw:.1f}")


def test_mo_length_requires_temperature_and_pressure():
    # The kinematic-flux conversion needs TA and PA; missing them must raise.
    try:
        compute_mo_length(ustar=0.3, H=150.0)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError when TA/PA are missing")


def test_std_v_default_coefficient():
    assert float(compute_std_v(0.3)) == 0.6  # a = 2.0 -> 2.0 * 0.3


def test_pblh_equator_is_finite():
    # f -> 0 at the equator; the f_min floor keeps h = ustar / f finite.
    h = float(compute_pblh(0.3, 0.0))
    assert np.isfinite(h), "equator pblh must be finite"
    assert abs(h - 0.3 / 1e-5) < 1e-6, f"expected floor at f_min, got {h}"


def test_pblh_midlatitude():
    assert abs(float(compute_pblh(0.3, 45.0)) - 2909.0664) < 0.1


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)
