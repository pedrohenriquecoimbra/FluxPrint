#!/usr/bin/env python
from setuptools import find_packages, setup

from fluxprint import version

setup(
    name='FluxPrint',
    version=version.__version__,
    url='https://github.com/pedrohenriquecoimbra/footprint_toolkit',
    description=(
        "Python module to easily handle footprint outputs"
        "Written by pedrohenriquecoimbra"),
    long_description=open('README.md', encoding='utf-8').read(),
    long_description_content_type="text/markdown",
    author='Pedro Henrique Coimbra',
    author_email='pedro-henrique.herig-coimbra@inrae.fr',
    keywords="footprint, flux, eddy covariance, EC",
    license='EUPL-1.2',    platforms=['any'],
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'numpy',
        'pandas',
        'fiona',
        'shapely',
        'pyproj',
        'xarray',
        'rasterio',
        'requests',
    ],
    extras_require={
        'icoscp': ['icoscp', 'icoscp_core'],
    },
    # See http://pypi.python.org/pypi?%3Aaction=list_classifiers
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Other Environment',
        'Framework :: Jupyter',
        'Intended Audience :: Science/Research',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8', 
        'Programming Language :: Python :: 3.9', 
        'Programming Language :: Python :: 3.10', 
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Programming Language :: Python :: 3.14',
        'Topic :: Other/Nonlisted Topic'],
    python_requires='>=3.8',
)