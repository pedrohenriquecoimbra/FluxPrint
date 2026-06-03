"""Footprint-model convention: a name registry and the model protocol.

A *footprint model* is a callable mapping micrometeorological inputs to one 2-D
:class:`~fluxprint.footprint.Footprint` in the local, tower-centred frame.
Inputs may be scalars (a single record) or equal-length sequences (composited
into one footprint).

Register a model with :func:`register_model` and fetch it by name with
:func:`get_model`; everywhere else selection is by name (``model="kljun2015"``).
The registry is backed by :mod:`regorator`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from regorator import create_registry, register

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fluxprint.footprint import Footprint

__all__ = [
    "FootprintModel", "MODELS",
    "register_model", "get_model", "available_models",
]

#: Registry mapping model name -> :class:`FootprintModel` (a :mod:`regorator` dict).
MODELS = create_registry("FluxPrint footprint models")


@runtime_checkable
class FootprintModel(Protocol):
    """Callable contract every footprint model conforms to.

    Implementations take keyword-only micrometeorological inputs plus grid
    options and return one local-frame :class:`~fluxprint.footprint.Footprint`.
    ``tower``/``tower_crs``/``time`` are metadata attached to the result, not
    used by the footprint math.
    """

    def __call__(self, *, zm, ustar, pblh, mo_length, v_sigma, wind_dir,
                 z0=None, umean=None, domain=None, dx=None, dy=None,
                 tower=None, tower_crs=None, time=None, **kwargs) -> "Footprint":
        ...


def register_model(name: str, description: str = "", **kwargs):
    """Return a decorator registering a footprint model under ``name``.

    Args:
        name: Lookup key, e.g. ``"kljun2015"``.
        description: Human-readable description stored on the model.
        **kwargs: Extra attributes set on the model (forwarded to regorator).
    """
    return register(name, MODELS, description, **kwargs)


def get_model(name: str) -> "FootprintModel":
    """Return the model registered under ``name``.

    Raises:
        KeyError: If no model is registered under ``name``.
    """
    model = MODELS.get(name)
    if model is None:
        raise KeyError(
            f"No footprint model named {name!r}. "
            f"Available: {available_models()}.")
    return model


def available_models() -> list[str]:
    """Return the sorted names of all registered models."""
    return sorted(MODELS.keys())
