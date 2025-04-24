
import warnings
import copy
import re
import numpy as np
import pandas as pd
from scipy import signal as sg
from pyproj import Transformer
import xarray as xr
import rasterio
from .model import ffp_kljun2015
from . import utils

DEFAULT_ATTRS = {
    None: {
        # f'Diurnal Footprints for {data_to_nc.sitename} at 30-min resolution',
        'Title': 'Flux Footprint',
        'Creation_Date': "",  # datetime.datetime.now().strftime('%d-%b-%Y'),
        'Contact': 'Pedro Coimbra and Benjamin Loubet at ECOSYS, INRAE, AgroParisTech, Université Paris-Saclay, Palaiseau, France, pedro-henrique.herig-coimbra@inrae.fr and benjamin.loubet@inrae.fr',
        'Aknowledgement': 'This is the continuation of the work from Betty Molinier and Natascha Kljun, Centre for Environmental and Climate Science, Lund University, betty.molinier@cec.lu.se and natascha.kljun@cec.lu.se',
        'Conventions': 'CF-1.8',
        'Creator': 'Betty Molinier¹ (ORCID: 0000-0002-7212-4120), Natascha Kljun¹ (ORCID: 0000-0001-9650-2184), Pedro Coimbra² (ORCID: 0009-0008-6845-8735) and Benjamin Loubet² (ORCID: 0000-0001-8825-8775).\n1 Centre for Environmental and Climate Science, Lund University, Sweden.\n2 ECOSYS, INRAE, AgroParisTech, Université Paris-Saclay, Palaiseau, France',
        'Institution': 'Centre for Environmental and Climate Science, Lund University, Lund, Sweden\nECOSYS, INRAE, AgroParisTech, Université Paris-Saclay, Palaiseau, France',
        'Source': "",  # 'Ecosystem Thematic Centre (2024). ETC NRT Fluxes, Romainville, 2023-02-13–2024-04-30, ICOS Cities, https://hdl.handle.net/11676/ML3hTCCg5neiu2yw_HUF7AkW; Hersbach, H., et al. (2023): ERA5 hourly data on single levels from 1940 to present. Copernicus Climate Change Service (C3S) Climate Data Store (CDS), DOI: 10.24381/cds.adbb2d47 (Accessed on 05-May-2024)',
        'Model_Used': 'FFP, Kljun et al. (2015), doi:10.5194/gmd‐8‐3695‐2015',
        # f'This file contains flux footprints for the {data_to_nc.sitename} flux tower in France at 30-minute temporal resolution. The name of the file includes the date of all footprints contained.',
        'Summary': "",
        'Subjects': "",  # 'Flux footprints, atmospheric modelling, urban flux, ICOS Cities',
        'Coordinate_Reference_System': "",  # 'WGS 84',
        # '+proj=tmerc +lat_0=48.88514 +lon_0=2.42222 +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +datum=WGS84 +units=m +no_defs',
        'crs_projection4': "",
        'crs_wkt': "",  # 'PROJCS["WGS_1984_Transverse_Mercator",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",48.885140],PARAMETER["central_meridian",2.422220],PARAMETER["scale_factor",1],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["Meter",1]]',
        'Variables': 'Time, X, Y, Boundary Layer Height Quality Flag, Footprint Climatology',
        'Tower_Location_Latitude': np.nan,
        'Tower_Location_Longitude': np.nan,
        'Tower_Location_CRS': "",
        'Tower Height (m)': np.nan,
        'Frequency': '',  # '30 min'
    },
    'timestep': {'units': 'yymmddhhMM', 'timezone': ''},
    'x': {'long_name': 'x coordinate of projection',
                       'standard_name': 'projection_x_coordinate',
                       'units': 'meters'},
    'y': {'long_name': 'y coordinate of projection',
                       'standard_name': 'projection_y_coordinate',
                       'units': 'meters'},
    'footprint': {'long_name': "footprint",
                  "units": "per square meter"},
}


def process_footprint_inputs(data=None, keep_cols=[], **kwargs):
    """
    Process input values for footprint calculation.

    Parameters:
        data (pd.DataFrame, optional): A DataFrame containing the required columns.
        **kwargs: Individual keyword arguments for the required values.

    Returns:
        dict: A dictionary with the processed input values as lists.
    """
    # Define the required keys
    required_keys = ['zm', 'z0', 'ws', 'ustar',
                     'pblh', 'mo_length', 'v_sigma', 'wd'] + keep_cols
    optional_keys = ['z0', 'ws'] + keep_cols

    # If data is provided, extract values from the DataFrame
    if data is not None and isinstance(data, pd.DataFrame):
        if not isinstance(data, pd.DataFrame):
            raise ValueError("`data` must be a pandas DataFrame.")

        # Use regex to match column names case-insensitively
        inputs = {}
        for key in required_keys:
            # Check if the key is provided as a keyword argument
            if key in kwargs:
                data[key] = kwargs[key]
            # Create a regex pattern to match the key case-insensitively
            pattern = re.compile(f'^{key}$', re.IGNORECASE)
            # Find matching columns in the DataFrame, prioritizing exact matches
            matching_columns = [col for col in data.columns if col == key] + [
                col for col in data.columns if pattern.match(col)]
            
            if matching_columns:
                # Use the first matching column
                inputs[key] = data[matching_columns[0]].tolist()
            
    elif data is not None and isinstance(data, dict):
        # If DataFrame provided is a dict like kwargs
        inputs = data
        inputs.update(kwargs)
    else:
        # If no DataFrame is provided, use kwargs
        inputs = kwargs

    # Check if all required keys are present in the inputs
    missing_keys = [key for key in required_keys if key not in inputs and key not in optional_keys]
    if missing_keys:
        raise ValueError(f"Missing required inputs: {missing_keys}")
    
    # Get the maximum length of the inputs
    max_len_inputs = max(len(v) if isinstance(
        v, (list, np.ndarray)) else 1 for v in inputs.values())

    # Ensure all values are lists
    for key in required_keys:
        value = inputs[key]
        if isinstance(value, pd.Series):
            inputs[key] = value.tolist()
        elif not isinstance(value, (list, np.ndarray)):
            inputs[key] = [value]*max_len_inputs

    return inputs


def calculate_footprint(data=None, by=None, **kwargs):
    """
    Calculate footprint using the Kljun et al. (2015) model.

    Parameters:
        data (pd.DataFrame, optional): A DataFrame containing the required columns.
        by (str, optional): Column name to group the data by.
        **kwargs: Individual keyword arguments for the required values.

    Returns:
        dict: Footprint data (x, y, fclim_2d, etc.).
    """
    # Process inputs
    inputs = process_footprint_inputs(data=data.copy(), keep_cols=[by] if isinstance(by, str) else [], **kwargs)

    # Group data by a column
    group_calc = [('climatology', inputs)] if by is None else pd.DataFrame(inputs).groupby(by)
    
    ffp = {}
    for i, this_input in group_calc:
        assert this_input is not None, 'Please include data for footprint calculation.'

        try:
            if isinstance(this_input, pd.DataFrame):
                this_input = this_input.to_dict(orient='list')
            
            # Calculate footprint
            footprint = ffp_kljun2015.FFP_climatology(
                zm=this_input['zm'],
                z0=this_input['z0'],
                umean=this_input['ws'],
                ustar=this_input['ustar'],
                h=this_input['pblh'],
                ol=this_input['mo_length'],
                sigmav=this_input['v_sigma'],
                wind_dir=this_input['wd'],
                domain=kwargs.get('domain', [-500, 500, -500, 500]),
                dx=kwargs.get('dx', kwargs.get('dy', 10)),
                dy=kwargs.get('dy', kwargs.get('dx', 10)),
                rs=kwargs.get('rs', [i/10 for i in range(1, 10)]),
                verbosity=kwargs.get('verbosity', 0)
            )
        except Exception as e:
            print(e)
            continue
        ffp[i] = footprint
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
        print(
            f"Footprint must be 3D (time, x, y), dimension passed was: {fclim_2d.shape}.")
        return fclim_2d

    assert len(
        fclim_2d.shape) == 3, f"Footprint must be 3D (time, x, y), dimension passed was: {fclim_2d.shape}."
    #n_valid = len(fclim_2d)
    fclim_clim = dx*dy*np.nanmean(fclim_2d, axis=0)

    if smooth_data is not None:
        skernel = np.matrix('0.05 0.1 0.05; 0.1 0.4 0.1; 0.05 0.1 0.05')
        fclim_2d = sg.convolve2d(fclim_2d, skernel, mode='same')
        fclim_2d = sg.convolve2d(fclim_2d, skernel, mode='same')
    return fclim_clim


def get_contour(footprint, dx, dy, rs, 
                _get_contour_levels=ffp_kljun2015.get_contour_levels,
                _get_contour_vertices=ffp_kljun2015.get_contour_vertices):
    clevs = _get_contour_levels(footprint["fclim_2d"], dx, dy, rs)
    frs = [item[2] for item in clevs]
    xrs = []
    yrs = []
    for ix, fr in enumerate(frs):
        xr, yr = _get_contour_vertices(
            footprint["x_2d"], footprint["y_2d"], footprint["fclim_2d"], fr)
        if xr is None:
            frs[ix] = None
        xrs.append(xr)
        yrs.append(yr)

    footprint.update({"xr": xrs, "yr": yrs, 'fr': frs, 'rs': rs})
    return footprint


def center_footprint(footprint, centre=None, center_previous=None):
    def __center_footprint__(footprint, centre, center_previous=(0, 0)):
        footprint = copy.deepcopy(footprint)
        # meters to coordinates

        def cy(x): return None if x is None else list(map(cy, x)) if (
            isinstance(x, list) or len(x.shape) > 1) else x + centre[0] - center_previous[0]
        def cx(x): return None if x is None else list(map(cx, x)) if (
            isinstance(x, list) or len(x.shape) > 1) else x + centre[1] - center_previous[1]

        for var in set(['xr', 'x_2d']) & set(footprint.keys()):
            footprint[var] = list(map(cx, footprint[var]))

        for var in set(['yr', 'y_2d']) & set(footprint.keys()):
            footprint[var] = list(map(cy, footprint[var]))

        for var in ['x_2d', 'y_2d']:
            footprint[var] = np.array(footprint[var])
        return footprint
    
    if isinstance(footprint, xr.Dataset):
        if not centre:
            centre = (
                float(footprint.attrs.get('Tower_Location_Latitude', 0)), 
                float(footprint.attrs.get('Tower_Location_Longitude', 0)))
            centre_crs = footprint.attrs.get('Tower_Location_CRS', "EPSG:4326")
            crs = utils.extract_crs(footprint) or "EPSG:3035"
            centre = utils.transform_coordinates(*centre, crs_in=centre_crs, crs_out=crs)
        if not center_previous:
            center_previous = (
                np.nanmean([footprint.y.max(), footprint.y.min()]),
                np.nanmean([footprint.x.max(), footprint.x.min()])
            )

        f_ = convert_to_dict(footprint)
        f_ = {k: __center_footprint__(v, centre, center_previous) for k, v in f_.items()}
        f_ = convert_to_nc(f_)

        footprint['x'] = f_.x
        footprint['y'] = f_.y

    elif isinstance(footprint, (rasterio.io.DatasetWriter, rasterio.io.DatasetReader)):
        assert centre and center_previous, 'Centre not defined.'
        footprint = utils.update_affine(
            footprint, c=centre[1] - center_previous[1], 
            f=centre[0] - center_previous[0])

    if isinstance(footprint, dict):
        footprint = __center_footprint__(
            footprint, centre, center_previous=(0, 0))
    return footprint


def convert_to_dict(data, name=None):
    # Convert data to dictionary
    footprint = {}
    if isinstance(data, xr.Dataset):
        # Convert xarray Dataset to dictionary
        if len(data.footprint.dims) == 2:
            data = {name: data}

        for i, name in enumerate(data[data.footprint.dims[0]].values):
            footprint[name] = {
                'fclim_2d': data['footprint'].values[i]
            }   
            footprint[name]["x_2d"], footprint[name]["y_2d"] = np.meshgrid(
                data['x'].values, data['y'].values)
        return footprint
    elif isinstance(data, (rasterio.io.DatasetWriter, rasterio.io.DatasetReader)):
        if name is None:
            name = data.name

        # Read the first band of the raster data
        footprint_data = data.read(1)  # Use `read(1)` to read the first band

        # Generate x and y coordinates based on the raster bounds and shape
        x = np.linspace(data.bounds.left, data.bounds.right,
                        footprint_data.shape[1])
        y = np.linspace(data.bounds.bottom, data.bounds.top,
                        footprint_data.shape[0])
        x, y = np.meshgrid(x, y)

        # Store the footprint data and coordinates in a dictionary
        footprint = {
            'fclim_2d': footprint_data,
            'x_2d': x,
            'y_2d': y
        }
        return {name: footprint}
    
        # Convert rasterio DatasetReader to dictionary
        footprint = {'fclim_2d': data.read()[0]}
        x = np.linspace(data.bounds[0], data.bounds[2],
                        footprint['fclim_2d'].shape[0])
        y = np.linspace(data.bounds[1], data.bounds[3],
                        footprint['fclim_2d'].shape[1])
        footprint["x_2d"], footprint["y_2d"] = np.meshgrid(x, y)
        return {name: footprint}
    else:
        raise ValueError(
            "Data must be a netcdf (xr array) or a rasterio Dataset.")


def convert_to_nc(data, **attrs):
    if isinstance(data, dict):
        if not utils.is_footprint_dict(data):
            #(not len(data.keys()) and 'fclim_2d' not in data) or (len(data.keys()) and 'fclim_2d' not in data[list(data.keys())[0]]):
            warnings.warn(
                "Warning: Data must be a dictionary with 'fclim_2d' key.")
        # Convert dictionary to xarray Dataset
        data = utils.dict_to_nc(data)
    elif isinstance(data, (rasterio.io.DatasetWriter, rasterio.io.DatasetReader)):
        # Convert rasterio DatasetReader to xarray Dataset
        data = convert_to_dict(data)
        # Convert dictionary to xarray Dataset
        data = utils.dict_to_nc(data)
    else:
        raise ValueError(
            "Data must be a dictionary or rasterio Dataset.")
    
    # Update attributes
    attrs = utils.update_nested_dict(copy.deepcopy(DEFAULT_ATTRS), attrs)
    data = utils.update_attrs_in_nc(data, attrs)
    return data


def convert_to_tif(data, anchor='top-left', **attrs):
    """
    Save footprint data as a TIFF file.
    
    Parameters:
        footprint (dict): Footprint data.
        output_path (str): Path to save the TIFF file.
        crs (str): Coordinate reference system.
    """
    if isinstance(data, dict):
        # Convert dictionary to xarray Dataset
        data = utils.dict_to_nc(data, attrs)
    
    if isinstance(data, xr.Dataset):
        # Convert xarray Dataset to rasterio Dataset
        arr = data['footprint'].to_numpy()
        x = data['x'].to_numpy()
        y = data['y'].to_numpy()
        dx = data['x'].attrs.get('dx', data.attrs.get('dx', 10))
        dy = data['y'].attrs.get('dy', data.attrs.get('dy', dx))
        crs = utils.extract_crs(data)

        # Footprint into array (band, lon, lat)
        if len(arr.shape) < 3:
            arr = np.array([arr])

        convention, anchor = utils.infer_convention_from_nc(data)
        transform = utils.affine_conventions(
            x, y, dx, dy, anchor=anchor, convention=convention)

        # Write a .tif profile
        profile = {'driver': 'GTiff',
                'dtype': arr.dtype,
                'nodata': None,
                'width': arr.shape[2],
                'height': arr.shape[1],
                'count': arr.shape[0],
                'crs': crs,
                'transform': transform,
                'compress': 'lzw',
                }
        profile.update({k: v for k, v in data.attrs.items() if k in profile.keys()})
        profile.update({k: v for k, v in attrs.items() if k in profile.keys()})
        
        memory_tif = rasterio.io.MemoryFile().open(**profile)
        [memory_tif.write(band, b + 1) for b, band in enumerate(arr)]
        
        return memory_tif
    else:
        raise ValueError(
            "Data must be a dictionary or rasterio Dataset.")
    return
