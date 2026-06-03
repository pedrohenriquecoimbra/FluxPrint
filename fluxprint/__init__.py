"""FluxPrint: flux footprint models for eddy covariance data analysis."""
import logging

from . import core, io, utils, commons, model
from .core import *  # noqa: F401,F403 (public API, bounded by core.__all__)
from .io import *    # noqa: F401,F403 (public API, bounded by io.__all__)
from .footprint import Footprint, FootprintSeries
from .version import __version__

# Libraries should not configure logging; attach a no-op handler so the package
# emits nothing unless the application configures the "fluxprint" logger.
logging.getLogger("fluxprint").addHandler(logging.NullHandler())

__all__ = [
    *core.__all__,
    *io.__all__,
    "Footprint",
    "FootprintSeries",
    "model",
    "__version__",
]
