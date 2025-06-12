"""
This script is a key part of the following publications:
    - Herig Coimbra, Pedro Henrique and Loubet, Benjamin and Laurent, Olivier and Mauder, Matthias and Heinesch, Bernard and 
    Bitton, Jonathan and Delpierre, Nicolas and Depuydt, Jérémie and Buysse, Pauline, Improvement of Co2 Flux Quality Through 
    Wavelet-Based Eddy Covariance: A New Method for Partitioning Respiration and Photosynthesis. 
    Available at SSRN: https://ssrn.com/abstract=4642939 or http://dx.doi.org/10.2139/ssrn.4642939
"""
# standard modules
import numpy as np
import copy
import os
import re
import warnings
import logging
import datetime

# 3rd party modules

# project modules


def start_logging(outputpath, **kwargs):
    """
    Start logging to a file in the specified output path.
    """
    logname = str(os.path.join(
        outputpath, f"log/current_{datetime.datetime.now().strftime('%y%m%dT%H%M%S')}.log"))
    mkdirs(logname)

    params = dict(filemode='a',
                   format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                   datefmt='%Y-%m-%d %H:%M:%S',
                   level=logging.DEBUG,
                   force=True)
    params.update(kwargs)

    # with open(logname, "w+"): pass
    logging.basicConfig(filename=logname, **params)

    logging.captureWarnings(True)
    logging.info("STARTING THE RUN")


def mkdirs(filename):
    os.makedirs(os.path.dirname(filename), exist_ok=True)


def update_nested_dict(d, u):
    """
    Recursively updates a nested dictionary `d` with values from another dictionary `u`.
    If a key in `u` maps to a dictionary and the corresponding key in `d` also maps to a dictionary,
    the function updates the nested dictionary in `d`. Otherwise, it overwrites the value in `d`.

    Args:
        d (dict): The dictionary to update.
        u (dict): The dictionary containing updates.

    Returns:
        dict: The updated dictionary.
    """
    # Iterate over each key-value pair in the update dictionary `u`
    for k, v in u.items():
        # Check if the current value is a dictionary
        if isinstance(v, dict):
            # If the corresponding value in `d` is also a dictionary, recursively update it
            # Use `d.get(k, {})` to handle cases where the key `k` is not already in `d`
            d[k] = update_nested_dict(d.get(k, {}), v)
        else:
            # If the value is not a dictionary, directly update/overwrite the key in `d`
            d[k] = v
    # Return the updated dictionary
    return d


# Allowed data types
ALLOWED_DTYPES = {
    None, 'uint8', 'uint16', 'int16', 'uint32', 'int32',
    'float32', 'float64', 'complex64', 'complex128',
    'complex', 'int64', 'uint64', 'int8', 'complex_int16'
}

# Mapping from unsupported → closest supported
UNSUPPORTED_MAP = {
    np.float16: np.float32,
    np.intp: np.int64,              # platform-dependent
    np.uintp: np.uint64,            # platform-dependent
}


def ensure_supported_dtype(arr):
    if not isinstance(arr, np.ndarray):
        arr = np.array(arr)

    dtype_str = str(arr.dtype)

    if dtype_str in ALLOWED_DTYPES:
        return arr

    # Check for direct mapping from unsupported → supported
    if arr.dtype.type in UNSUPPORTED_MAP:
        return arr.astype(UNSUPPORTED_MAP[arr.dtype.type])

    # Default fallback (e.g., float16 or unknown types)
    if np.issubdtype(arr.dtype, np.floating):
        return arr.astype(np.float32)
    elif np.issubdtype(arr.dtype, np.complexfloating):
        return arr.astype(np.complex64)
    elif np.issubdtype(arr.dtype, np.integer):
        return arr.astype(np.int32)

    raise TypeError(f"Unsupported data type: {arr.dtype}")
