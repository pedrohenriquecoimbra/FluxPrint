import warnings
from rasterio.transform import Affine
import numpy as np
import logging
import copy
import xarray as xr
from pyproj import Transformer  # for coordinate transformations
import rasterio

def setup_logging():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')


class structuredData:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            self.__dict__[k] = v
        pass

# utils.py
def is_footprint_dict(d):
    return ('fclim_2d' in d) or (len(d.keys()) and 'fclim_2d' in d[list(d.keys())[0]])

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

def find_utm_epsg_from_lon_deprecated(lon: float, lat: float = None):
    utm_band = str(int(np.floor((lon + 180) / 6) % 60))
    epsg_code = "EPSG:327" + utm_band.zfill(2)
    return epsg_code

def find_peak(array):
    return np.unravel_index(array.argmax(), array.shape)

def transform_crs(*xy, crs_in="EPSG:4326", crs_out="EPSG:3035"):
    transformer = Transformer.from_crs(crs_in, crs_out)
    return transformer.transform(*xy)

def find_utm_epsg_from_lon(lon: float, lat: float = None) -> str:
    """
    Determine the UTM EPSG code based on longitude.

    Parameters:
        lon (float): Longitude in decimal degrees.
        lat (float, optional): Latitude in decimal degrees. Not used in the calculation.

    Returns:
        str: UTM EPSG code (e.g., "EPSG:32612" for UTM zone 12N).
    """
    # Calculate UTM zone number
    utm_zone = int(np.floor((lon + 180) / 6) + 1)

    # Determine hemisphere (N or S)
    hemisphere="6" if lat is None or lat >= 0 else "7"

    # Construct EPSG code
    epsg_code=f"EPSG:32{hemisphere}{utm_zone:02d}"
    return epsg_code


def update_affine(src, a=0, b=0, c=0, d=0, e=0, f=0):
    # Get the affine transformation matrix
    transform = src.transform

    # Modify the affine transformation matrix to shift the raster north by some distance
    # The affine matrix is in the form:
    # | a, b, c |
    # | d, e, f |
    # Where (c, f) are the top-left corner coordinates (longitude, latitude)
    # To shift north by 1°, add 1 to the latitude (f)
    new_transform = rasterio.transform.guard_transform(
        (transform[0] + a, transform[1] + b, transform[2] + c,
         transform[3] + d, transform[4] + e, transform[5] + f))

    # Get the metadata of the input raster
    # Update the metadata with the new affine transformation matrix
    src.transform = new_transform
    return src


def update_attrs_in_nc(data, attrs={}):
    """Update attributes in a NetCDF dataset.
    
    Args:
        data (xr.Dataset): The dataset to update.
        attrs (dict): The attributes to update.
        
    Returns:
        xr.Dataset: The updated dataset.
    """
    # Update global attributes
    global_attrs = copy.deepcopy(attrs.pop(None, {}))
    for k, v in attrs.items():
        if k not in data.variables.keys():
            global_attrs.update({k: v})
    data.attrs.update(global_attrs)

    # Update variable attributes
    for k, v in attrs.items():
        if k in data.variables.keys():
            data[k].attrs.update(v)
    return data


def fp_to_nc(fclim_2d, x, y, timestep, attrs={}):
    data = xr.Dataset({'footprint': (('timestep', 'x', 'y'), np.array(fclim_2d))},
                      coords={'timestep': np.array(timestep), 'x': np.array(x), 'y': np.array(y)})
    data = update_attrs_in_nc(data, attrs)
    return data


def dict_to_nc(footprint, attrs={}):
    if footprint == {}:
        return None
    x = np.array(list(footprint[list(footprint.keys())[0]].get('x_2d')))[0, :]
    y = np.array(list(footprint[list(footprint.keys())[0]].get('y_2d')))[:, 0]
    footprints = [f['fclim_2d'] for f in footprint.values()]
    timesteps = list(footprint.keys())
    data = fp_to_nc(footprints, x, y, timesteps, attrs=attrs)
    return data

def transformer_convention(data, convention, anchor, convention_from: str = None, anchor_from: str = None):
    if isinstance(data, xr.Dataset):
        convention_infer, anchor_infer = infer_convention_from_nc(data)
    elif isinstance(data, (rasterio.io.DatasetWriter, rasterio.io.DatasetReader)):
        convention_infer, anchor_infer = infer_convention_from_affine(
            data.transform)
    anchor_from = anchor_from or anchor_infer
    convention_from = convention_from or convention_infer

    # Remove spaces
    anchor_from = anchor_from.replace(' ', '')
    convention_from = convention_from.replace(' ', '')
    anchor = anchor.replace(' ', '')
    convention = convention.replace(' ', '')

    if anchor_from != anchor_infer:
        warnings.warn(
            f'Anchor ({anchor_from}) differs from infered anchor ({anchor_infer}).')
    if convention_from != convention_infer:
        warnings.warn(
            f'Convention ({convention_from}) differs from infered convention ({convention_infer}).')

    func_ = []
    if convention != convention_from:
        def f_(arr): return arr.T
        func_.append(f_)
    
    if convention == '(lon,lat)' and anchor.split('-')[0] != anchor_from.split('-')[0]:
        def f_(arr): return arr[:, :, ::-1] if len(arr.shape) == 3 else arr[:, ::-1]
        func_.append(f_)
    
    if convention == '(lon,lat)' and anchor.split('-')[1] != anchor_from.split('-')[1]:
        def f_(arr): return arr[:, ::-1, :] if len(arr.shape) == 3 else arr[::-1, :]
        func_.append(f_)
    
    if convention == '(lat,lon)' and anchor.split('-')[0] != anchor_from.split('-')[0]:
        def f_(arr): return arr[:, ::-1, :] if len(arr.shape) == 3 else arr[::-1, :]
        func_.append(f_)
    
    if convention == '(lat,lon)' and anchor.split('-')[1] != anchor_from.split('-')[1]:
        def f_(arr): return arr[:, :, ::-1] if len(arr.shape) == 3 else arr[:, ::-1]
        func_.append(f_)


    def supra_function(array):
        for func in func_:
            array = func(array)
        return array
    return supra_function


def find_middle_point(bounds):
    # Get the bounding box
    min_x, min_y, max_x, max_y = bounds.left, bounds.bottom, bounds.right, bounds.top

    # Calculate the middle point
    middle_x = (min_x + max_x) / 2
    middle_y = (min_y + max_y) / 2

    return middle_y, middle_x
    
def affine_conventions(x, y, dx, dy, anchor='top-left', convention='(lat,lon)'):
    default = rasterio.Affine(0, dx, np.min(x),   dy, 0, np.min(y))
    transform_affine = {
        '(lat,lon)': {'default':      rasterio.Affine(dx, 0, np.min(x),   0, -dy, np.max(y)),
                  'top-left':     rasterio.Affine(dx, 0, np.min(x),   0, -dy, np.max(y)),
                  'top-right':    rasterio.Affine(-dx, 0, np.max(x),  0, -dy, np.max(y)),
                  'bottom-right': rasterio.Affine(-dx, 0, np.max(x),  0, dy, np.min(y)),
                  'bottom-left':  rasterio.Affine(dx, 0, np.min(x),   0, dy, np.min(y)), },
        '(lon,lat)': {'default':      rasterio.Affine(0, dx, np.min(x),   dy, 0, np.min(y)),
                      'top-left':     rasterio.Affine(0, dx, np.min(x),   -dy, 0, np.max(y)),
                      'top-right':    rasterio.Affine(0, -dx, np.max(x),  -dy, 0, np.max(y)),
                      'bottom-right': rasterio.Affine(0, -dx, np.max(x),  dy, 0, np.min(y)),
                      'bottom-left':  rasterio.Affine(0, dx, np.min(x),   dy, 0, np.min(y)), }, }
    
    if convention in transform_affine:
        if not transform_affine.get(convention).get(anchor, None):
            warnings.warn(
                f'Ignoring anchor {anchor} and convention ({convention}) and using default affine.')
        return transform_affine.get(convention).get(anchor, transform_affine[convention]['default'])
    else:
        if anchor or convention:
            warnings.warn(
                f'Ignoring anchor {anchor} and convention ({convention}) and using default affine.')
        return default


def identify_convention(affine_matrix, dx, dy, min_x, max_x, min_y, max_y):
    a, b, xoff, d, e, yoff = affine_matrix[:6]

    if a == dx and e == -dy and xoff == min_x and yoff == max_y:
        return "(lat,lon)", "top-left"
    elif a == -dx and e == -dy and xoff == max_x and yoff == max_y:
        return "(lat,lon)", "top-right"
    elif a == -dx and e == dy and xoff == max_x and yoff == min_y:
        return "(lat,lon)", "bottom-right"
    elif a == dx and e == dy and xoff == min_x and yoff == min_y:
        return "(lat,lon)", "bottom-left"
    
    elif b == dx and d == dy and xoff == min_x and yoff == min_y:
        return "(lon,lat)", "bottom-left"
    elif b == -dx and d == dy and xoff == min_x and yoff == min_y:
        return "(lon,lat)", "bottom-right"
    elif b == -dx and d == -dy and xoff == min_x and yoff == min_y:
        return "(lon,lat)", "top-right"
    elif b == dx and d == -dy and xoff == min_x and yoff == min_y:
        return "(lon,lat)", "top-left"
    else:
        return "Unknown convention"


def infer_convention_from_affine(affine_matrix):
    a, b, xoff, d, e, yoff = affine_matrix[:6]

    if a > 0 and e < 0:
        return "(lat,lon)", "top-left"
    elif a < 0 and e < 0:
        return "(lat,lon)", "top-right"
    elif a < 0 and e > 0:
        return "(lat,lon)", "bottom-right"
    elif a > 0 and e > 0:
        return "(lat,lon)", "bottom-left"
    elif b > 0 and d > 0:
        return "(lon,lat)", "bottom-left"
    elif b > 0 and d < 0:
        return "(lon,lat)", "bottom-right"
    elif b < 0 and d < 0:
        return "(lon,lat)", "top-right"
    elif b < 0 and d > 0:
        return "(lon,lat)", "top-left"
    else:
        return "Unknown convention"


def infer_convention_from_nc(data):
    if 'x' in list(data.footprint.dims)[-2] and 'y' in list(data.footprint.dims)[-1]:
        a = data.x[-1] - data.x[0]
        e = data.y[-1] - data.y[0]
        b = d = 0
    else:
        a = e = 0
        b = data.x[-1] - data.x[0]
        d = data.y[-1] - data.y[0]
    if a > 0 and e < 0:
        return "(lat,lon)", "top-left"
    elif a < 0 and e < 0:
        return "(lat,lon)", "top-right"
    elif a < 0 and e > 0:
        return "(lat,lon)", "bottom-right"
    elif a > 0 and e > 0:
        return "(lat,lon)", "bottom-left"
    elif b > 0 and d > 0:
        return "(lon,lat)", "bottom-left"
    elif b > 0 and d < 0:
        return "(lon,lat)", "bottom-right"
    elif b < 0 and d < 0:
        return "(lon,lat)", "top-right"
    elif b < 0 and d > 0:
        return "(lon,lat)", "top-left"
    else:
        return "Unknown convention"

def transform_coordinates(*args, crs_in="EPSG:4326", crs_out="EPSG:3035"):
    """
    Transform coordinates between CRS.
    
    Parameters:
        args (float): Coordinates.
        crs_in (str): Input CRS.
        crs_out (str): Output CRS.
    
    Returns:
        tuple: Transformed coordinates.
    """
    transformer = Transformer.from_crs(crs_in, crs_out)
    return transformer.transform(*args)


def attribute_crs(data, crs="EPSG:3035"):
    if not isinstance(data, (xr.Dataset, xr.DataArray)):
        crs = rasterio.crs.CRS.from_string(crs)
        new_attrs = {None: {'Coordinate_Reference_System': crs.to_string(),
                            'crs_projection4': crs.to_proj4(),
                            'crs_wkt': crs.to_wkt()}
                    }
        data = update_attrs_in_nc(data, new_attrs)
    return data

def extract_crs(nc, str:bool=False):
    if nc.attrs.get('crs_wkt', None):
        crs = rasterio.crs.CRS.from_wkt(nc.attrs['crs_wkt'])
        if str:
            crs = f'EPSG:{crs.to_epsg()}'
    else:
        crs = nc.attrs.get('crs', None)
    return crs


def reproject_tif(src, crs):
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    transform, width, height = calculate_default_transform(
        src.crs, crs, src.width, src.height, *src.bounds)
    kwargs = src.meta.copy()

    kwargs.update({
        'crs': crs,
        'transform': transform,
        'width': width,
        'height': height})

    dst = rasterio.io.MemoryFile().open(**kwargs)
    for i in range(1, src.count + 1):
        reproject(
            source=rasterio.band(src, i),
            destination=rasterio.band(dst, i),
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=crs,
            resampling=Resampling.nearest)
    return dst


def reproject_netcdf(data, crs_to, crs_from=None):
    crs_from = extract_crs(data, str=True) or crs_from
    coords = list(zip(*[transform_coordinates(
        *coord, crs_in=crs_from, crs_out=crs_to) for coord in list(zip(data.y.to_numpy(), data.x.to_numpy()))]))
    data['x'] = (data['x'].dims, np.array(coords[1]), data['x'].attrs)
    data['y'] = (data['y'].dims, np.array(coords[0]), data['y'].attrs)
    crs = rasterio.crs.CRS.from_string(crs_to)
    data.attrs.update({'Coordinate_Reference_System': crs.to_string(),
                       'crs_projection4': crs.to_proj4(),
                       'crs_wkt': crs.to_wkt()})
    return data


def plot_leaflet(nc, filename='sample/output/footprint_map.html'):
    import folium
    nc = reproject_netcdf(nc.copy(), 'EPSG:4326')  # 'EPSG:3857')

    # Define the center of the map
    center_lat = np.nanmean(nc.y.to_numpy())  # Latitude
    center_lon = np.nanmean(nc.x.to_numpy())  # Longitude

    # Create a folium map
    m = folium.Map(location=[center_lat, center_lon], 
                   zoom_start=13,
                   control_scale=True)

    # Get the footprint data
    footprint_data = np.nanmean(nc.footprint.to_numpy(), axis=0)
    footprint_data = transformer_convention(
        nc, '(lat,lon)', 'bottom-left')(footprint_data)

    # Normalize the footprint data for visualization
    footprint_data_normalized = (footprint_data - np.nanmin(footprint_data)) / \
        (np.nanmax(footprint_data) - np.nanmin(footprint_data))

    # Define the bounds of the footprint data
    bounds = [[np.nanmin(nc.y.to_numpy()), np.nanmin(nc.x.to_numpy())],
              [np.nanmax(nc.y.to_numpy()), np.nanmax(nc.x.to_numpy())]]

    # Add the footprint data as an image overlay
    folium.raster_layers.ImageOverlay(
        image=footprint_data_normalized,
        bounds=bounds,
        opacity=0.6,
        colormap=lambda x: (1, 0, 0, x)  # Red colormap
    ).add_to(m)

    # Save the map as an HTML file
    #m.save(filename)

    # Display the map
    return m
