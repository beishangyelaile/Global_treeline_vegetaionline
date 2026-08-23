/**
 * Observed alpine treeline extraction, 2000 and 2020,
 * partitioned into polar-facing and equator-facing slopes.
 *
 * Paste this complete file into the Google Earth Engine Code Editor.
 * The default BBOX is a small tracer. Formal global processing must use tiles.
 */

var CONFIG = {
  BBOX: [11.2, 47.1, 11.3, 47.2],
  CONTEXT_BUFFER_M: 2000,

  RUN_EXPORTS: false,
  EXPORT_QA: true,
  EXPORT_GMBA_SUMMARY: false,
  EXPORT_FOLDER: 'Globaltreeline',
  TASK_PREFIX: 'treeline_polar_equator',

  GMBA_ASSET: 'projects/ee-remote/assets/Alpine/GMBA_v2',
  SAYRE_ASSET: 'projects/ee-remote/assets/Alpine/high_mountain',
  SAYRE_CLASSES: [31, 32],
  FOREST_HEIGHT_2000: 'projects/glad/GLCLU2020/Forest_height_2000',
  FOREST_HEIGHT_2020: 'projects/glad/GLCLU2020/Forest_height_2020',
  CHELSA_BIO01: 'projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21',
  AW3D30: 'JAXA/ALOS/AW3D30/V4_1',
  ALOS_LANDFORMS: 'CSP/ERGo/1_0/Global/ALOS_landforms',
  WORLDCOVER: 'ESA/WorldCover/v100',

  // This uploaded UInt16 asset stores deci-Kelvin (2693--2800 in the tracer).
  // Convert to the official CHELSA bio01 physical unit, degrees Celsius.
  CHELSA_SCALE: 0.1,
  CHELSA_OFFSET: -273.15,
  OTSU_THRESHOLD_C: null,

  APPLY_QUARTER_DEGREE_SCREEN: true,
  MIN_HIGH_MOUNTAIN_FRACTION: 0.10,
  MAX_TREE_COVER_FRACTION: 0.95,

  CANOPY_THRESHOLD_M: 3,
  MIN_FOREST_PATCH_HA: 0.5,
  HOLE_MAX_SIZE_PIXELS: 512,
  HOLE_BORDER_WIDTH_M: 90,
  PATCH_COUNT_CAP: 64,
  MEDIAN_RADIUS_PIXELS: 1,

  WINDOW_RADIUS_M: 150,
  MIN_SAMPLES_PER_GROUP: 5,
  T_TEST_VARIANCE: 'welch', // 'welch' or 'pooled'

  // North sector: [315, 360) U [0, 45); south sector: [135, 225).
  ASPECT_HALF_WIDTH_DEG: 45,
  MIN_SLOPE_DEG: 5,
  // Polar/equator direction is undefined very close to the equator.
  EQUATOR_BUFFER_DEG: 0.1,

  STRICT_AW3D_NATIVE_ONLY: false,
  OTSU_MAX_PIXELS: 1e8,
  TILE_SCALE: 4
};

var FINE_CRS = 'EPSG:4326';
var FINE_TRANSFORM = [0.00025, 0, -180, 0, -0.00025, 90];
var CLIMATE_CRS = 'EPSG:4326';
var CLIMATE_TRANSFORM = [1 / 120, 0, -180, 0, -1 / 120, 90];
var QUARTER_DEGREE_TRANSFORM = [0.25, 0, -180, 0, -0.25, 90];

if (CONFIG.RUN_EXPORTS && CONFIG.OTSU_THRESHOLD_C === null) {
  throw new Error(
      'Formal exports require one fixed OTSU_THRESHOLD_C shared by all tiles.');
}
if (['welch', 'pooled'].indexOf(CONFIG.T_TEST_VARIANCE) === -1) {
  throw new Error("T_TEST_VARIANCE must be 'welch' or 'pooled'.");
}
if (CONFIG.ASPECT_HALF_WIDTH_DEG <= 0 || CONFIG.ASPECT_HALF_WIDTH_DEG > 90) {
  throw new Error('ASPECT_HALF_WIDTH_DEG must be in (0, 90].');
}

var batchRegion = ee.Geometry.Rectangle(CONFIG.BBOX, null, false);
var processingRegion = batchRegion.buffer(CONFIG.CONTEXT_BUFFER_M);

function classMask(image, values) {
  var result = image.eq(values[0]);
  for (var i = 1; i < values.length; i++) {
    result = result.or(image.eq(values[i]));
  }
  return result;
}

function selectGmbaIntersectingSayre(gmba, sayreHigh, region) {
  return sayreHigh.rename('sayre_high').reduceRegions({
    collection: gmba.filterBounds(region),
    reducer: ee.Reducer.max().setOutputs(['sayre_hit']),
    scale: sayreHigh.projection().nominalScale(),
    tileScale: CONFIG.TILE_SCALE
  }).filter(ee.Filter.gt('sayre_hit', 0));
}

function quarterDegreeScreen(sayreHigh) {
  var gridProjection = ee.Projection(FINE_CRS, QUARTER_DEGREE_TRANSFORM);
  var sayreProjection = sayreHigh.projection();
  var highFraction = sayreHigh.unmask(0)
      .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 65535})
      .reproject(gridProjection);
  var worldCoverTree = ee.ImageCollection(CONFIG.WORLDCOVER)
      .first().select('Map').eq(10).unmask(0);
  var treeAtSayreScale = worldCoverTree
      .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 4096})
      .reproject(sayreProjection);
  var treeFraction = treeAtSayreScale
      .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 65535})
      .reproject(gridProjection);
  return highFraction.gte(CONFIG.MIN_HIGH_MOUNTAIN_FRACTION)
      .and(treeFraction.lte(CONFIG.MAX_TREE_COVER_FRACTION))
      .rename('valid_quarter_degree_cell');
}

function buildAw3d(region) {
  var collection = ee.ImageCollection(CONFIG.AW3D30)
      .filterBounds(region)
      .map(function(image) {
        var msk = image.select('MSK');
        var valid = CONFIG.STRICT_AW3D_NATIVE_ONLY ? msk.eq(0) : msk.neq(1);
        return image.select('DSM').rename('elevation').updateMask(valid)
            .addBands(image.select('STK').rename('dem_stk').updateMask(valid))
            .addBands(msk.rename('dem_msk').updateMask(valid));
      });
  var projection = ee.Image(collection.first()).select('elevation').projection();
  return collection.mosaic().setDefaultProjection(projection).clip(region);
}

function fillInteriorHoles(forest, region) {
  var background = forest.not().clip(region).rename('background');
  var labels = background.selfMask().connectedComponents(
      ee.Kernel.plus(1), CONFIG.HOLE_MAX_SIZE_PIXELS).select('labels');
  var inner = region.buffer(-CONFIG.HOLE_BORDER_WIDTH_M);
  var borderRing = region.difference(inner, 30);
  var border = ee.Image(0).byte().paint(
      ee.FeatureCollection([ee.Feature(borderRing)]), 1)
      .setDefaultProjection(forest.projection()).rename('touch');
  var touchesBorder = border.addBands(labels).reduceConnectedComponents({
    reducer: ee.Reducer.max(),
    labelBand: 'labels',
    maxSize: CONFIG.HOLE_MAX_SIZE_PIXELS
  }).select('touch');
  var holes = labels.mask().and(touchesBorder.eq(0));
  return forest.or(holes).rename('forest_filled').clip(region);
}

function cleanForest(canopyAsset, region) {
  var forest = ee.Image(canopyAsset).select([0])
      .gt(CONFIG.CANOPY_THRESHOLD_M).unmask(0).clip(region)
      .rename('forest_raw');
  var filled = fillInteriorHoles(forest, region);
  var count = filled.selfMask().connectedPixelCount(
      CONFIG.PATCH_COUNT_CAP, true);
  var componentAreaM2 = count.multiply(ee.Image.pixelArea());
  return filled.updateMask(componentAreaM2.gte(
      CONFIG.MIN_FOREST_PATCH_HA * 10000))
      .unmask(0).rename('forest_clean').clip(region);
}

function forestEdges(forest, domain) {
  var smoothed = forest.focalMedian(
      CONFIG.MEDIAN_RADIUS_PIXELS, 'square', 'pixels');
  return smoothed.toFloat().convolve(ee.Kernel.laplacian8())
      .zeroCrossing().gt(0).and(domain)
      .selfMask().rename('forest_edge');
}

function otsuThreshold(histogram) {
  histogram = ee.Dictionary(histogram);
  var counts = ee.Array(histogram.get('histogram'));
  var means = ee.Array(histogram.get('bucketMeans'));
  var size = ee.Number(means.length().get([0]));
  var total = ee.Number(counts.reduce(ee.Reducer.sum(), [0]).get([0]));
  var weightedSum = ee.Number(
      means.multiply(counts).reduce(ee.Reducer.sum(), [0]).get([0]));
  var globalMean = weightedSum.divide(total);
  var scores = ee.List.sequence(1, size.subtract(1)).map(function(index) {
    index = ee.Number(index);
    var aCounts = counts.slice(0, 0, index);
    var aCount = ee.Number(aCounts.reduce(ee.Reducer.sum(), [0]).get([0]));
    var aMean = ee.Number(means.slice(0, 0, index).multiply(aCounts)
        .reduce(ee.Reducer.sum(), [0]).get([0])).divide(aCount);
    var bCount = total.subtract(aCount);
    var bMean = weightedSum.subtract(aCount.multiply(aMean)).divide(bCount);
    return aCount.multiply(aMean.subtract(globalMean).pow(2))
        .add(bCount.multiply(bMean.subtract(globalMean).pow(2)));
  });
  var bestIndex = ee.Array(scores).argmax().get(0);
  return ee.Number(means.toList().get(bestIndex));
}

function temperatureAndThreshold(calibrationCandidates) {
  var raw = ee.Image(CONFIG.CHELSA_BIO01).select([0]).rename('bio01_raw');
  var temperature = raw
      .multiply(CONFIG.CHELSA_SCALE).add(CONFIG.CHELSA_OFFSET)
      .rename('temperature_c');
  var candidateOnClimateGrid = calibrationCandidates.unmask(0)
      .reduceResolution({reducer: ee.Reducer.max(), maxPixels: 4096})
      .reproject(temperature.projection()).gt(0);
  var histogram = ee.Dictionary(raw.updateMask(candidateOnClimateGrid)
      .reduceRegion({
        reducer: ee.Reducer.histogram({maxBuckets: 256, minBucketWidth: 1}),
        geometry: batchRegion,
        scale: raw.projection().nominalScale(),
        maxPixels: CONFIG.OTSU_MAX_PIXELS,
        tileScale: CONFIG.TILE_SCALE
      }).get('bio01_raw'));
  var derivedRaw = CONFIG.OTSU_THRESHOLD_C === null ?
      otsuThreshold(histogram) : null;
  var derived = CONFIG.OTSU_THRESHOLD_C === null ?
      derivedRaw.multiply(CONFIG.CHELSA_SCALE).add(CONFIG.CHELSA_OFFSET) : null;
  var threshold = CONFIG.OTSU_THRESHOLD_C === null ?
      derived : ee.Number(CONFIG.OTSU_THRESHOLD_C);
  return {
    raw: raw,
    image: temperature,
    histogram: histogram,
    derivedRawThreshold: derivedRaw,
    derivedThreshold: derived,
    threshold: threshold,
    coldMask: temperature.lte(threshold).rename('cold_zone')
  };
}

function oneSidedTCritical(df) {
  var table = [
    [1, -6.314], [2, -2.920], [3, -2.353], [4, -2.132],
    [5, -2.015], [6, -1.943], [7, -1.895], [8, -1.860],
    [9, -1.833], [10, -1.812], [12, -1.782], [15, -1.753],
    [20, -1.725], [30, -1.697], [40, -1.684], [60, -1.671],
    [120, -1.658], [1000, -1.646]
  ];
  var critical = ee.Image.constant(table[0][1]);
  for (var i = 1; i < table.length; i++) {
    critical = critical.where(df.gte(table[i][0]), table[i][1]);
  }
  return critical;
}

function upperEdgeTest(forest, dem, candidates, populationMask) {
  var kernel = ee.Kernel.square(
      CONFIG.WINDOW_RADIUS_M, 'meters', false);
  var reducer = ee.Reducer.mean()
      .combine(ee.Reducer.variance(), '', true)
      .combine(ee.Reducer.count(), '', true);
  var forestStats = dem.updateMask(forest.eq(1).and(populationMask))
      .rename('elevation').reduceNeighborhood({
        reducer: reducer, kernel: kernel, skipMasked: false
      });
  var nonForestStats = dem.updateMask(forest.eq(0).and(populationMask))
      .rename('elevation').reduceNeighborhood({
        reducer: reducer, kernel: kernel, skipMasked: false
      });
  var nf = forestStats.select('elevation_count');
  var nn = nonForestStats.select('elevation_count');
  var mf = forestStats.select('elevation_mean');
  var mn = nonForestStats.select('elevation_mean');
  var vf = forestStats.select('elevation_variance');
  var vn = nonForestStats.select('elevation_variance');
  var standardError;
  var degreesOfFreedom;
  if (CONFIG.T_TEST_VARIANCE === 'pooled') {
    var pooledVariance = vf.multiply(nf.subtract(1))
        .add(vn.multiply(nn.subtract(1)))
        .divide(nf.add(nn).subtract(2));
    standardError = pooledVariance.multiply(
        ee.Image(1).divide(nf).add(ee.Image(1).divide(nn))).sqrt();
    degreesOfFreedom = nf.add(nn).subtract(2);
  } else {
    var vfMean = vf.divide(nf);
    var vnMean = vn.divide(nn);
    var se2 = vfMean.add(vnMean);
    standardError = se2.sqrt();
    degreesOfFreedom = se2.pow(2).divide(
        vfMean.pow(2).divide(nf.subtract(1))
            .add(vnMean.pow(2).divide(nn.subtract(1))));
  }
  var t = mf.subtract(mn).divide(standardError);
  var enough = nf.gte(CONFIG.MIN_SAMPLES_PER_GROUP)
      .and(nn.gte(CONFIG.MIN_SAMPLES_PER_GROUP))
      .and(standardError.gt(0));
  return candidates.and(enough)
      .and(t.lt(oneSidedTCritical(degreesOfFreedom)))
      .selfMask().rename('upper_edge');
}

function aspectGroups(dem) {
  var aspect = ee.Terrain.aspect(dem).rename('aspect_deg');
  var slope = ee.Terrain.slope(dem).rename('slope_deg');
  var halfWidth = CONFIG.ASPECT_HALF_WIDTH_DEG;
  var north = aspect.gte(360 - halfWidth).or(aspect.lt(halfWidth));
  var south = aspect.gte(180 - halfWidth).and(aspect.lt(180 + halfWidth));
  var latitude = ee.Image.pixelLonLat().select('latitude');
  var northHemisphere = latitude.gt(CONFIG.EQUATOR_BUFFER_DEG);
  var southHemisphere = latitude.lt(-CONFIG.EQUATOR_BUFFER_DEG);
  var validSlope = slope.gte(CONFIG.MIN_SLOPE_DEG);

  var polar = northHemisphere.and(north)
      .or(southHemisphere.and(south)).and(validSlope)
      .rename('polar_facing');
  var equator = northHemisphere.and(south)
      .or(southHemisphere.and(north)).and(validSlope)
      .rename('equator_facing');
  var aspectClass = polar.unmask(0).multiply(0).byte()
      .where(polar, 1).where(equator, 2)
      .rename('aspect_class');
  return {
    aspect: aspect,
    slope: slope,
    polar: polar,
    equator: equator,
    aspectClass: aspectClass
  };
}

function extractTreeline(canopyAsset, year, groupName, groupMask,
                         domain, dem, nonValley, coldMask,
                         precomputedForest) {
  var forest = precomputedForest || cleanForest(canopyAsset, processingRegion);
  var groupDomain = groupMask.and(domain);
  var edges = forestEdges(forest, groupDomain);
  var candidates = edges.and(nonValley).and(coldMask)
      .selfMask().rename('candidate_' + groupName + '_' + year);
  // Both populations are restricted to the same orientation class.
  var upper = upperEdgeTest(forest, dem, candidates, groupMask)
      .rename('upper_' + groupName + '_' + year);
  var elevation = dem.updateMask(upper)
      .rename('treeline_' + groupName + '_' + year + '_m').toFloat();
  return {
    forest: forest,
    edges: edges,
    candidates: candidates,
    upper: upper,
    elevation: elevation
  };
}

function aggregateToClimateGrid(image) {
  return image.reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 4096})
      .reproject(ee.Projection(CLIMATE_CRS, CLIMATE_TRANSFORM));
}

// ---------------------------------------------------------------------------
// Build domain, common climate threshold, and orientation-specific products.
// ---------------------------------------------------------------------------

var sayre = ee.Image(CONFIG.SAYRE_ASSET).select([0]);
var sayreHigh = classMask(sayre, CONFIG.SAYRE_CLASSES)
    .unmask(0).rename('sayre_high');
var gmba = ee.FeatureCollection(CONFIG.GMBA_ASSET);
var selectedGmba = selectGmbaIntersectingSayre(
    gmba, sayreHigh, processingRegion);
var gmbaMask = ee.Image(0).byte().paint(selectedGmba, 1)
    .setDefaultProjection(sayreHigh.projection())
    .rename('gmba_selected').unmask(0);
var gridScreen = CONFIG.APPLY_QUARTER_DEGREE_SCREEN ?
    quarterDegreeScreen(sayreHigh) : ee.Image.constant(1);
var domain = sayreHigh.and(gmbaMask).and(gridScreen)
    .selfMask().rename('analysis_domain');

var aw3d = buildAw3d(processingRegion);
var dem = aw3d.select('elevation').toFloat();
var groups = aspectGroups(dem);
var landforms = ee.Image(CONFIG.ALOS_LANDFORMS).select('constant');
var nonValley = landforms.neq(41).and(landforms.neq(42))
    .rename('non_valley');

var forest2020 = cleanForest(CONFIG.FOREST_HEIGHT_2020, processingRegion);
// Calibrate temperature on unsplit 2020 edges so both aspect groups and the
// no-aspect version can use exactly the same thermal threshold.
var preliminaryEdges2020 = forestEdges(forest2020, domain).and(nonValley);
var temperatureInfo = temperatureAndThreshold(preliminaryEdges2020);

var polar2000 = extractTreeline(
    CONFIG.FOREST_HEIGHT_2000, 2000, 'polar', groups.polar,
    domain, dem, nonValley, temperatureInfo.coldMask, null);
var equator2000 = extractTreeline(
    CONFIG.FOREST_HEIGHT_2000, 2000, 'equator', groups.equator,
    domain, dem, nonValley, temperatureInfo.coldMask, polar2000.forest);
var polar2020 = extractTreeline(
    CONFIG.FOREST_HEIGHT_2020, 2020, 'polar', groups.polar,
    domain, dem, nonValley, temperatureInfo.coldMask, forest2020);
var equator2020 = extractTreeline(
    CONFIG.FOREST_HEIGHT_2020, 2020, 'equator', groups.equator,
    domain, dem, nonValley, temperatureInfo.coldMask, forest2020);

var treeline30m = polar2000.elevation
    .addBands(equator2000.elevation)
    .addBands(polar2020.elevation)
    .addBands(equator2020.elevation)
    .set({
      method: 'Liang_et_al_2026_observed_treeline_reproduction',
      aspect_partition: 'hemisphere_polar_equator',
      otsu_threshold_c: temperatureInfo.threshold,
      aspect_half_width_deg: CONFIG.ASPECT_HALF_WIDTH_DEG,
      minimum_slope_deg: CONFIG.MIN_SLOPE_DEG,
      equator_buffer_deg: CONFIG.EQUATOR_BUFFER_DEG,
      t_test: CONFIG.T_TEST_VARIANCE + '_one_sided_alpha_0.05'
    });

var polar2000_1km = aggregateToClimateGrid(polar2000.elevation)
    .rename('polar_2000_mean_m');
var equator2000_1km = aggregateToClimateGrid(equator2000.elevation)
    .rename('equator_2000_mean_m');
var polar2020_1km = aggregateToClimateGrid(polar2020.elevation)
    .rename('polar_2020_mean_m');
var equator2020_1km = aggregateToClimateGrid(equator2020.elevation)
    .rename('equator_2020_mean_m');
var polarShift = polar2020_1km.subtract(polar2000_1km).divide(20)
    .rename('polar_shift_m_per_year');
var equatorShift = equator2020_1km.subtract(equator2000_1km).divide(20)
    .rename('equator_shift_m_per_year');
var treeline1km = polar2000_1km.addBands(equator2000_1km)
    .addBands(polar2020_1km).addBands(equator2020_1km)
    .addBands(polarShift).addBands(equatorShift)
    .set('otsu_threshold_c', temperatureInfo.threshold);

var qa30m = domain.unmask(0).byte()
    .addBands(groups.aspectClass.unmask(0).byte())
    .addBands(groups.aspect.unmask(-1).rename('aspect_deg').toFloat())
    .addBands(groups.slope.unmask(-1).rename('slope_deg').toFloat())
    .addBands(polar2000.forest.rename('forest_2000').byte())
    .addBands(forest2020.rename('forest_2020').byte())
    .addBands(polar2000.upper.unmask(0).rename('polar_upper_2000').byte())
    .addBands(equator2000.upper.unmask(0).rename('equator_upper_2000').byte())
    .addBands(polar2020.upper.unmask(0).rename('polar_upper_2020').byte())
    .addBands(equator2020.upper.unmask(0).rename('equator_upper_2020').byte())
    .addBands(temperatureInfo.coldMask.unmask(0).rename('cold_zone').byte())
    .addBands(aw3d.select('dem_msk').unmask(255).toUint8())
    .addBands(aw3d.select('dem_stk').unmask(0).toUint8())
    .toFloat();

var temperatureQa = temperatureInfo.image.reduceRegion({
  reducer: ee.Reducer.minMax().combine(ee.Reducer.mean(), '', true),
  geometry: batchRegion,
  scale: temperatureInfo.image.projection().nominalScale(),
  maxPixels: CONFIG.OTSU_MAX_PIXELS,
  tileScale: CONFIG.TILE_SCALE
});

print('CONFIG', CONFIG);
print('GMBA first feature (inspect hierarchy fields)', gmba.first());
print('GMBA features intersecting Sayre 31/32 in buffered tile', selectedGmba.size());
print('CHELSA bio01 QA; expected physical unit is deg C', temperatureQa);
print('Otsu threshold on original integer BIO1', temperatureInfo.derivedRawThreshold);
print('Derived Otsu threshold converted to deg C', temperatureInfo.derivedThreshold);
print('Threshold actually used (deg C)', temperatureInfo.threshold);
print('Aspect class: 0=excluded/other, 1=polar-facing, 2=equator-facing');
print('WARNING', CONFIG.OTSU_THRESHOLD_C === null ?
    'Tracer only: do not compare tiles until one fixed Otsu threshold is configured.' :
    'Fixed Otsu threshold configured; tiles are comparable.');

Map.centerObject(batchRegion, 11);
Map.addLayer(selectedGmba, {color: '7f7f7f'}, 'Selected GMBA', false);
Map.addLayer(domain, {palette: ['f1c40f']}, 'Analysis domain', false);
Map.addLayer(groups.aspectClass.updateMask(groups.aspectClass.gt(0)),
    {min: 1, max: 2, palette: ['2166ac', 'b2182b']},
    'Aspect groups (1 polar, 2 equator)');
Map.addLayer(polar2020.elevation,
    {min: 0, max: 5000, palette: ['313695', 'ffffbf', 'a50026']},
    'Polar-facing treeline 2020');
Map.addLayer(equator2020.elevation,
    {min: 0, max: 5000, palette: ['2c7bb6', 'ffffbf', 'd7191c']},
    'Equator-facing treeline 2020');
Map.addLayer(polarShift,
    {min: -5, max: 5, palette: ['2166ac', 'f7f7f7', 'b2182b']},
    'Polar-facing shift m/yr', false);
Map.addLayer(equatorShift,
    {min: -5, max: 5, palette: ['2166ac', 'f7f7f7', 'b2182b']},
    'Equator-facing shift m/yr', false);

if (CONFIG.RUN_EXPORTS) {
  Export.image.toDrive({
    image: treeline30m.clip(batchRegion).toFloat(),
    description: CONFIG.TASK_PREFIX + '_30m',
    fileNamePrefix: CONFIG.TASK_PREFIX + '_30m',
    folder: CONFIG.EXPORT_FOLDER,
    region: batchRegion,
    crs: FINE_CRS,
    crsTransform: FINE_TRANSFORM,
    maxPixels: 1e13,
    fileFormat: 'GeoTIFF',
    formatOptions: {cloudOptimized: true}
  });
  Export.image.toDrive({
    image: treeline1km.clip(batchRegion).toFloat(),
    description: CONFIG.TASK_PREFIX + '_1km',
    fileNamePrefix: CONFIG.TASK_PREFIX + '_1km',
    folder: CONFIG.EXPORT_FOLDER,
    region: batchRegion,
    crs: CLIMATE_CRS,
    crsTransform: CLIMATE_TRANSFORM,
    maxPixels: 1e13,
    fileFormat: 'GeoTIFF',
    formatOptions: {cloudOptimized: true}
  });
  if (CONFIG.EXPORT_QA) {
    Export.image.toDrive({
      image: qa30m.clip(batchRegion),
      description: CONFIG.TASK_PREFIX + '_qa30m',
      fileNamePrefix: CONFIG.TASK_PREFIX + '_qa30m',
      folder: CONFIG.EXPORT_FOLDER,
      region: batchRegion,
      crs: FINE_CRS,
      crsTransform: FINE_TRANSFORM,
      maxPixels: 1e13,
      fileFormat: 'GeoTIFF',
      formatOptions: {cloudOptimized: true}
    });
  }
  if (CONFIG.EXPORT_GMBA_SUMMARY) {
    // Standard contains overlapping hierarchy levels. Use Standard-Basic or
    // select one level before interpreting rows as independent mountains.
    var batchMountains = selectedGmba.map(function(feature) {
      return feature.intersection(batchRegion, 100)
          .set('gmba_index', feature.get('system:index'));
    });
    var gmbaSummary = treeline1km.reduceRegions({
      collection: batchMountains,
      reducer: ee.Reducer.mean(),
      scale: 1000,
      crs: CLIMATE_CRS,
      tileScale: CONFIG.TILE_SCALE
    });
    Export.table.toDrive({
      collection: gmbaSummary,
      description: CONFIG.TASK_PREFIX + '_gmba_summary',
      fileNamePrefix: CONFIG.TASK_PREFIX + '_gmba_summary',
      folder: CONFIG.EXPORT_FOLDER,
      fileFormat: 'CSV'
    });
  }
}
