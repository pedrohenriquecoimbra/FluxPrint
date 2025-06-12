from . import calc_footprint_FFP, calc_footprint_FFP_climatology
from .calc_footprint_FFP_climatology import FFP_climatology

def main(*args, **kwargs):
    return type('var_', (object,), FFP_climatology(*args, **kwargs))
