"""Solid Waste Assessment Tool

This script will compute two statistics: the amount of solid waste generated per week per municipal jurisdiction (in metric tonnes), and the amount of uncollected solid waste generated by 'service area', i.e. the area of a jurisdiction for which a service provider (e.g. contractor or municipal department) provides solid waste collection services.

Required source files

The script requires three source files:
    (1) a vector source indicating the administrative boundaries for both a first sub-national government tier (e.g. the state level) and a second tier (e.g. the municipal level):
        - this sample script uses the administrative boundaries for Nigeria available here: https://data.humdata.org/dataset/nga-administrative-boundaries#;;
    (2) a vector source representing service areas including an attribute field or column indicating the total amount of solid waste collected per week by each contractor/service provider in metric tonnes ('total_coll'):
        - the template below uses dummy polygons and collection totals assuming a 21% collection rate. A second shapefile containing dummy data for Ogun State is available in the `data_files` folder as well. Switch between Lagos and Ogun in the drop-down menu to demonstrate how the script can be quickly run on any jurisdiction;        A
    (3) the pop GeoTIFF of a CIESIN High-Resolution Settlements Layer providing the number of persons estimated to live in each 1 arc-second pixel (2015 data) or a GPW layer at 30 arc-seconds resolution (2000-2020 data) 
        - the template below uses the HRSL for Nigeria available at https://ciesin.columbia.edu/data/hrsl/#data ;

Outputs

The script will return three outputs:
    (1) a list of municipalities by amount of solid waste generated per week in metric tonnes, in descending order;
    (2) a list of service providers by amount of uncollected solid waste per week in metric tonnes, in descending order;
    (3) choropleth maps visualizing each list using a scalar colormap (blues for (1) and reds for (2)).

A drop-down menu allows for selecting superordinate jurisdictions from a list. The assumption on the amount of solid waste produced per capita per day can be adjusted by way of an interactive slider widget. Results are then updated in real-time.
"""

import numpy as np
import pandas as pd
import geopandas as gpd
import cartopy.crs as ccrs
from cartopy.feature import ShapelyFeature
import rasterio as rio
from rasterstats import zonal_stats
import matplotlib.pyplot as plt
from ipywidgets import interact
import time
import functools

def timer(func):
    """Print runtime of decorated function"""
    @functools.wraps(func)
    def wrapper_timer(*args, **kwargs):
        start_time = time.perf_counter() #1
        value = func(*args, **kwargs)
        end_time = time.perf_counter() #2
        run_time = end_time - start_time #3
        print(f"\nFinished {func.__name__!r} in {run_time:.4f} secs")
        return value
    return wrapper_timer

def getVector(fp_adm, fp_service_areas, state_name_field, state_select='Lagos'):
    """Returns a subset of vector features and the bounding box (study area).

    Parameters
    ----------

    fp_adm : str
        File path for the administrative boundaries vector source.
    fp_service_areas : str
        File path for the service areas vector source.
    state_name_field : str
        The name of the column containing names of superordinate jurisdictions.
    state_select : str
        The name of the superordinate jurisdiction (state) for which to conduct the analysis.

    Returns
    ----------
    municipal_filter : GeoDataFrame
        GDF of the jurisdictions.
    bbox : ndarray
        Bounding box of the study area.
    service_areas : GeoDataFrame
        GDF of the service areas
    """

    # LOAD ADMINISTRATIVE BOUNDARIES VECTOR DATA

    # load Nigeria Local Government Area boundaries (Level 2, 'ADM2_EN'), and select only those LGAS within the larger Lagos State administrative boundary (Level 1, 'ADM1_EN')

    municipal_all = gpd.read_file(fp_adm)
    
    municipal_filter = municipal_all[municipal_all[state_name_field] == state_select]
    
    # Somehow feed to interact() for drop-down: state_list = sorted(municipal_all[state_name_field].unique().tolist())

    # DEFINE STUDY AREA BASED ON VECTOR SELECTION

    bbox = municipal_filter.total_bounds

    # LOAD VECTOR DATA FOR SERVICE AREAS OF SOLID WASTE SERVICE PROVIDERS

    # service_areas = gpd.read_file(fp_service_areas).to_crs(crs)
    service_areas = gpd.read_file(fp_service_areas)
    
    return municipal_filter, bbox, service_areas

def computeArray(fp_raster, bbox, sw_ppd):
    """Returns the nd array, affine and nodata variables required for zonal_stats.

    Parameters
    ----------
    fp_raster : str
        File path to the HRSL or GWP raster source.
    bbox : nd array
        Bounding box of the study area.
    sw_ppw : float
        The amount of solid waste generated per capita per day.
    
    Returns
    ----------
    array : nd array
        Array representing the amount of solid waste produce per pixel per week in metric tonnes.
    affine : Affine
        Affine transformation for the study area.
    nodata : type depends on raster source
        The NoData value of the raster source.
    """ 
    # 1. LOAD HIGH RESOLUTION SETTLEMENTS LAYER

    # Continuous floating point raster layer by CIESIN representing number of persons per 30x30m grid cell
    # Sample: Nigeria

    with rio.open(fp_raster) as dataset:
    
        # read CRS and no data attributes, create Window object
        crs = dataset.crs
        nodata = dataset.nodata
        window = dataset.window(*bbox)

        # CREATE NUMPY ND ARRAYS

        # load a subset of the HRSL corresponding to the study area
        pop_array = dataset.read(1, window=window)
        affine = dataset.window_transform(window)
        pop_array[(pop_array < 0)] = np.nan # sets negative NoData values to NaN to enable array algebra

        # Calculate tons of solid waste produced per grid cell rson per week

        sw_ppd_array = pop_array * sw_ppd # converts population to solid waste per person and day

        sw_ppw_array = sw_ppd_array * 7 # converts daily to weekly figures

        array = sw_ppw_array / 1000 # converts kilograms to tons per week (TPW)
        
        return array, affine, nodata

def zonalStats(municipal_filter, service_areas, array, affine, nodata, stat_select, mun_name_field, provider_name_field):
        """Returns one list each of feature names and zonal statistics.
        
        Parameters
        ----------
        municipal_filter : GeoDataFrame
            GDF of the jurisdictions.
        service_areas : GeoDataFrame
            GDF of the service areas
        array : nd array
            Array representing the amount of solid waste produce per pixel per week in metric tonnes.
        affine : Affine
            Affine transformation for the study area.
        nodata : type depends on raster source
            The NoData value of the raster source.
        stat_select : str
            The statistic to be computed (default is 'sum')
        mun_name_field : str
            The name of the column containing the names of subordinate jurisdictions.
        provider_name_field : str
            The name of the column containing the names of service providers.
        
        Returns
        ----------
        mun_names : list
            List of names of subordinate jurisdictions.
        mun_stats : list
            List of zonal statistics for subordinate jurisdictions.
        provider_names : list
            List of names of service providers
        provider_stats : list
            List of zonal statistics for service providers.
        """

        def getNamesStats(vector, array, name_field, feature_list, stat_list, stat_select='sum'):
            """Returns one list each of feature names and zonal statistics.

            Parameters
            ----------
            vector : path to a vector source or geo-like python object
                Python object can be a GeoPandas GeoDataFrame.
            raster : ndarray
                rasterstats alternative arg, 'path to a GDAL raster', not accepted.   
            name_field : str
                The vector source's column name containing feature names.
            feature_list : str
                Temp variable name for feature list. Must be unique in script as run consecutively.
            stat_list : str
                Temp variable name for statistics list. Must be unique in script.
            stat_select : str, optional
                String value for any statistic supported by zonal_stats (the default is 'sum').

            Returns
            ----------
            feature_list : list
                A list in alphabetical order containing the names of polygon features for which zonal stats are computed.
            stat_list : list
                A list containing the zonal statistics in the same order as "feature_list".
            """

            temp = zonal_stats(vector, array, affine=affine, nodata=nodata, stats=stat_select, geojson_out=True)

            for feature_dict in temp:
                feature_name = feature_dict['properties'][name_field]
                feature_stat = feature_dict['properties'][stat_select]
                feature_list.append(feature_name)
                stat_list.append(feature_stat)

        # CALCULATE ZONAL STATS - BASELINE GENERATION PER MUNICIPALITY

        # empty lists of all polygon names and zonal stats to be populated by function

        mun_names = []
        mun_stats = []
        getNamesStats(municipal_filter, array, mun_name_field, mun_names, mun_stats)

        # CALCULATE ZONAL STATS - SOLID WASTE COLLECTED PER SERVICE AREA

        provider_names = []
        provider_stats = []
        getNamesStats(service_areas, array, provider_name_field, provider_names, provider_stats)

        return mun_names, mun_stats, provider_names, provider_stats

def processStats(service_areas, mun_names, mun_stats, provider_names, provider_stats, provider_coll_field):
        """Prints two rank-ordered lists and returns two lists of ordered tuples.

        Parameters
        ----------
        service_areas : GeoDataFrame
            GDF of the service areas
        mun_names : list
            List of names of subordinate jurisdictions.
        mun_stats : list
            List of zonal statistics for subordinate jurisdictions.
        provider_names : list
            List of names of service providers
        provider_stats : list
            List of zonal statistics for service providers.
        provider_coll_field : str
            Name of the column containing the recorded collection totals for each service provider.

        Returns
        ----------
        mun_dict_sorted : list
            List of name-stat tuples for subordinate jurisdictions.
        provider_dict_sorted : list
            List of name-stat tuples for service providers.
        """

        # extract total collection values and subtract the total waste generated in each service area

        provider_coll = service_areas[provider_coll_field].values.tolist()
        zip_object = zip(provider_stats, provider_coll)

        provider_uncoll = []
        for i, j in zip_object:
            provider_uncoll.append(i - j)

        # ORGANISE AND PRINT RESULTS

        # combine populated lists into dictionaries
        mun_dict = dict(zip(mun_names, mun_stats))
        provider_dict = dict(zip(provider_names, provider_uncoll))

        # sort dictionaries by value in descending order into list of tuples
        mun_dict_sorted = sorted(mun_dict.items(), key=lambda x: x[1], reverse=True)
        provider_dict_sorted = sorted(provider_dict.items(), key=lambda x: x[1], reverse=True)

        # print the items in the sorted list of tuples
        print('Municipalities by solid waste generated per week (descending):\n')
        [print(i[0],':',f"{int(i[1]):,}", 'tonnes') for i in mun_dict_sorted]

        print('\nService providers by total uncollected solid waste per week (descending)\n')
        [print(i[0],':',f"{int(i[1]):,}", 'tonnes') for i in provider_dict_sorted]

        return mun_dict_sorted, provider_dict_sorted

@timer
def main(state_list='Lagos', sw_ppd=(0.4, 1.2, 0.1)): 
    """Executes all functions, adds zonal stats to GDFs and plots results on a subplot each.

    The majority of hard coded user inputs are declared as enclosing scope variables here.

    Parameters
    ----------
    state_list : str or list
        Setting the default string value in the function definition causes this state to be active on-load. This is useful when service area source files are not available for all states (selecting such a state will cause and OpenFailedError). When interact() is executed, the list of states is passed to main().
    sw_ppd : tuple
        Floats for min, max and step controlling the ipywidgets slider
    """

    # USER INPUT 1: Enclosing Scope Variables

    # equates the element from the list passed to main() by interact() with state_select
    state_select = state_list

    # indicate the name of the column containing the names of municipalities
    mun_name_field = 'ADM2_EN'

    # indicating the filepath to the service areas data source(s)
    fp_service_areas = 'data_files/01_input/01_vector/service_areas_' + state_select.lower() + '.shp'

    # indicating the name of the column containing the names of service providers;
    provider_name_field = 'psp_name'

    # indicate the name of the column containing the weekly collection totals reported
    provider_coll_field = 'total_coll'

    # indicate the filepath to the raster data source;
    fp_raster = 'data_files/01_input/02_raster/hrsl_nga_pop.tif'

    # OPTIONAL CUSTOMIZATIONS

    # the rasterstats zonal statistic to be computed for each jurisdiction (default is 'sum')
    stat_select = 'sum' 

    # If adjusting 'stat_select', also adjust the title of the choropleth maps accordingly
    var_name = 'Tonnes of solid waste generated per week'
    
    # EXECUTE FUNCTIONS

    municipal_filter, bbox, service_areas = getVector(fp_adm, fp_service_areas, state_name_field, state_select)

    array, affine, nodata = computeArray(fp_raster, bbox, sw_ppd)

    mun_names, mun_stats, provider_names, provider_stats = zonalStats(municipal_filter, service_areas, array, affine, nodata, stat_select, mun_name_field, provider_name_field)

    mun_dict_sorted, provider_dict_sorted = processStats(service_areas, mun_names, mun_stats, provider_names, provider_stats, provider_coll_field)

    # UPDATE GEODATAFRAMES WITH RESULTS
    
    # Assign zonal statistics to new columns 
    # 'stat_output' for municipal_filter GDF and 'total_uncoll' for service_areas GDF
    # using two different methods
    
    municipal_filter = municipal_filter.assign(
            stat_output = pd.Series(mun_stats, index = municipal_filter.index)
    )

    for i, row in service_areas.iterrows():
            service_areas.loc[i, 'total_uncoll'] = provider_stats[i] - row[provider_coll_field]
    
    # PLOT RESULTS ON CHOROPLETH MAPS
    
    # Define figure CRS and canvas layout

    myCRS = ccrs.Mercator()

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7.5), subplot_kw=dict(projection=myCRS))

    # --Subplot 1-- 
    
    # add municipal boundaries

    municipal_feat = ShapelyFeature(municipal_filter['geometry'], myCRS, facecolor='none', edgecolor='k', linewidth=0.5)
    ax1.add_feature(municipal_feat)

    # add dynamic title and annotation
    title = var_name + ' by municipalities in ' + state_select + ' State'
    ax1.set_title(title, fontdict={'fontsize': '12', 'fontweight' : '5'})

    ax1.axis('off')

    ax1.annotate('Source: CIESIN HRSL, assuming ' + str(sw_ppd) + 'kg of solid waste per capita per day', xy=(0.225, .025), xycoords='figure fraction', fontsize=12, color='#555555')

    # create colorbar legend
    vmin, vmax =  municipal_filter['stat_output'].min(), municipal_filter['stat_output'].max()
    sm = plt.cm.ScalarMappable(cmap='Blues', norm=plt.Normalize(vmin=vmin, vmax=vmax))
    sm.set_array([])

    fig.colorbar(sm, ax=ax1, orientation="horizontal")
    
    municipal_filter.plot(column='stat_output', cmap='Blues', linewidth=0.8, ax=ax1, edgecolor='0.8')

    # --Sub-plot 2--

    # add dynamic title and annotation
    title2 = 'Tonnes of uncollected solid waste per week by service area (dummy data)'
    ax2.set_title(title2, fontdict={'fontsize': '12', 'fontweight' : '5'})

    ax2.axis('off')

    # Create colorbar legend
    vmin2, vmax2 =  service_areas['total_uncoll'].min(), service_areas['total_uncoll'].max()
    sm = plt.cm.ScalarMappable(cmap='Reds', norm=plt.Normalize(vmin=vmin2, vmax=vmax2))
    sm.set_array([])

    fig.colorbar(sm, ax=ax2, orientation="horizontal")

    municipal_filter.plot(facecolor='none', linewidth=0.5, ax=ax2, edgecolor='k')

    service_areas.plot(column='total_uncoll', cmap='Reds', linewidth=0.8, ax=ax2, edgecolor='k')

if __name__ == "__main__":

    # USER INPUTS 2: Global Scope Variables
    # this set of variables is declared globally to make state_list accessible to interact()

    fp_adm = 'data_files/01_input/01_vector/nga_admbnda_adm2_osgof_20190417.shp'

    # indicate the name of the column indicating the names of the superordinate (e.g. state-level) jurisdictions
    # If this information is in a data source separate from the subordinate tier,
    # a spatial join via GeoPandas or a desktop GIS may be necessary
    state_name_field = 'ADM1_EN'

    # variables declared to be passed to interact()
    municipal_all = gpd.read_file(fp_adm)
    state_list = sorted(municipal_all[state_name_field].unique().tolist())

    interact(main, state_list=state_list)

