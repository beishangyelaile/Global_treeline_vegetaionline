"""Python/geemap port of the 2026-08-21 observed-treeline GEE scripts.

The default tracer is read-only: ``--check`` writes a JSON console report and
an HTML map, but never starts an Earth Engine export. Formal exports are only
reachable through ``--export`` and require a fixed Otsu threshold in degrees C.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# This script is named code.py. Remove its directory before importing geemap so
# IPython imports Python's standard-library ``code`` module instead of this file.
SCRIPT_DIR = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0]).resolve() == SCRIPT_DIR:
    sys.path.pop(0)

import ee
import geemap
import google.auth
from google.auth.transport.requests import Request


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FOREST_HEIGHT_2000 = "projects/glad/GLCLU2020/Forest_height_2000"
FOREST_HEIGHT_2020 = "projects/glad/GLCLU2020/Forest_height_2020"
AW3D30 = "JAXA/ALOS/AW3D30/V4_1"
ALOS_LANDFORMS = "CSP/ERGo/1_0/Global/ALOS_landforms"
WORLDCOVER = "ESA/WorldCover/v100"
SAYRE_CLASSES = (31, 32)
VALLEY_CLASSES = (41, 42)
FINE_CRS = "EPSG:4326"
FINE_TRANSFORM = [0.00025, 0, -180, 0, -0.00025, 90]
CLIMATE_CRS = "EPSG:4326"
CLIMATE_TRANSFORM = [1 / 120, 0, -180, 0, -1 / 120, 90]
QUARTER_DEGREE_TRANSFORM = [0.25, 0, -180, 0, -0.25, 90]
WORKLOAD_TAG = "global-treeline-20260821"
ADC_SCOPES = (
    "https://www.googleapis.com/auth/earthengine",
    "https://www.googleapis.com/auth/cloud-platform",
)
ONE_SIDED_T_CRITICAL_95 = (
    (1, -6.314),
    (2, -2.920),
    (3, -2.353),
    (4, -2.132),
    (5, -2.015),
    (6, -1.943),
    (7, -1.895),
    (8, -1.860),
    (9, -1.833),
    (10, -1.812),
    (12, -1.782),
    (15, -1.753),
    (20, -1.725),
    (30, -1.697),
    (40, -1.684),
    (60, -1.671),
    (120, -1.658),
    (1000, -1.646),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Resolve parameters without an EE call")
    mode.add_argument("--check", action="store_true", help="Run the first read-only tracer")
    mode.add_argument("--export", action="store_true", help="Start opt-in Drive exports")
    mode.add_argument("--monitor-once", metavar="REGISTRY", help="Read registered task states once")

    parser.add_argument("--project", default=os.environ.get("EE_PROJECT"))
    parser.add_argument("--gmba-asset", default=os.environ.get("GMBA_ASSET"))
    parser.add_argument("--sayre-asset", default=os.environ.get("SAYRE_ASSET"))
    parser.add_argument("--chelsa-bio01", default=os.environ.get("CHELSA_BIO01"))
    parser.add_argument("--bbox", type=float, nargs=4, default=[11.2, 47.1, 11.3, 47.2])
    parser.add_argument("--aspect-mode", choices=("none", "polar-equator"), default="none")
    parser.add_argument("--context-buffer-m", type=float, default=2000)
    parser.add_argument("--temperature-scale", type=float, default=0.1)
    parser.add_argument("--temperature-offset", type=float, default=-273.15)
    parser.add_argument("--otsu-threshold-c", type=float)
    parser.add_argument("--apply-quarter-degree-screen", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--minimum-high-mountain-fraction", type=float, default=0.10)
    parser.add_argument("--maximum-tree-cover-fraction", type=float, default=0.95)
    parser.add_argument("--canopy-threshold-m", type=float, default=3.0)
    parser.add_argument("--minimum-forest-patch-ha", type=float, default=0.5)
    parser.add_argument("--hole-max-size-pixels", type=int, default=512)
    parser.add_argument("--hole-border-width-m", type=float, default=90)
    parser.add_argument("--patch-count-cap", type=int, default=64)
    parser.add_argument("--median-radius-pixels", type=float, default=1)
    parser.add_argument("--window-radius-m", type=float, default=150)
    parser.add_argument("--minimum-samples-per-group", type=int, default=5)
    parser.add_argument("--t-test-variance", choices=("welch", "pooled"), default="welch")
    parser.add_argument("--aspect-half-width-deg", type=float, default=45)
    parser.add_argument("--minimum-slope-deg", type=float, default=5)
    parser.add_argument("--equator-buffer-deg", type=float, default=0.1)
    parser.add_argument("--strict-aw3d-native-only", action="store_true")
    parser.add_argument("--otsu-max-pixels", type=float, default=1e8)
    parser.add_argument("--tile-scale", type=float, default=4)
    parser.add_argument("--report-json", type=Path, default=SCRIPT_DIR / "first_run_console.json")
    parser.add_argument("--map-html", type=Path, default=SCRIPT_DIR / "first_run_map.html")
    parser.add_argument("--export-qa", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--export-gmba-summary", action="store_true")
    parser.add_argument("--task-prefix", default="treeline_python")
    parser.add_argument("--drive-folder", default="Globaltreeline")
    parser.add_argument("--max-export-area-km2", type=float, default=25_000)
    parser.add_argument("--registry-dir", type=Path, default=PROJECT_ROOT / "outputs" / "tasks")
    return parser


def validate_bbox(bbox: Sequence[float]) -> Tuple[float, float, float, float]:
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox must satisfy -180 <= W < E <= 180 and -90 <= S < N <= 90")
    return west, south, east, north


def approximate_bbox_area_km2(bbox: Sequence[float]) -> float:
    west, south, east, north = bbox
    radius_km = 6371.0088
    return abs(
        radius_km**2
        * math.radians(east - west)
        * (math.sin(math.radians(north)) - math.sin(math.radians(south)))
    )


def missing_requirements(args: argparse.Namespace, export: bool = False) -> List[str]:
    missing = []
    if not args.project:
        missing.append("Earth Engine Cloud project (--project or EE_PROJECT)")
    if not args.gmba_asset:
        missing.append("GMBA asset (--gmba-asset or GMBA_ASSET)")
    if not args.sayre_asset:
        missing.append("Sayre high-mountain asset (--sayre-asset or SAYRE_ASSET)")
    if not args.chelsa_bio01:
        missing.append("CHELSA V2.1 BIO1 asset (--chelsa-bio01 or CHELSA_BIO01)")
    if export and args.otsu_threshold_c is None:
        missing.append("fixed Otsu threshold in degrees C (--otsu-threshold-c)")
    return missing


def resolved_plan(args: argparse.Namespace) -> Dict[str, object]:
    area_km2 = approximate_bbox_area_km2(args.bbox)
    missing = missing_requirements(args, export=args.export)
    return {
        "mode": "dry-run" if args.dry_run else "check" if args.check else "export",
        "project": args.project,
        "auth": "Google Application Default Credentials",
        "bbox": args.bbox,
        "bbox_area_km2_approx": area_km2,
        "aspect_mode": args.aspect_mode,
        "fine_grid": {"crs": FINE_CRS, "transform": FINE_TRANSFORM},
        "climate_grid": {"crs": CLIMATE_CRS, "transform": CLIMATE_TRANSFORM},
        "output": {
            "check_report": str(args.report_json),
            "check_map": str(args.map_html),
            "export_destination": f"Google Drive/{args.drive_folder}",
        },
        "temperature_transform": {
            "formula": "temperature_c = raw * scale + offset",
            "scale": args.temperature_scale,
            "offset": args.temperature_offset,
            "fixed_otsu_threshold_c": args.otsu_threshold_c,
        },
        "assets": {
            "gmba": args.gmba_asset,
            "sayre": args.sayre_asset,
            "forest_height_2000": FOREST_HEIGHT_2000,
            "forest_height_2020": FOREST_HEIGHT_2020,
            "chelsa_bio01": args.chelsa_bio01,
            "aw3d30": AW3D30,
            "alos_landforms": ALOS_LANDFORMS,
            "worldcover": WORLDCOVER,
        },
        "ready": not missing,
        "missing_requirements": missing,
        "export_guard": "task.start() is reachable only with --export and a fixed Otsu threshold",
    }


def initialize_with_adc(project: str) -> Dict[str, object]:
    credentials, detected_project = google.auth.default(scopes=list(ADC_SCOPES))
    credentials.refresh(Request())
    ee.Initialize(credentials=credentials, project=project)
    ee.data.setDefaultWorkloadTag(WORKLOAD_TAG)
    return {
        "credential_type": type(credentials).__name__,
        "valid": bool(credentials.valid),
        "detected_project": detected_project,
        "quota_project": getattr(credentials, "quota_project_id", None),
        "ee_project": project,
        "asset_root_count": len(ee.data.getAssetRoots()),
    }


def class_mask(image: ee.Image, values: Iterable[int]) -> ee.Image:
    masks = [image.eq(value) for value in values]
    result = masks[0]
    for mask in masks[1:]:
        result = result.Or(mask)
    return result


def select_gmba_intersecting_sayre(
    gmba: ee.FeatureCollection, sayre_high: ee.Image, region: ee.Geometry, args: argparse.Namespace
) -> ee.FeatureCollection:
    return sayre_high.rename("sayre_high").reduceRegions(
        collection=gmba.filterBounds(region),
        reducer=ee.Reducer.max().setOutputs(["sayre_hit"]),
        scale=sayre_high.projection().nominalScale(),
        tileScale=args.tile_scale,
    ).filter(ee.Filter.gt("sayre_hit", 0))


def quarter_degree_screen(sayre_high: ee.Image, args: argparse.Namespace) -> ee.Image:
    grid_projection = ee.Projection(FINE_CRS, QUARTER_DEGREE_TRANSFORM)
    sayre_projection = sayre_high.projection()
    high_fraction = (
        sayre_high.unmask(0)
        .reduceResolution(reducer=ee.Reducer.mean(), maxPixels=65535)
        .reproject(grid_projection)
        .rename("high_mountain_fraction")
    )
    worldcover_tree = ee.ImageCollection(WORLDCOVER).first().select("Map").eq(10).unmask(0)
    tree_at_sayre_scale = (
        worldcover_tree.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=4096)
        .reproject(sayre_projection)
    )
    tree_fraction = (
        tree_at_sayre_scale.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=65535)
        .reproject(grid_projection)
        .rename("tree_cover_fraction")
    )
    return high_fraction.gte(args.minimum_high_mountain_fraction).And(
        tree_fraction.lte(args.maximum_tree_cover_fraction)
    ).rename("valid_quarter_degree_cell")


def build_aw3d(region: ee.Geometry, strict_native_only: bool) -> ee.Image:
    def prepare(image: ee.Image) -> ee.Image:
        image = ee.Image(image)
        mask = image.select("MSK")
        valid = mask.eq(0) if strict_native_only else mask.neq(1)
        return (
            image.select("DSM").rename("elevation").updateMask(valid)
            .addBands(image.select("STK").rename("dem_stk").updateMask(valid))
            .addBands(mask.rename("dem_msk").updateMask(valid))
        )

    collection = ee.ImageCollection(AW3D30).filterBounds(region).map(prepare)
    projection = ee.Image(collection.first()).select("elevation").projection()
    return collection.mosaic().setDefaultProjection(projection).clip(region)


def fill_interior_holes(forest: ee.Image, region: ee.Geometry, args: argparse.Namespace) -> ee.Image:
    background = forest.Not().clip(region).rename("background")
    labels = background.selfMask().connectedComponents(
        ee.Kernel.plus(1), args.hole_max_size_pixels
    ).select("labels")
    inner = region.buffer(-args.hole_border_width_m)
    border_ring = region.difference(inner, 30)
    border = (
        ee.Image(0).byte()
        .paint(ee.FeatureCollection([ee.Feature(border_ring)]), 1)
        .setDefaultProjection(forest.projection())
        .rename("touch")
    )
    touches_border = border.addBands(labels).reduceConnectedComponents(
        reducer=ee.Reducer.max(), labelBand="labels", maxSize=args.hole_max_size_pixels
    ).select("touch")
    holes = labels.mask().And(touches_border.eq(0))
    return forest.Or(holes).rename("forest_filled").clip(region)


def clean_forest(canopy_asset: str, region: ee.Geometry, args: argparse.Namespace) -> ee.Image:
    forest = (
        ee.Image(canopy_asset).select([0]).gt(args.canopy_threshold_m)
        .unmask(0).clip(region).rename("forest_raw")
    )
    filled = fill_interior_holes(forest, region, args)
    count = filled.selfMask().connectedPixelCount(args.patch_count_cap, True)
    component_area_m2 = count.multiply(ee.Image.pixelArea())
    keep = component_area_m2.gte(args.minimum_forest_patch_ha * 10_000)
    return filled.updateMask(keep).unmask(0).rename("forest_clean").clip(region)


def forest_edges(forest: ee.Image, domain: ee.Image, args: argparse.Namespace) -> ee.Image:
    smoothed = forest.focalMedian(args.median_radius_pixels, "square", "pixels")
    laplacian = smoothed.toFloat().convolve(ee.Kernel.laplacian8())
    return laplacian.zeroCrossing().gt(0).And(domain).selfMask().rename("forest_edge")


def otsu_threshold_from_histogram(histogram: Mapping[str, object]) -> float:
    counts = [float(value) for value in histogram.get("histogram", [])]
    means = [float(value) for value in histogram.get("bucketMeans", [])]
    if len(counts) < 2 or len(counts) != len(means) or sum(counts) <= 0:
        raise ValueError(f"temperature histogram is empty or degenerate: {histogram}")
    total = sum(counts)
    weighted_sum = sum(mean * count for mean, count in zip(means, counts))
    global_mean = weighted_sum / total
    scores: List[float] = []
    for split in range(1, len(means)):
        a_count = sum(counts[:split])
        b_count = total - a_count
        if a_count <= 0 or b_count <= 0:
            scores.append(float("-inf"))
            continue
        a_mean = sum(mean * count for mean, count in zip(means[:split], counts[:split])) / a_count
        b_mean = (weighted_sum - a_count * a_mean) / b_count
        scores.append(
            a_count * (a_mean - global_mean) ** 2 + b_count * (b_mean - global_mean) ** 2
        )
    best_index = max(range(len(scores)), key=scores.__getitem__)
    if not math.isfinite(scores[best_index]):
        raise ValueError(f"temperature histogram has no valid Otsu split: {histogram}")
    return means[best_index]


def convert_raw_temperature(raw_value: float, scale: float, offset: float) -> float:
    return raw_value * scale + offset


def temperature_graph(
    calibration_candidates: ee.Image,
    batch_region: ee.Geometry,
    args: argparse.Namespace,
) -> Dict[str, object]:
    raw = ee.Image(args.chelsa_bio01).select([0]).rename("bio01_raw")
    temperature = raw.multiply(args.temperature_scale).add(args.temperature_offset).rename("temperature_c")
    candidate_on_climate_grid = (
        calibration_candidates.unmask(0)
        .reduceResolution(reducer=ee.Reducer.max(), maxPixels=4096)
        .reproject(temperature.projection())
        .gt(0)
    )
    qa_reducer = (
        ee.Reducer.minMax()
        .combine(ee.Reducer.mean(), sharedInputs=True)
        .combine(ee.Reducer.count(), sharedInputs=True)
    )
    range_qa = raw.addBands(temperature).reduceRegion(
        reducer=qa_reducer,
        geometry=batch_region,
        scale=temperature.projection().nominalScale(),
        maxPixels=args.otsu_max_pixels,
        tileScale=args.tile_scale,
    )
    histogram = raw.updateMask(candidate_on_climate_grid).reduceRegion(
        reducer=ee.Reducer.histogram(maxBuckets=256, minBucketWidth=1),
        geometry=batch_region,
        scale=raw.projection().nominalScale(),
        maxPixels=args.otsu_max_pixels,
        tileScale=args.tile_scale,
    ).get("bio01_raw")
    return {
        "raw": raw,
        "temperature": temperature,
        "range_qa": range_qa,
        "histogram": histogram,
    }


def assess_bio1_conversion(range_info: Mapping[str, object], scale: float, offset: float) -> Dict[str, object]:
    raw_min = range_info.get("bio01_raw_min")
    raw_max = range_info.get("bio01_raw_max")
    raw_mean = range_info.get("bio01_raw_mean")
    if not all(isinstance(value, (int, float)) for value in (raw_min, raw_max, raw_mean)):
        verdict = "undetermined: no valid BIO1 pixels in the tracer"
    elif 2000 <= float(raw_mean) <= 3500:
        if math.isclose(scale, 0.1) and math.isclose(offset, -273.15):
            verdict = "conversion confirmed by asset range: deci-Kelvin to degrees Celsius"
        else:
            verdict = "conversion required: values match deci-Kelvin; use raw * 0.1 - 273.15"
    elif scale != 1 or offset != 0:
        verdict = "explicit conversion configured; verify the uploaded asset ingestion settings"
    elif -100 <= float(raw_min) <= 80 and -100 <= float(raw_max) <= 80:
        verdict = "no conversion indicated: uploaded values are already physically plausible degrees Celsius"
    elif 150 <= float(raw_mean) <= 350:
        verdict = "conversion likely required: values resemble Kelvin; test offset -273.15"
    elif abs(float(raw_min)) > 100 or abs(float(raw_max)) > 100:
        verdict = "conversion likely required: values resemble scaled integer temperature"
    else:
        verdict = "undetermined: inspect asset ingestion metadata and a wider representative range"
    return {
        "official_chelsa_v2_1_bio01_unit": "degrees Celsius",
        "configured_scale": scale,
        "configured_offset": offset,
        "verdict": verdict,
        "otsu_note": (
            "Otsu is run on the unchanged integer values with a one-raw-unit minimum bucket width. "
            "Only the resulting raw threshold is converted to degrees Celsius."
        ),
    }


def one_sided_t_critical_image(df: ee.Image) -> ee.Image:
    critical = ee.Image.constant(ONE_SIDED_T_CRITICAL_95[0][1])
    for minimum_df, value in ONE_SIDED_T_CRITICAL_95[1:]:
        critical = critical.where(df.gte(minimum_df), value)
    return critical


def upper_edge_test(
    forest: ee.Image,
    dem: ee.Image,
    candidates: ee.Image,
    population_mask: ee.Image,
    args: argparse.Namespace,
) -> ee.Image:
    kernel = ee.Kernel.square(args.window_radius_m, "meters", False)
    reducer = (
        ee.Reducer.mean()
        .combine(ee.Reducer.variance(), sharedInputs=True)
        .combine(ee.Reducer.count(), sharedInputs=True)
    )
    forest_population = forest.eq(1).And(population_mask)
    nonforest_population = forest.eq(0).And(population_mask)
    forest_stats = dem.updateMask(forest_population).rename("elevation").reduceNeighborhood(
        reducer=reducer, kernel=kernel, skipMasked=False
    )
    nonforest_stats = dem.updateMask(nonforest_population).rename("elevation").reduceNeighborhood(
        reducer=reducer, kernel=kernel, skipMasked=False
    )
    nf = forest_stats.select("elevation_count")
    nn = nonforest_stats.select("elevation_count")
    mf = forest_stats.select("elevation_mean")
    mn = nonforest_stats.select("elevation_mean")
    vf = forest_stats.select("elevation_variance")
    vn = nonforest_stats.select("elevation_variance")
    if args.t_test_variance == "pooled":
        pooled_variance = vf.multiply(nf.subtract(1)).add(vn.multiply(nn.subtract(1))).divide(
            nf.add(nn).subtract(2)
        )
        standard_error = pooled_variance.multiply(
            ee.Image(1).divide(nf).add(ee.Image(1).divide(nn))
        ).sqrt()
        degrees_of_freedom = nf.add(nn).subtract(2)
    else:
        vf_mean = vf.divide(nf)
        vn_mean = vn.divide(nn)
        se2 = vf_mean.add(vn_mean)
        standard_error = se2.sqrt()
        degrees_of_freedom = se2.pow(2).divide(
            vf_mean.pow(2).divide(nf.subtract(1)).add(vn_mean.pow(2).divide(nn.subtract(1)))
        )
    statistic = mf.subtract(mn).divide(standard_error)
    enough = nf.gte(args.minimum_samples_per_group).And(
        nn.gte(args.minimum_samples_per_group)
    ).And(standard_error.gt(0))
    return candidates.And(enough).And(
        statistic.lt(one_sided_t_critical_image(degrees_of_freedom))
    ).selfMask().rename("upper_edge")


def aspect_groups(dem: ee.Image, args: argparse.Namespace) -> Dict[str, ee.Image]:
    aspect = ee.Terrain.aspect(dem).rename("aspect_deg")
    slope = ee.Terrain.slope(dem).rename("slope_deg")
    half_width = args.aspect_half_width_deg
    north = aspect.gte(360 - half_width).Or(aspect.lt(half_width))
    south = aspect.gte(180 - half_width).And(aspect.lt(180 + half_width))
    latitude = ee.Image.pixelLonLat().select("latitude")
    north_hemisphere = latitude.gt(args.equator_buffer_deg)
    south_hemisphere = latitude.lt(-args.equator_buffer_deg)
    valid_slope = slope.gte(args.minimum_slope_deg)
    polar = north_hemisphere.And(north).Or(south_hemisphere.And(south)).And(valid_slope).rename(
        "polar_facing"
    )
    equator = north_hemisphere.And(south).Or(south_hemisphere.And(north)).And(valid_slope).rename(
        "equator_facing"
    )
    aspect_class = ee.Image.constant(0).byte().where(polar, 1).where(equator, 2).rename("aspect_class")
    return {"aspect": aspect, "slope": slope, "polar": polar, "equator": equator, "class": aspect_class}


def extract_treeline(
    canopy_asset: str,
    year: int,
    group_name: str,
    group_mask: ee.Image,
    domain: ee.Image,
    dem: ee.Image,
    nonvalley: ee.Image,
    cold_mask: ee.Image,
    processing_region: ee.Geometry,
    args: argparse.Namespace,
    precomputed_forest: Optional[ee.Image] = None,
) -> Dict[str, ee.Image]:
    forest = (
        precomputed_forest
        if precomputed_forest is not None
        else clean_forest(canopy_asset, processing_region, args)
    )
    edges = forest_edges(forest, group_mask.And(domain), args)
    candidates = edges.And(nonvalley).And(cold_mask).selfMask().rename(f"candidate_{group_name}_{year}")
    upper = upper_edge_test(forest, dem, candidates, group_mask, args).rename(f"upper_{group_name}_{year}")
    elevation = dem.updateMask(upper).rename(f"treeline_{group_name}_{year}_m").toFloat()
    return {"forest": forest, "edges": edges, "candidates": candidates, "upper": upper, "elevation": elevation}


def aggregate_to_climate_grid(image: ee.Image) -> ee.Image:
    return image.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=4096).reproject(
        ee.Projection(CLIMATE_CRS, CLIMATE_TRANSFORM)
    )


def build_common(args: argparse.Namespace, batch_region: ee.Geometry, processing_region: ee.Geometry) -> Dict[str, object]:
    sayre = ee.Image(args.sayre_asset).select([0])
    sayre_high = class_mask(sayre, SAYRE_CLASSES).unmask(0).rename("sayre_high")
    gmba = ee.FeatureCollection(args.gmba_asset)
    selected_gmba = select_gmba_intersecting_sayre(gmba, sayre_high, processing_region, args)
    gmba_mask = (
        ee.Image(0).byte().paint(selected_gmba, 1).setDefaultProjection(sayre_high.projection())
        .rename("gmba_selected").unmask(0)
    )
    grid_screen = quarter_degree_screen(sayre_high, args) if args.apply_quarter_degree_screen else ee.Image.constant(1)
    domain = sayre_high.And(gmba_mask).And(grid_screen).selfMask().rename("analysis_domain")
    aw3d = build_aw3d(processing_region, args.strict_aw3d_native_only)
    dem = aw3d.select("elevation").toFloat()
    landforms = ee.Image(ALOS_LANDFORMS).select("constant")
    nonvalley = class_mask(landforms, VALLEY_CLASSES).Not().rename("non_valley")
    forest2020 = clean_forest(FOREST_HEIGHT_2020, processing_region, args)
    preliminary_edges2020 = forest_edges(forest2020, domain, args).And(nonvalley)
    return {
        "gmba": gmba,
        "selected_gmba": selected_gmba,
        "domain": domain,
        "aw3d": aw3d,
        "dem": dem,
        "nonvalley": nonvalley,
        "forest2020": forest2020,
        "preliminary_edges2020": preliminary_edges2020,
    }


def build_products(
    args: argparse.Namespace,
    common: Mapping[str, object],
    processing_region: ee.Geometry,
    threshold_c: float,
    temperature: ee.Image,
) -> Dict[str, object]:
    domain = ee.Image(common["domain"])
    dem = ee.Image(common["dem"])
    nonvalley = ee.Image(common["nonvalley"])
    forest2020 = ee.Image(common["forest2020"])
    cold_mask = temperature.lte(threshold_c).rename("cold_zone")
    if args.aspect_mode == "none":
        group_mask = ee.Image.constant(1).clip(processing_region)
        result2000 = extract_treeline(
            FOREST_HEIGHT_2000, 2000, "all", group_mask, domain, dem, nonvalley,
            cold_mask, processing_region, args
        )
        result2020 = extract_treeline(
            FOREST_HEIGHT_2020, 2020, "all", group_mask, domain, dem, nonvalley,
            cold_mask, processing_region, args, forest2020
        )
        elevation2000_1km = aggregate_to_climate_grid(result2000["elevation"]).rename("treeline_2000_mean_m")
        elevation2020_1km = aggregate_to_climate_grid(result2020["elevation"]).rename("treeline_2020_mean_m")
        shift = elevation2020_1km.subtract(elevation2000_1km).divide(20).rename(
            "shift_2000_2020_m_per_year"
        ).toFloat()
        treeline30m = result2000["elevation"].addBands(result2020["elevation"]).toFloat()
        treeline1km = elevation2000_1km.addBands(elevation2020_1km).addBands(shift).toFloat()
        qa30m = (
            domain.unmask(0).rename("analysis_domain")
            .addBands(result2000["forest"].rename("forest_2000"))
            .addBands(result2020["forest"].rename("forest_2020"))
            .addBands(result2000["edges"].unmask(0).rename("edge_2000"))
            .addBands(result2020["edges"].unmask(0).rename("edge_2020"))
            .addBands(result2000["upper"].unmask(0).rename("upper_2000"))
            .addBands(result2020["upper"].unmask(0).rename("upper_2020"))
            .addBands(cold_mask.unmask(0))
            .addBands(nonvalley.unmask(0))
            .addBands(ee.Image(common["aw3d"]).select("dem_msk").unmask(255))
            .addBands(ee.Image(common["aw3d"]).select("dem_stk").unmask(0))
            .toFloat()
        )
        return {
            "treeline30m": treeline30m,
            "treeline1km": treeline1km,
            "qa30m": qa30m,
            "map_layers": [
                ("Treeline elevation 2000", result2000["elevation"], 30, True),
                ("Treeline elevation 2020", result2020["elevation"], 30, True),
                ("Treeline shift m/yr", shift, 1000, False),
                ("AW3D MSK", ee.Image(common["aw3d"]).select("dem_msk"), 30, False),
            ],
        }

    groups = aspect_groups(dem, args)
    polar2000 = extract_treeline(
        FOREST_HEIGHT_2000, 2000, "polar", groups["polar"], domain, dem, nonvalley,
        cold_mask, processing_region, args
    )
    equator2000 = extract_treeline(
        FOREST_HEIGHT_2000, 2000, "equator", groups["equator"], domain, dem, nonvalley,
        cold_mask, processing_region, args, polar2000["forest"]
    )
    polar2020 = extract_treeline(
        FOREST_HEIGHT_2020, 2020, "polar", groups["polar"], domain, dem, nonvalley,
        cold_mask, processing_region, args, forest2020
    )
    equator2020 = extract_treeline(
        FOREST_HEIGHT_2020, 2020, "equator", groups["equator"], domain, dem, nonvalley,
        cold_mask, processing_region, args, forest2020
    )
    polar2000_1km = aggregate_to_climate_grid(polar2000["elevation"]).rename("polar_2000_mean_m")
    equator2000_1km = aggregate_to_climate_grid(equator2000["elevation"]).rename("equator_2000_mean_m")
    polar2020_1km = aggregate_to_climate_grid(polar2020["elevation"]).rename("polar_2020_mean_m")
    equator2020_1km = aggregate_to_climate_grid(equator2020["elevation"]).rename("equator_2020_mean_m")
    polar_shift = polar2020_1km.subtract(polar2000_1km).divide(20).rename("polar_shift_m_per_year")
    equator_shift = equator2020_1km.subtract(equator2000_1km).divide(20).rename("equator_shift_m_per_year")
    treeline30m = (
        polar2000["elevation"].addBands(equator2000["elevation"])
        .addBands(polar2020["elevation"]).addBands(equator2020["elevation"]).toFloat()
    )
    treeline1km = (
        polar2000_1km.addBands(equator2000_1km).addBands(polar2020_1km)
        .addBands(equator2020_1km).addBands(polar_shift).addBands(equator_shift).toFloat()
    )
    qa30m = (
        domain.unmask(0).rename("analysis_domain")
        .addBands(groups["class"].unmask(0))
        .addBands(groups["aspect"].unmask(-1))
        .addBands(groups["slope"].unmask(-1))
        .addBands(polar2000["forest"].rename("forest_2000"))
        .addBands(forest2020.rename("forest_2020"))
        .addBands(polar2000["upper"].unmask(0))
        .addBands(equator2000["upper"].unmask(0))
        .addBands(polar2020["upper"].unmask(0))
        .addBands(equator2020["upper"].unmask(0))
        .addBands(cold_mask.unmask(0))
        .addBands(ee.Image(common["aw3d"]).select("dem_msk").unmask(255))
        .addBands(ee.Image(common["aw3d"]).select("dem_stk").unmask(0))
        .toFloat()
    )
    return {
        "treeline30m": treeline30m,
        "treeline1km": treeline1km,
        "qa30m": qa30m,
        "map_layers": [
            ("Aspect groups", groups["class"].updateMask(groups["class"].gt(0)), 30, True),
            ("Polar-facing treeline 2020", polar2020["elevation"], 30, True),
            ("Equator-facing treeline 2020", equator2020["elevation"], 30, True),
            ("Polar-facing shift m/yr", polar_shift, 1000, False),
            ("Equator-facing shift m/yr", equator_shift, 1000, False),
        ],
    }


def asset_summary(asset_id: str) -> Dict[str, object]:
    info = ee.data.getAsset(asset_id)
    bands = []
    for band in info.get("bands", []):
        grid = band.get("grid", {})
        bands.append(
            {
                "id": band.get("id"),
                "data_type": band.get("dataType"),
                "crs": grid.get("crsCode"),
                "affine_transform": grid.get("affineTransform"),
            }
        )
    return {"id": asset_id, "type": info.get("type"), "bands": bands}


def image_count(image: ee.Image, region: ee.Geometry, scale: float, args: argparse.Namespace) -> Dict[str, object]:
    return image.reduceRegion(
        reducer=ee.Reducer.count(), geometry=region, scale=scale,
        maxPixels=args.otsu_max_pixels, tileScale=args.tile_scale
    ).getInfo()


def create_map(
    args: argparse.Namespace,
    batch_region: ee.Geometry,
    common: Mapping[str, object],
    products: Mapping[str, object],
) -> List[Dict[str, object]]:
    west, south, east, north = args.bbox
    map_object = geemap.Map(center=[(south + north) / 2, (west + east) / 2], zoom=11)
    layer_report: List[Dict[str, object]] = []
    map_object.addLayer(common["selected_gmba"], {"color": "7f7f7f"}, "Selected GMBA", False)
    layer_report.append(
        {"name": "Selected GMBA", "kind": "FeatureCollection", "shown": False, "feature_count": None}
    )
    map_object.addLayer(common["domain"], {"palette": ["f1c40f"]}, "Analysis domain", False)
    layer_report.append(
        {
            "name": "Analysis domain",
            "kind": "Image",
            "shown": False,
            "pixel_count": image_count(ee.Image(common["domain"]), batch_region, 30, args),
        }
    )
    palettes = {
        "Treeline elevation 2000": {"min": 0, "max": 5000, "palette": ["2c7bb6", "ffffbf", "d7191c"]},
        "Treeline elevation 2020": {"min": 0, "max": 5000, "palette": ["313695", "ffffbf", "a50026"]},
        "Treeline shift m/yr": {"min": -5, "max": 5, "palette": ["2166ac", "f7f7f7", "b2182b"]},
        "Aspect groups": {"min": 1, "max": 2, "palette": ["2166ac", "b2182b"]},
        "Polar-facing treeline 2020": {"min": 0, "max": 5000, "palette": ["313695", "ffffbf", "a50026"]},
        "Equator-facing treeline 2020": {"min": 0, "max": 5000, "palette": ["2c7bb6", "ffffbf", "d7191c"]},
        "Polar-facing shift m/yr": {"min": -5, "max": 5, "palette": ["2166ac", "f7f7f7", "b2182b"]},
        "Equator-facing shift m/yr": {"min": -5, "max": 5, "palette": ["2166ac", "f7f7f7", "b2182b"]},
        "AW3D MSK": {"min": 0, "max": 52},
    }
    for name, image, scale, shown in products["map_layers"]:
        map_object.addLayer(image, palettes[name], name, shown)
        layer_report.append(
            {
                "name": name,
                "kind": "Image",
                "shown": shown,
                "pixel_count": image_count(ee.Image(image), batch_region, scale, args),
            }
        )
    args.map_html.parent.mkdir(parents=True, exist_ok=True)
    map_object.to_html(filename=str(args.map_html), title="Global treeline first tracer")
    return layer_report


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_check(args: argparse.Namespace) -> Dict[str, object]:
    auth = initialize_with_adc(args.project)
    west, south, east, north = validate_bbox(args.bbox)
    batch_region = ee.Geometry.Rectangle([west, south, east, north], proj=None, geodesic=False)
    processing_region = batch_region.buffer(args.context_buffer_m)
    assets = {
        name: asset_summary(asset_id)
        for name, asset_id in {
            "gmba": args.gmba_asset,
            "sayre": args.sayre_asset,
            "forest_height_2000": FOREST_HEIGHT_2000,
            "forest_height_2020": FOREST_HEIGHT_2020,
            "chelsa_bio01": args.chelsa_bio01,
            "aw3d30": AW3D30,
            "alos_landforms": ALOS_LANDFORMS,
            "worldcover": WORLDCOVER,
        }.items()
    }
    common = build_common(args, batch_region, processing_region)
    gmba_hit_count = ee.FeatureCollection(common["selected_gmba"]).size().getInfo()
    gmba_property_names = ee.Feature(ee.FeatureCollection(common["gmba"]).first()).propertyNames().getInfo()
    temperature_info = temperature_graph(
        ee.Image(common["preliminary_edges2020"]), batch_region, args
    )
    temperature_range = ee.Dictionary(temperature_info["range_qa"]).getInfo()
    if args.otsu_threshold_c is None:
        histogram = temperature_info["histogram"].getInfo()
        if not isinstance(histogram, dict):
            raise ValueError(f"temperature histogram is empty or degenerate: {histogram}")
        threshold_raw = otsu_threshold_from_histogram(histogram)
        threshold_c = convert_raw_temperature(
            threshold_raw, args.temperature_scale, args.temperature_offset
        )
        threshold_source = "Otsu on raw integer BIO1; threshold converted to degrees Celsius"
    else:
        histogram = None
        threshold_raw = None
        threshold_c = args.otsu_threshold_c
        threshold_source = "fixed CLI value"
    products = build_products(
        args, common, processing_region, threshold_c, ee.Image(temperature_info["temperature"])
    )
    layer_report = create_map(args, batch_region, common, products)
    layer_report[0]["feature_count"] = gmba_hit_count
    report = {
        "status": "first-run-check-passed",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "resolved_plan": resolved_plan(args),
        "authentication": auth,
        "assets": assets,
        "gmba": {
            "features_intersecting_sayre_31_32_in_buffered_tile": gmba_hit_count,
            "first_feature_property_names": gmba_property_names,
            "overlap_warning": "GMBA Standard hierarchy levels overlap; hit count is not an independent mountain count.",
        },
        "chelsa_bio01_range": temperature_range,
        "bio1_conversion_assessment": assess_bio1_conversion(
            temperature_range, args.temperature_scale, args.temperature_offset
        ),
        "otsu": {
            "threshold_raw": threshold_raw,
            "threshold_c": threshold_c,
            "source": threshold_source,
            "histogram_bucket_count": len(histogram.get("histogram", [])) if histogram else None,
            "candidate_sample_count": sum(histogram.get("histogram", [])) if histogram else None,
        },
        "layers": layer_report,
        "map_html": str(args.map_html),
        "exports_started": False,
    }
    write_json_atomic(args.report_json, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def start_exports(args: argparse.Namespace) -> Path:
    auth = initialize_with_adc(args.project)
    west, south, east, north = validate_bbox(args.bbox)
    batch_region = ee.Geometry.Rectangle([west, south, east, north], proj=None, geodesic=False)
    area_km2 = ee.Number(batch_region.area(maxError=1000)).divide(1_000_000).getInfo()
    if area_km2 > args.max_export_area_km2:
        raise ValueError(
            f"ROI area {area_km2:.1f} km2 exceeds {args.max_export_area_km2:.1f}; use explicit smaller tiles"
        )
    processing_region = batch_region.buffer(args.context_buffer_m)
    common = build_common(args, batch_region, processing_region)
    temperature = ee.Image(args.chelsa_bio01).select([0]).multiply(args.temperature_scale).add(
        args.temperature_offset
    ).rename("temperature_c")
    products = build_products(args, common, processing_region, args.otsu_threshold_c, temperature)
    specs = [
        ("30m", ee.Image(products["treeline30m"]).clip(batch_region).toFloat(), FINE_TRANSFORM),
        ("1km", ee.Image(products["treeline1km"]).clip(batch_region).toFloat(), CLIMATE_TRANSFORM),
    ]
    if args.export_qa:
        specs.append(("qa30m", ee.Image(products["qa30m"]).clip(batch_region).toFloat(), FINE_TRANSFORM))
    records = []
    for suffix, _, transform in specs:
        description = f"{args.task_prefix}_{args.aspect_mode}_{suffix}".replace("-", "_")
        records.append(
            {
                "description": description,
                "task_id": None,
                "state": "PLANNED",
                "destination": f"Google Drive/{args.drive_folder}",
                "file_name_prefix": description,
                "crs_transform": transform,
            }
        )
    if args.export_gmba_summary:
        description = f"{args.task_prefix}_{args.aspect_mode}_gmba_summary".replace("-", "_")
        records.append(
            {
                "description": description,
                "task_id": None,
                "state": "PLANNED",
                "destination": f"Google Drive/{args.drive_folder}",
                "file_name_prefix": description,
                "format": "CSV",
            }
        )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    registry = args.registry_dir / f"{timestamp}-{args.task_prefix}.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": args.project,
        "authentication": auth,
        "bbox": args.bbox,
        "area_km2": area_km2,
        "otsu_threshold_c": args.otsu_threshold_c,
        "tasks": records,
    }
    write_json_atomic(registry, payload)
    for index, (suffix, image, transform) in enumerate(specs):
        try:
            task = ee.batch.Export.image.toDrive(
                image=image,
                description=records[index]["description"],
                fileNamePrefix=records[index]["file_name_prefix"],
                folder=args.drive_folder,
                region=batch_region,
                crs=FINE_CRS if suffix != "1km" else CLIMATE_CRS,
                crsTransform=transform,
                maxPixels=1e13,
                fileFormat="GeoTIFF",
                formatOptions={"cloudOptimized": True},
            )
            records[index]["task_id"] = task.id
            records[index]["state"] = "CREATED"
            write_json_atomic(registry, payload)
            task.start()
            records[index]["state"] = "SUBMITTED"
            write_json_atomic(registry, payload)
        except Exception as error:
            records[index]["state"] = "FAILED_TO_START"
            records[index]["error"] = f"{type(error).__name__}: {error}"
            write_json_atomic(registry, payload)
            raise
    if args.export_gmba_summary:
        table_index = len(specs)

        def intersect_batch(feature: ee.Feature) -> ee.Feature:
            feature = ee.Feature(feature)
            return feature.intersection(batch_region, 100).set(
                "gmba_index", feature.get("system:index")
            )

        batch_mountains = ee.FeatureCollection(common["selected_gmba"]).map(intersect_batch)
        gmba_summary = ee.Image(products["treeline1km"]).reduceRegions(
            collection=batch_mountains,
            reducer=ee.Reducer.mean(),
            scale=1000,
            crs=CLIMATE_CRS,
            tileScale=args.tile_scale,
        )
        try:
            task = ee.batch.Export.table.toDrive(
                collection=gmba_summary,
                description=records[table_index]["description"],
                fileNamePrefix=records[table_index]["file_name_prefix"],
                folder=args.drive_folder,
                fileFormat="CSV",
            )
            records[table_index]["task_id"] = task.id
            records[table_index]["state"] = "CREATED"
            write_json_atomic(registry, payload)
            task.start()
            records[table_index]["state"] = "SUBMITTED"
            write_json_atomic(registry, payload)
        except Exception as error:
            records[table_index]["state"] = "FAILED_TO_START"
            records[table_index]["error"] = f"{type(error).__name__}: {error}"
            write_json_atomic(registry, payload)
            raise
    print(json.dumps({"registry": str(registry), "tasks": records}, ensure_ascii=False, indent=2))
    return registry


def monitor_once(project: str, registry_path: Path) -> None:
    initialize_with_adc(project)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    task_ids = [task["task_id"] for task in registry["tasks"] if task.get("task_id")]
    states = ee.data.getTaskStatus(task_ids)
    counts: Dict[str, int] = {}
    details = []
    descriptions = {task["task_id"]: task["description"] for task in registry["tasks"]}
    for state in states:
        status = state.get("state", "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
        details.append(
            {
                "task_id": state.get("id"),
                "description": descriptions.get(state.get("id")),
                "state": status,
                "error_message": state.get("error_message"),
            }
        )
    print(json.dumps({"counts": counts, "tasks": details}, ensure_ascii=False, indent=2))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.monitor_once:
        if not args.project:
            parser.error("--monitor-once requires --project or EE_PROJECT")
        monitor_once(args.project, Path(args.monitor_once))
        return 0
    try:
        validate_bbox(args.bbox)
    except ValueError as error:
        parser.error(str(error))
    if not 0 < args.aspect_half_width_deg <= 90:
        parser.error("--aspect-half-width-deg must be in (0, 90]")
    plan = resolved_plan(args)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    missing = missing_requirements(args, export=args.export)
    if missing:
        parser.error("missing requirements: " + "; ".join(missing))
    if args.check:
        run_check(args)
    elif args.export:
        start_exports(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
