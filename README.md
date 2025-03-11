# Footprint Tools

A Python library for calculating and managing flux footprints using the Kljun et al. (2015) model. The library supports reading and writing footprint data in various formats, including pandas DataFrames, dictionaries, TIFF, and NetCDF.

---

## Features

- **Footprint Calculation**: Calculate flux footprints using the Kljun et al. (2015) model.
- **Data Formats**: Read and write footprint data in multiple formats:
  - Pandas DataFrame
  - Python dictionaries
  - TIFF files
  - NetCDF files
- **Coordinate Transformations**: Transform coordinates between different CRS (e.g., WGS84 to UTM).
- **Aggregation**: Aggregate multiple footprints into a climatological footprint.
- **Flexible Inputs**: Accepts meteorological data in various formats for footprint calculation.

---

## Installation

You can install the library using `pip`:

```bash
pip install footprint_toolkit
```

---

## Usage

### 1. Calculate a Footprint

```python
from footprint_toolkit.core import calculate_footprint

# Input data
data = {
    'zm': 10,            # Measurement height (m)
    'z0': 0.1,           # Roughness length (m)
    'ws': [3.0],         # Wind speed (m/s)
    'ustar': [0.3],      # Friction velocity (m/s)
    'pblh': [1000],      # Planetary boundary layer height (m)
    'mo_length': [-100], # Monin-Obukhov length (m)
    'v_sigma': [0.5],    # Standard deviation of lateral velocity (m/s)
    'wd': [180]          # Wind direction (degrees)
}

# Calculate footprint
footprint = calculate_footprint(data, domain=[-100, 100, -100, 100], dx=10, dy=10)
```

### 2. Save Footprint to NetCDF

```python
from footprint_toolkit.io import write_to_netcdf

# Save footprint to NetCDF
write_to_netcdf(footprint, 'output.nc')
```

### 3. Save Footprint to TIFF

```python
from footprint_toolkit.io import write_to_tif

# Save footprint to TIFF
write_to_tif(footprint, 'output.tif', crs="EPSG:4326")
```

### 4. Aggregate Multiple Footprints

```python
from footprint_toolkit.core import aggregate_footprints

# List of footprints
footprints = [footprint1, footprint2, footprint3]

# Aggregate footprints
climatological_footprint = aggregate_footprints(footprints)
```

### 5. Transform Coordinates

```python
from footprint_toolkit.utils import transform_coordinates

# Transform coordinates from WGS84 to UTM
x, y = transform_coordinates(48.84422, 1.95191, crs_in="EPSG:4326", crs_out="EPSG:3035")
```

---

## API Reference

### Core Functions (`core.py`)
- `calculate_footprint(data, domain, dx, dy)`: Calculate a flux footprint.
- `aggregate_footprints(footprints)`: Aggregate multiple footprints.

### I/O Functions (`io.py`)
- `write_to_netcdf(footprint, output_path)`: Save footprint data as a NetCDF file.
- `write_to_tif(footprint, output_path, crs)`: Save footprint data as a TIFF file.
- `read_from_dataframe(df, zm, z0, ws_col, ustar_col, pblh_col, mo_length_col, v_sigma_col, wd_col)`: Prepare input data from a pandas DataFrame.

### Utility Functions (`utils.py`)
- `transform_coordinates(x, y, crs_in, crs_out)`: Transform coordinates between CRS.
- `validate_input_data(data)`: Validate input data for footprint calculation.

---

## Examples

Check out the `examples/` directory for detailed usage examples:
- `example_dataframe.py`: Calculate footprints from a pandas DataFrame.
- `example_netcdf.py`: Save footprints as NetCDF files.
- `example_tif.py`: Save footprints as TIFF files.

---

## Contributing

Contributions are welcome! Please follow these steps:
1. Fork the repository.
2. Create a new branch for your feature or bugfix.
3. Submit a pull request.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## Acknowledgments

- Kljun et al. (2015) for the footprint model.
- Contributors and maintainers of the `footprint_kljun2015` library.

---

## Contact

For questions or feedback, please contact:
- [Pedro Henrique Coimbra](mailto:pedro-henrique.herig-coimbra@inrae.fr)
- [GitHub Issues](https://github.com/pedrohenriquecoimbra/footprint_toolkit/issues)

---

This `README.md` provides a clear and concise overview of your library, making it easy for users to understand and use your tool. Let me know if you need further adjustments!