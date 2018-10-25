# ===============================================================================
# Copyright 2018 dgketchum
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ===============================================================================

from datetime import datetime

import ee

from map.distribute_points import get_years

YEARS = get_years()
ROI = 'users/dgketchum/boundaries/western_states_polygon'
PLOTS = 'ft:16GE8ltH8obD9lJu6ScJQ02csAzZY27zstaKHgKVD'

IRR = {
    'CO_DIV1': ('ft:1U1yFC2vhtWXX80Gz76mp5kwfHZFE3uaVLZL_OrI2', [1998, 2003, 2006, 2013, 2016]),
    'CO_SanLuis': ('ft:1U1yFC2vhtWXX80Gz76mp5kwfHZFE3uaVLZL_OrI2', [1998, 2003, 2006, 2013, 2016]),
    'CA': ('ft:1U1yFC2vhtWXX80Gz76mp5kwfHZFE3uaVLZL_OrI2', [1991, 1997, 2005, 2008, 2014]),
    'NV': ('ft:1DUcSDaruwvXMIyBEYd2_rCYo8w6D6v4nHTs5nsTR', [x for x in range(2001, 2011)]),
    'UCRB_WY': ('ft:1M0GDErc0dgoYajU_HStZBkp-hBL4kUiZufFdtWHG', [1989, 1996, 2010, 2013, 2016]),  # a.k.a. 2000
    'UCRB_UT_CO': ('ft:1Av2WlcPRBd7JZqYOU73VCLOJ-b5q6H5u6Bboebdv', [1998, 2003, 2006, 2013, 2016]),  # a.k.a. 2005
    'UCRB_UT': ('ft:144ymxhlcv8lj1u_BYQFEC1ITmiISW52q5JvxSVyk', [1998, 2003, 2006, 2013, 2016]),  # a.k.a. 2006
    'UCRB_NM': ('ft:1pBSJDPdFDHARbdc5vpT5FzRek-3KXLKjNBeVyGdR', [1987, 2001, 2004, 2007, 2016]),  # a.k.a. 2009
    'Acequias': ('ft:1emF9Imjj8GPxpRmPU2Oze2hPeojPS4O6udIQNTgX', [1987, 2001, 2004, 2007, 2016]),
}


def filter_irrigated():
    for k, v in IRR.items():
        plots = ee.FeatureCollection(v[0])
        for year in v[1]:
            print(k, year)
            start = '{}-01-01'.format(year)

            late_summer_s = ee.Date(start).advance(7, 'month')
            late_summer_e = ee.Date(start).advance(10, 'month')
            if year < 2013:
                collection = ndvi5()
            else:
                collection = ndvi8()

            late_collection = period_stat(collection, late_summer_s, late_summer_e)
            _buffer = lambda x: x.buffer(-10)
            buffered_fc = plots.map(_buffer)

            int_mean = late_collection.select('nd_mean').reduce(ee.Reducer.intervalMean(0.0, 15.0))

            ndvi_attr = int_mean.reduceRegions(collection=buffered_fc,
                                               reducer=ee.Reducer.mean(),
                                               scale=30.0)

            filt_fc = ndvi_attr.filter(ee.Filter.gt('mean', 0.5))
            task = ee.batch.Export.table.toCloudStorage(filt_fc,
                                                        folder='Irrigation',
                                                        description='{}_{}'.format(k, year),
                                                        bucket='wudr',
                                                        fileNamePrefix='{}_{}'.format(k, year),
                                                        fileFormat='KML')

            task.start()
            break
        break


def request_band_extract(file_prefix):
    roi = ee.FeatureCollection(ROI)
    plots = ee.FeatureCollection(PLOTS)
    for yr in YEARS:
        start = '{}-01-01'.format(yr)
        end_date = '{}-01-01'.format(yr + 1)
        spring_s = '{}-03-01'.format(yr)
        summer_s = '{}-06-01'.format(yr)
        fall_s = '{}-09-01'.format(yr)

        l5_coll = ee.ImageCollection('LANDSAT/LT05/C01/T1_SR').filterBounds(
            roi).filterDate(start, end_date).map(ls5_edge_removal).map(ls57mask)

        l7_coll = ee.ImageCollection('LANDSAT/LE07/C01/T1_SR').filterBounds(
            roi).filterDate(start, end_date).map(ls57mask)

        l8_coll = ee.ImageCollection('LANDSAT/LC08/C01/T1_SR').filterBounds(
            roi).filterDate(start, end_date).map(ls8mask)

        lsSR_masked = ee.ImageCollection(l7_coll.merge(l8_coll).merge(l5_coll))
        lsSR_spr_mn = ee.Image(lsSR_masked.filterDate(spring_s, summer_s).mean())
        lsSR_sum_mn = ee.Image(lsSR_masked.filterDate(summer_s, fall_s).mean())
        lsSR_fal_mn = ee.Image(lsSR_masked.filterDate(fall_s, end_date).mean())

        gridmet = ee.ImageCollection("IDAHO_EPSCOR/GRIDMET").filterBounds(
            roi).filterDate(start, end_date).select('pr', 'eto', 'tmmn', 'tmmx').map(lambda x: x)

        temp_reducer = ee.Reducer.percentile([10, 50, 90])
        t_names = ['tmmn_p10_cy', 'tmmn_p50_cy', 'tmmn_p90_cy', 'tmmx_p10_cy', 'tmmx_p50_cy', 'tmmx_p90_cy']
        temp_perc = gridmet.select('tmmn', 'tmmx').reduce(temp_reducer).rename(t_names)
        precip_reducer = ee.Reducer.sum()
        precip_sum = gridmet.select('pr', 'eto').reduce(precip_reducer).rename('precip_total_cy', 'pet_total_cy')
        wd_estimate = precip_sum.select('precip_total_cy').subtract(precip_sum.select('pet_total_cy')).rename(
            'wd_est_cy')
        input_bands = lsSR_spr_mn.addBands([lsSR_sum_mn, lsSR_fal_mn, temp_perc, precip_sum, wd_estimate])

        dem1 = ee.Image('USGS/NED')
        dem2 = ee.Terrain.products(dem1).select('elevation', 'slope', 'aspect')
        static_input_bands = dem2
        coords = ee.Image.pixelLonLat().rename(['Lon_GCS', 'LAT_GCS'])
        static_input_bands = static_input_bands.addBands(coords)
        input_bands = input_bands.addBands(static_input_bands)

        d = datetime.strptime(start, '%Y-%m-%d')
        epoch = datetime.utcfromtimestamp(0)
        start_millisec = (d - epoch).total_seconds() * 1000

        filtered = plots.filter(ee.Filter.eq('YEAR', ee.Number(start_millisec)))

        plot_sample_regions = input_bands.sampleRegions(
            collection=filtered,
            properties=['POINT_TYPE', 'YEAR'],
            scale=30,
            tileScale=16)

        task = ee.batch.Export.table.toCloudStorage(
            plot_sample_regions,
            description='{}_{}'.format(file_prefix, yr),
            bucket='wudr',
            fileNamePrefix='{}_{}'.format(file_prefix, yr),
            fileFormat='CSV')

        task.start()


def get_qa_bits(image, start, end, qa_mask):
    pattern = 0
    for i in range(start, end - 1):
        pattern += 2 ** i
    return image.select([0], [qa_mask]).bitwiseAnd(pattern).rightShift(start)


def mask_quality(image):
    QA = image.select('pixel_qa')
    shadow = get_qa_bits(QA, 3, 3, 'cloud_shadow')
    cloud = get_qa_bits(QA, 5, 5, 'cloud')
    cirrus_detected = get_qa_bits(QA, 9, 9, 'cirrus_detected')
    return image.updateMask(shadow.eq(0)).updateMask(cloud.eq(0).updateMask(cirrus_detected.eq(0)))


def ls57mask(img):
    sr_bands = img.select('B1', 'B2', 'B3', 'B4', 'B5', 'B7')
    mask_sat = sr_bands.neq(20000)
    img_nsat = sr_bands.updateMask(mask_sat)
    mask1 = img.select('pixel_qa').bitwiseAnd(8).eq(0)
    mask2 = img.select('pixel_qa').bitwiseAnd(32).eq(0)
    mask_p = mask1.And(mask2)
    img_masked = img_nsat.updateMask(mask_p)
    mask_sel = img_masked.select(['B1', 'B2', 'B3', 'B4', 'B5', 'B7'], ['B2', 'B3', 'B4', 'B5', 'B6', 'B7'])
    mask_mult = mask_sel.multiply(0.0001).copyProperties(img, ['system:time_start'])
    return mask_mult


def ls8mask(img):
    sr_bands = img.select('B2', 'B3', 'B4', 'B5', 'B6', 'B7')
    mask_sat = sr_bands.neq(20000)
    img_nsat = sr_bands.updateMask(mask_sat)
    mask1 = img.select('pixel_qa').bitwiseAnd(8).eq(0)
    mask2 = img.select('pixel_qa').bitwiseAnd(32).eq(0)
    mask_p = mask1.And(mask2)
    img_masked = img_nsat.updateMask(mask_p)
    mask_mult = img_masked.multiply(0.0001).copyProperties(img, ['system:time_start'])
    return mask_mult


def ndvi5():
    l = ee.ImageCollection('LANDSAT/LT05/C01/T1_SR').map(lambda x: x.select().addBands(
        x.normalizedDifference(['B4', 'B3'])))
    return l


def ndvi7():
    l = ee.ImageCollection('LANDSAT/LE07/C01/T1_SR').map(lambda x: x.select().addBands(
        x.normalizedDifference(['B4', 'B3'])))
    return l


def ndvi8():
    l = ee.ImageCollection('LANDSAT/LC08/C01/T1_SR').map(lambda x: x.select().addBands(
        x.normalizedDifference(['B5', 'B4'])))
    return l


def ls5_edge_removal(lsImage):
    inner_buffer = lsImage.geometry().buffer(-3000)
    buffer = lsImage.clip(inner_buffer)
    return buffer


def period_stat(collection, start, end):
    c = collection.filterDate(start, end)
    return c.reduce(
        ee.Reducer.mean().combine(reducer2=ee.Reducer.minMax(),
                                  sharedInputs=True))


def is_authorized():
    try:
        ee.Initialize()
        print('Authorized')
        return True
    except Exception as e:
        print('You are not authorized: {}'.format(e))
        return False


if __name__ == '__main__':
    is_authorized()
    prefix = 'filt'
    filter_irrigated()
# ========================= EOF ====================================================================
