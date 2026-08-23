/**
 * Observed alpine treeline extraction, 2000 and 2020 (no aspect partition).
 *
 * Paste this complete file into the Google Earth Engine Code Editor.
 * The default BBOX is a small tracer. Formal global processing must use tiles.
 *
 * Study domain:
 *   GMBA v2 Standard ∩ Sayre classes 31/32 ∩ valid 0.25-degree cells.
 *
 * Important reproducibility rule:
 *   OTSU_THRESHOLD_C may be null for a tracer/calibration run, but a formal
 *   export requires one fixed numeric threshold shared by every tile.
 */

var CONFIG = {
  // Small tracer only. Replace by one explicit export tile [W, S, E, N].
  BBOX: [11.2, 47.1, 11.3, 47.2],
  CONTEXT_BUFFER_M: 2000,

  RUN_EXPORTS: false,
  EXPORT_QA: true,
  EXPORT_GMBA_SUMMARY: false,
  EXPORT_FOLDER: 'Globaltreeline',
  TASK_PREFIX: 'treeline_no_aspect',

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
  // Largest background component eligible for topology-preserving hole fill.
  // This is an explicit implementation choice; the paper gives no value.
  HOLE_MAX_SIZE_PIXELS: 512,
  HOLE_BORDER_WIDTH_M: 90,
  PATCH_COUNT_CAP: 64,
  MEDIAN_RADIUS_PIXELS: 1,

  WINDOW_RADIUS_M: 150,
  MIN_SAMPLES_PER_GROUP: 5,
  T_TEST_VARIANCE: 'welch', // 'welch' or 'pooled'

  // false: accept every catalog-valid source (MSK != 1).
  // true: retain only original valid AW3D pixels (MSK == 0).
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
      'Formal exports require a fixed OTSU_THRESHOLD_C. Run once with ' +
      'RUN_EXPORTS=false, record the calibrated threshold, then paste it into CONFIG.');
}
if (['welch', 'pooled'].indexOf(CONFIG.T_TEST_VARIANCE) === -1) {
  throw new Error("T_TEST_VARIANCE must be 'welch' or 'pooled'.");
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
  var candidates = gmba.filterBounds(region);
  var hitReducer = ee.Reducer.max().setOutputs(['sayre_hit']);
  return sayreHigh.rename('sayre_high').reduceRegions({
    collection: candidates,
    reducer: hitReducer,
    scale: sayreHigh.projection().nominalScale(),
    tileScale: CONFIG.TILE_SCALE
  }).filter(ee.Filter.gt('sayre_hit', 0));
}

function quarterDegreeScreen(sayreHigh) {
  var gridProjection = ee.Projection(FINE_CRS, QUARTER_DEGREE_TRANSFORM);
  var sayreProjection = sayreHigh.projection();

  var highFraction = sayreHigh.unmask(0)
      .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 65535})
      .reproject(gridProjection)
      .rename('high_mountain_fraction');

  var worldCoverTree = ee.ImageCollection(CONFIG.WORLDCOVER)
      .first().select('Map').eq(10).unmask(0);
  // Two-stage aggregation avoids exceeding reduceResolution maxPixels when
  // moving directly from 10 m to 0.25 degrees.
  var treeAtSayreScale = worldCoverTree
      .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 4096})
      .reproject(sayreProjection);
  var treeFraction = treeAtSayreScale
      .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 65535})
      .reproject(gridProjection)
      .rename('tree_cover_fraction');

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
  // Label only non-forest objects no larger than HOLE_MAX_SIZE_PIXELS.
  // connectedComponents masks larger objects, so they remain non-forest.
  var background = forest.not().clip(region).rename('background');
  var labels = background.selfMask().connectedComponents(
      ee.Kernel.plus(1), CONFIG.HOLE_MAX_SIZE_PIXELS).select('labels');

  // A background object touching the buffered processing boundary is exterior
  // and must not be filled. This preserves the outer forest boundary.
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
  // Process before applying the study mask so domain boundaries are not
  // misidentified as forest boundaries.
  var forest = ee.Image(canopyAsset).select([0])
      .gt(CONFIG.CANOPY_THRESHOLD_M).unmask(0).clip(region)
      .rename('forest_raw');
  var filled = fillInteriorHoles(forest, region);
  var count = filled.selfMask().connectedPixelCount(
      CONFIG.PATCH_COUNT_CAP, true);
  var componentAreaM2 = count.multiply(ee.Image.pixelArea());
  var keep = componentAreaM2.gte(CONFIG.MIN_FOREST_PATCH_HA * 10000);
  return filled.updateMask(keep).unmask(0).rename('forest_clean').clip(region);
}

function forestEdges(forest, domain) {
  var smoothed = forest.focalMedian(
      CONFIG.MEDIAN_RADIUS_PIXELS, 'square', 'pixels');
  var laplacian = smoothed.toFloat().convolve(ee.Kernel.laplacian8());
  return laplacian.zeroCrossing().gt(0)
      .and(domain).selfMask().rename('forest_edge');
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

  var indices = ee.List.sequence(1, size.subtract(1));
  var scores = indices.map(function(index) {
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

// Conservative stepwise critical values for a lower-tail, one-sided t test
// with alpha=0.05. The condition is mean(forest) < mean(non-forest).
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

  var forestPopulation = forest.eq(1).and(populationMask);
  var nonForestPopulation = forest.eq(0).and(populationMask);
  var forestStats = dem.updateMask(forestPopulation).rename('elevation')
      .reduceNeighborhood({
        reducer: reducer,
        kernel: kernel,
        skipMasked: false
      });
  var nonForestStats = dem.updateMask(nonForestPopulation).rename('elevation')
      .reduceNeighborhood({
        reducer: reducer,
        kernel: kernel,
        skipMasked: false
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

function extractTreeline(canopyAsset, year, domain, dem, nonValley,
                         coldMask, populationMask, precomputedForest) {
  var forest = precomputedForest || cleanForest(canopyAsset, processingRegion);
  var edges = forestEdges(forest, domain);
  var candidates = edges.and(nonValley).and(coldMask)
      .selfMask().rename('candidate_' + year);
  var upper = upperEdgeTest(
      forest, dem, candidates, populationMask).rename('upper_' + year);
  var elevation = dem.updateMask(upper)
      .rename('treeline_' + year + '_m').toFloat();
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
// Build the study domain and products.
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
var landforms = ee.Image(CONFIG.ALOS_LANDFORMS).select('constant');
var nonValley = landforms.neq(41).and(landforms.neq(42))
    .rename('non_valley');
var allOrientations = ee.Image.constant(1).clip(processingRegion);

// Use 2020 preliminary edges, after the valley screen, to calibrate the Otsu
// split. Formal tiles must reuse the same fixed threshold.
var forest2020 = cleanForest(CONFIG.FOREST_HEIGHT_2020, processingRegion);
var preliminaryEdges2020 = forestEdges(forest2020, domain).and(nonValley);
var temperatureInfo = temperatureAndThreshold(preliminaryEdges2020);

var result2000 = extractTreeline(
    CONFIG.FOREST_HEIGHT_2000, 2000, domain, dem, nonValley,
    temperatureInfo.coldMask, allOrientations, null);
var result2020 = extractTreeline(
    CONFIG.FOREST_HEIGHT_2020, 2020, domain, dem, nonValley,
    temperatureInfo.coldMask, allOrientations, forest2020);

var treeline30m = result2000.elevation.addBands(result2020.elevation)
    .set({
      method: 'Liang_et_al_2026_observed_treeline_reproduction',
      aspect_partition: 'none',
      otsu_threshold_c: temperatureInfo.threshold,
      t_test: CONFIG.T_TEST_VARIANCE + '_one_sided_alpha_0.05',
      hole_fill_max_size_pixels: CONFIG.HOLE_MAX_SIZE_PIXELS
    });
var treeline2000_1km = aggregateToClimateGrid(result2000.elevation)
    .rename('treeline_2000_mean_m');
var treeline2020_1km = aggregateToClimateGrid(result2020.elevation)
    .rename('treeline_2020_mean_m');
var shiftRate1km = treeline2020_1km.subtract(treeline2000_1km)
    .divide(20).rename('shift_2000_2020_m_per_year').toFloat();
var treeline1km = treeline2000_1km.addBands(treeline2020_1km)
    .addBands(shiftRate1km)
    .set('otsu_threshold_c', temperatureInfo.threshold);

var qa30m = domain.unmask(0).byte()
    .addBands(result2000.forest.rename('forest_2000').byte())
    .addBands(result2020.forest.rename('forest_2020').byte())
    .addBands(result2000.edges.unmask(0).rename('edge_2000').byte())
    .addBands(result2020.edges.unmask(0).rename('edge_2020').byte())
    .addBands(result2000.upper.unmask(0).rename('upper_2000').byte())
    .addBands(result2020.upper.unmask(0).rename('upper_2020').byte())
    .addBands(temperatureInfo.coldMask.unmask(0).rename('cold_zone').byte())
    .addBands(nonValley.unmask(0).byte())
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
print('WARNING', CONFIG.OTSU_THRESHOLD_C === null ?
    'Tracer only: do not compare tiles until one fixed Otsu threshold is configured.' :
    'Fixed Otsu threshold configured; tiles are comparable.');

Map.centerObject(batchRegion, 11);
Map.addLayer(selectedGmba, {color: '7f7f7f'}, 'Selected GMBA', false);
Map.addLayer(domain, {palette: ['f1c40f']}, 'Analysis domain', false);
Map.addLayer(result2000.elevation,
    {min: 0, max: 5000, palette: ['2c7bb6', 'ffffbf', 'd7191c']},
    'Treeline elevation 2000');
Map.addLayer(result2020.elevation,
    {min: 0, max: 5000, palette: ['313695', 'ffffbf', 'a50026']},
    'Treeline elevation 2020');
Map.addLayer(shiftRate1km,
    {min: -5, max: 5, palette: ['2166ac', 'f7f7f7', 'b2182b']},
    'Treeline shift m/yr', false);
Map.addLayer(qa30m.select('dem_msk'), {min: 0, max: 52}, 'AW3D MSK', false);

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
    // Standard contains overlapping hierarchy levels. Do not treat every row
    // as an independent mountain unless a non-overlapping level is selected.
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
