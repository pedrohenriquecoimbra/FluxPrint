# built-in modules
import warnings
import copy
import re
import numbers
import sys
import os
import logging

# 3rd party modules
import numpy as np
from numpy import ma
import pandas as pd
from scipy import signal as sg
from pyproj import Transformer
import xarray as xr
import rasterio
import matplotlib.pyplot as plt

# local modules
from .commons import start_logging, update_nested_dict
from . import model
from . import utils
from . import io
from . import template
from . import exceptions
from . import micrometeorology

logger = logging.getLogger('fluxprint.core')


def process_footprint_inputs(data=None, keep_cols=[], estimate_missing_variables=True, **kwargs):
    """
    Process input values for footprint calculation.

    Parameters:
        data (pd.DataFrame, optional): A DataFrame containing the required columns.
        **kwargs: Individual keyword arguments for the required values.

    Returns:
        dict: A dictionary with the processed input values as lists.
    """
    # Define the required keys
    required_keys = ['zm', 'z0', 'umean', 'ustar',
                     'pblh', 'mo_length', 'v_sigma', 'wind_dir'] + keep_cols
    aka_keys = {'wind_dir': ['wd'],
                'v_sigma': ['sigmav'],
                'ustar': ['u*'],
                'umean': ['ws'],
                'mo_length': ['ol']}
    core_keys = ['zm', 'umean', 'wind_dir']
    optional_keys = ['z0', 'umean'] + keep_cols
    # optional_keys = [] + keep_cols

    # Drop full nan columns
    data = data.dropna(axis=1, how='all')

    # If data is provided, extract values from the DataFrame
    if data is not None and isinstance(data, pd.DataFrame):
        if not isinstance(data, pd.DataFrame):
            raise ValueError("`data` must be a pandas DataFrame.")

        # Use regex to match column names case-insensitively
        inputs = {}
        for key in required_keys:
            # Create a regex pattern to match the key case-insensitively
            pattern = re.compile(f'^{key}$', re.IGNORECASE)
            # Find matching columns in the DataFrame, prioritizing exact matches
            matching_columns = [col for col in data.columns if col == key] + [
                col for col in data.columns if pattern.match(col)]
            
            if matching_columns:
                logger.debug(f'matching_columns: {matching_columns}')
                # Use the first matching column
                inputs[key] = data[matching_columns[0]].tolist()

        # Use other names variables may be known for (e.g. wind direction, wind_dir, wd)
        for key in required_keys:
            for aka in aka_keys.get(key, []):
                # Create a regex pattern to match the key case-insensitively
                pattern = re.compile(f'^{aka}$', re.IGNORECASE)
                # Find matching columns in the DataFrame, prioritizing exact matches
                matching_columns = [col for col in data.columns if col == key] + [
                    col for col in data.columns if pattern.match(col)]

                # if aka in data.columns:
                if matching_columns:
                    logger.debug(f'aka: {matching_columns[0]}')
                    inputs[key] = data[matching_columns[0]]

        # Check if the key is provided as a keyword argument
        for key in required_keys:
            if key in kwargs:
                logger.debug(f'kwargs {key}')
                inputs[key] = kwargs[key]
            
    elif data is not None and isinstance(data, dict):
        # If DataFrame provided is a dict like kwargs
        inputs = data
        inputs.update(kwargs)
    else:
        # If no DataFrame is provided, use kwargs
        inputs = kwargs

    # Check if all core keys are present in the inputs
    missing_keys = [key for key in core_keys if key not in inputs]
    if missing_keys:
        raise ValueError(f"Missing required inputs: {missing_keys}")
        
    # Check if all required keys are present in the inputs
    missing_keys = [
        key for key in required_keys if key not in inputs and key not in optional_keys]
    logger.debug(f'missing_keys: {missing_keys}, {inputs.keys()}')
    if missing_keys:
        if estimate_missing_variables:
            for key in missing_keys:
                inputs[key] = micrometeorology.caller(inputs, key)
        else:
            raise ValueError(f"Missing required inputs: {missing_keys}")
    
    # Get the maximum length of the inputs
    max_len_inputs = max(len(v) if isinstance(
        v, (list, np.ndarray)) else 1 for v in inputs.values())

    # Ensure all values are lists
    for key in required_keys:
        if key not in inputs:
            continue
        value = inputs[key]
        if isinstance(value, pd.Series):
            inputs[key] = value.tolist()
        elif not isinstance(value, (list, np.ndarray)):
            inputs[key] = [value]*max_len_inputs

    logger.debug(f'inputs: {inputs}')
    return inputs


def wrapper(*args, out_as='nc', dst='', precision=None, meta={}, **kwargs):
    if dst:
        start_logging(os.path.dirname(dst))

    meta = update_nested_dict(meta,
                              {'__global__': {'Model_Used': kwargs.get('model', model.kljun2015).__name__},
                               'footprint': {'10^?': precision}})

    ffp = calculate_footprint(*args, **kwargs)
    ffp = utils.convert_to_nc(ffp, **meta)
    ffp = utils.center_footprint(ffp)

    ffp = ffp.assign(ffp.mean(
        'timestep').rename({'footprint': 'footprint_climatology'}))

    if precision:
        ffp['footprint'].data = (
            ffp.footprint.data*10**precision).astype(np.int16)
    
    if out_as in ['object']:
        ffp = utils.convert_to_object(ffp)#*args, **kwargs)
    elif out_as in ['netcdf', 'nc']:
        pass
        # ffp = utils.convert_to_nc(ffp)
    elif out_as in ['raster', 'tif', 'tiff']:
        ffp = utils.convert_to_tif(ffp)
    else:
        logger.info(f'Parameter `out_as` received an unknown value: {out_as}.')
    
    if dst:
        io.write_to_file(ffp, dst)
    
    return ffp


def calculate_footprint(data=None, by=None, model=model.kljun2015, query=None, **kwargs):
    """
    Calculate footprint using the Kljun et al. (2015) model.

    Parameters:
        data (pd.DataFrame, optional): A DataFrame containing the required columns.
        by (str, optional): Column name to group the data by.
        **kwargs: Individual keyword arguments for the required values.

    Returns:
        dict: Footprint data (x, y, fclim_2d, etc.).
    """
    if isinstance(data, pd.DataFrame):
        data = data.copy()
    if isinstance(data, str):
        data = io.read_from_url(data, na_values=[-9999])
    if query:
        data = data.query(query)
    
    # Process inputs
    inputs = process_footprint_inputs(data=data, keep_cols=[by] if isinstance(by, str) else [], **kwargs)

    # Group data by a column
    group_calc = [('climatology', inputs)] if by is None else pd.DataFrame(inputs).groupby(by)

    this_ffp = type('var_', (object,), {})
    ffp = type('var_', (object,), {'group': [], 'x_2d': [], 'y_2d': [], 'fclim_2d': [],
                                   'n': [], 'flag_err': []})
    for i, this_group in group_calc:
        assert this_group is not None, 'Please include data for footprint calculation.'

        try:
            logger.debug(f'Current footprint: {i}, {model}.')
            if isinstance(this_group, pd.DataFrame):
                this_group = this_group.to_dict(orient='list')
            
            input_variables = ['zm', 'z0', 'umean', 'ustar', 'pblh', 'mo_length', 'v_sigma',
                               'wind_dir', 'domain', 'dx', 'dy', 'rs', 'smooth_data', 'verbosity']
            
            this_input = {
                'domain': [-500, 500, -500, 500],
                'rs': [i/10 for i in range(1, 10)], 'dx': 10, 'dy': 10, 'verbosity': 0}
            this_input.update(
                {k: v for k, v in kwargs.items() if k in input_variables})
            this_input.update(
                {k: v for k, v in this_group.items() if k in input_variables})
            
            # Calculate footprint
            this_ffp = model.calc_ffp_climatology(
                **this_input
            )
        except Exception as e:
            logger.error(f'CriticalErr: {e}')
            # continue
            
        # ffp[i] = this_ffp
        # ffp{k: vars(ffp).get(k, []) + [v] for k, v in vars(this_ffp).items() if not (k.startswith('__') and k.endswith('__'))}
        ffp.group = ffp.group + [i]
        ffp.x_2d = ffp.x_2d + [vars(this_ffp).get('x_2d', [])]
        ffp.y_2d = ffp.y_2d + [vars(this_ffp).get('y_2d', [])]
        ffp.fclim_2d = ffp.fclim_2d + [vars(this_ffp).get('fclim_2d', [])]
        ffp.n = ffp.n + [vars(this_ffp).get('n', [])]
        ffp.flag_err = ffp.flag_err + [vars(this_ffp).get('flag_err', [])]
    
    # Memory efficient
    # ffp.x_2d = np.float32(ffp.x_2d)
    # ffp.y_2d = np.float32(ffp.y_2d)
    ffp.fclim_2d = np.float32(ffp.fclim_2d)
    ffp.n = np.int8(ffp.n)
    ffp.flag_err = np.int8(ffp.flag_err)
    return ffp


def aggregate_footprints(fclim_2d, dx, dy, smooth_data=1):
    """
    Aggregate multiple footprints into a single climatological footprint.
    
    Parameters:
        footprints (list): List of footprint dictionaries.
    
    Returns:
        np.ndarray: Aggregated footprint.
    """
    fclim_2d = np.array(fclim_2d)
    if len(fclim_2d.shape) == 2:
        logger.info(
            f"Footprint must be 3D (time, x, y), dimension passed was: {fclim_2d.shape}.")
        return fclim_2d

    assert len(
        fclim_2d.shape) == 3, f"Footprint must be 3D (time, x, y), dimension passed was: {fclim_2d.shape}."
    #n_valid = len(fclim_2d)

    fclim_clim = np.nanmean(fclim_2d, axis=0)

    if smooth_data is not None:
        skernel = np.matrix('0.05 0.1 0.05; 0.1 0.4 0.1; 0.05 0.1 0.05')
        fclim_clim = sg.convolve2d(fclim_clim, skernel, mode='same')
        fclim_clim = sg.convolve2d(fclim_clim, skernel, mode='same')
    return fclim_clim


def get_contour(footprint, dx, dy, rs, verbosity=0):
    flag_err = 0

    footprint = utils.convert_to_object(
        footprint)

    # Handle rs
    if rs is not None:

        # Check that rs is a list, otherwise make it a list
        if isinstance(rs, numbers.Number):
            if 0.9 < rs <= 1 or 90 < rs <= 100:
                rs = 0.9
            rs = [rs]
        if not isinstance(rs, list):
            exceptions.raise_ffp_exception(18, verbosity)

        # If rs is passed as percentages, normalize to fractions of one
        if np.max(rs) >= 1:
            rs = [x/100. for x in rs]

        # Eliminate any values beyond 0.9 (90%) and inform user
        if np.max(rs) > 0.9:
            exceptions.raise_ffp_exception(19, verbosity)
            rs = [item for item in rs if item <= 0.9]

        # Sort levels in ascending order
        rs = list(np.sort(rs))

    # Derive footprint ellipsoid incorporating R% of the flux, if requested,
    # starting at peak value.
    if rs is not None:
        clevs = utils.get_contour_levels(footprint.fclim_2d, dx, dy, rs)
        frs = [item[2] for item in clevs]
        xrs = []
        yrs = []
        for ix, fr in enumerate(frs):
            xr, yr = utils.get_contour_vertices(
                footprint.x_2d, footprint.y_2d, footprint.fclim_2d, fr)
            if xr is None:
                frs[ix] = None
                flag_err = 2
            xrs.append(xr)
            yrs.append(yr)

    # footprint.update({"xr": xrs, "yr": yrs, 'fr': frs, 'rs': rs})
    # return footprint
    return type('var_', (object,), {"xr": xrs, "yr": yrs, 'fr': frs, 'rs': rs, 'flag_err': flag_err})


