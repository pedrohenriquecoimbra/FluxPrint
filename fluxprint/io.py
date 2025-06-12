import re
import glob
import rasterio
import xarray as xr
import requests
import pandas as pd
import numpy as np
import logging
import os
from io import BytesIO
from zipfile import ZipFile
from pyproj import Transformer
from shapely import geometry
import fiona
from . import utils

logger = logging.getLogger('fluxprint.io')

def is_glob_path(path):
    # Regular expression to check for glob characters
    return bool(re.search(r'[*?\[\]]', path))


def read_handler(path, *args, **kwargs):
    if isinstance(path, (list, tuple)):
        return [read_handler(p, *args, **kwargs) for p in path]
    elif isinstance(path, (str)) and is_glob_path(path):
        return [read_handler(p, *args, **kwargs) for p in glob.glob(path)]
    elif isinstance(path, (str)) and not is_glob_path(path):    
        if path.startswith('http'):
            return read_from_url(path, *args, **kwargs)
        else:
            return read_from_file(path, *args, **kwargs)
    return


def read_from_url(url=None, *args, **kwargs):
    """
    Read data from a URL.
    
    Parameters:
        url (str): URL to read data from.
        *args: Additional arguments to pass to the read
        **kwargs: Additional keyword arguments to pass to the read
            
    Returns:
        pd.DataFrame: Data read from the URL.
    """
    # Send a GET request to the URL
    response = requests.get(url)
    success = 0

    # Check if the request was successful
    if response.status_code == 200:
        in_memory = BytesIO(response.content)
        if not success:
            try:
                with ZipFile(in_memory, 'r') as zf:
                    data = pd.read_csv(
                        BytesIO(zf.read(zf.filelist[0])), *args, **kwargs)
                success = 1
            except Exception as e:
                logger.error(f"Failed to read url as csv: {e}")
            pass
        if not success:
            try:
                data = xr.open_dataset(in_memory)
                success = 1
            except Exception as e:
                logger.error(f"Failed to read url as netcdf: {e}")
                pass
    else:
        logger.error(f"Failed to download file: {response.status_code}")
    return data


def read_from_file(path, *args, memory=True, **kwargs):
    """
    Read data from a file.
    
    Parameters:
        path (str): Path to the file.
        *args: Additional arguments to pass to the read function.
        **kwargs: Additional keyword arguments to pass to the read function.
            
    Returns:
        Data read from the file.
    """
    if isinstance(path, str):
        if path.endswith('.csv'):
            return pd.read_csv(path, *args, **kwargs)
        elif path.endswith('.nc'):
            kwargs_ = {'engine': "netcdf4"}
            kwargs_.update(kwargs)
            return xr.open_dataset(path, *args, **kwargs_)
        elif path.endswith('.tif'):
            if memory:
                with rasterio.open(path) as tif:
                    memory_tif = rasterio.io.MemoryFile().open(**tif.meta)
                    memory_tif.write(tif.read())
                    memory_tif.source = path
                return memory_tif
            else:
                return rasterio.open(path, *args, **kwargs)
        else:
            raise ValueError(f"Unsupported file format: {path}")
    elif isinstance(path, (xr.core.dataset.Dataset)):
        return path
    return


def write_to_file(data, path, **kwargs):
    """
    Write data to a file.
    
    Parameters:
        path (str): Path to the file.
        data: Data to write to the file.
        **kwargs: Additional keyword arguments to pass to the write function.
        
    Returns:
        None
    """
    if isinstance(path, str):
        if path.endswith('.nc'):
            return write_to_netcdf(data, path, **kwargs)
        elif path.endswith('.shp'):
            return write_to_shapefile(data, path, **kwargs)
        elif path.endswith('.tif'):
            return write_to_raster(data, path, **kwargs)
        else:
            raise ValueError(f"Unsupported file format: {path}")
    return

def write_to_netcdf(data, path, **kwargs):
    # Convert data to NetCDF format if necessary
    if not isinstance(data, (xr.Dataset, xr.DataArray)):
        data = utils.convert_to_nc(data, **kwargs)
    # Write the data to the file
    return data.to_netcdf(path, 'w')

def write_to_shapefile(data, path, **kwargs):
    from .core import get_contour
    if not isinstance(data, dict):
        data = utils.convert_to_dict(data)
    # Write a new Shapefile
    for d, footprint in data.items():
        if 'rs' not in footprint.keys():
            footprint = get_contour(
                footprint, 10, 10, [i/10 for i in range(1, 10)])
        dst_path = path.rsplit(
            '.', 1)[0] + f'{d}.' + path.rsplit('.', 1)[-1]
        __write_to_shp__(dst_path, footprint, **kwargs)
    return

def write_to_raster(data, path, **kwargs):
    # Convert data to a raster if necessary
    if not isinstance(data, (rasterio.io.DatasetWriter, rasterio.io.DatasetReader)):
        data = utils.convert_to_tif(data, **kwargs)
    # Write the data to the file
    with rasterio.open(path, "w", **data.meta) as dest:
        dest.write(data.read())
    return


def __write_to_shp__(dst_path, footprint, schema: dict={}, **kwargs):
    # Define a polygon feature geometry with one attribute
    schema.update({
        'geometry': 'Polygon',
        'properties': {'rs': 'int', 'fr': 'float'},
    })

    with fiona.open(dst_path, 'w', 'ESRI Shapefile', schema, **kwargs) as c:
        # If there are multiple geometries, put the "for" loop here
        order = {k: i for i, k in enumerate(footprint['rs'])}
        for k in sorted(footprint['rs'], reverse=True):
            if footprint['xr'][order[k]] is not None:
                poly = geometry.Polygon(
                    list(zip(footprint['xr'][order[k]], footprint['yr'][order[k]])))
                c.write({
                    'geometry': geometry.mapping(poly),
                    'properties': {'rs': int(k*100), 'fr': footprint['fr'][order[k]]},
                })
    return
