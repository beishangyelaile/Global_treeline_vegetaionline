"""Reproduce observed alpine treelines and their 2000--2020 shift in GEE.

Exports are opt-in. A plain run or ``--dry-run`` makes no Earth Engine call.
The paper-faithful export path requires a user-supplied CHELSA V2.1 annual mean
temperature asset because no verified public GEE asset was found.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# This file is named code.py. Remove its directory before importing geemap so
# IPython can import Python's standard-library ``code`` module.
SCRIPT_DIR = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0]).resolve() == SCRIPT_DIR:
    sys.path.pop(0)

import ee
import geemap


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FOREST_HEIGHT_2000 = "projects/glad/GLCLU2020/Forest_height_2000"
FOREST_HEIGHT_2020 = "projects/glad/GLCLU2020/Forest_height_2020"
NASADEM = "NASA/NASADEM_HGT/001"
WORLDCOVER = "ESA/WorldCover/v100"
LANDFORMS = "CSP/ERGo/1_0/Global/SRTM_landforms"
HIGH_MOUNTAIN_CLASSES = (31, 32)
VALLEY_CLASSES = (41, 42)
GRID_TRANSFORM = [0.25, 0, -180, 0, -0.25, 90]
WORKLOAD_TAG = "global-treeline-repro"
T_CRITICAL_95_TWO_SIDED = (
    (1, -12.706),
    (2, -4.303),
    (3, -3.182),
    (4, -2.776),
    (5, -2.571),
    (6, -2.447),
    (7, -2.365),
    (8, -2.306),
    (9, -2.262),
    (10, -2.228),
    (12, -2.179),
    (15, -2.131),
    (20, -2.086),
    (30, -2.042),
    (40, -2.021),
    (60, -2.000),
    (120, -1.980),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Print resolved parameters; no EE call")
    mode.add_argument("--check", action="store_true", help="Run a lightweight online tracer check")
    mode.add_argument("--export", action="store_true", help="Start Google Drive export tasks")
    mode.add_argument("--monitor-once", metavar="REGISTRY", help="Check registered export tasks once")

    parser.add_argument("--project", default=os.environ.get("EE_PROJECT"))
    parser.add_argument("--mountain-asset", default=os.environ.get("HIGH_MOUNTAIN_ASSET"))
    parser.add_argument("--temperature-asset", default=os.environ.get("TREELINE_TEMPERATURE_ASSET"))
    parser.add_argument("--temperature-band", default=os.environ.get("TREELINE_TEMPERATURE_BAND"))
    parser.add_argument("--temperature-scale", type=float)
    parser.add_argument("--temperature-offset", type=float)
    parser.add_argument("--temperature-threshold-c", type=float)
    parser.add_argument("--allow-missing-temperature", action="store_true")
    parser.add_argument("--bbox", type=float, nargs=4, metavar=("W", "S", "E", "N"))
    parser.add_argument("--canopy-threshold-m", type=float, default=3.0)
    parser.add_argument("--minimum-patch-ha", type=float, default=0.5)
    parser.add_argument("--hole-radius-m", type=float, default=30.0)
    parser.add_argument("--accept-hole-filling-approximation", action="store_true")
    parser.add_argument("--median-radius-pixels", type=float, default=1.0)
    parser.add_argument("--window-radius-m", type=float, default=150.0)
    parser.add_argument("--minimum-window-samples", type=int, default=5)
    parser.add_argument("--scale-30m", type=float, default=30.0)
    parser.add_argument("--scale-1km", type=float, default=1000.0)
    parser.add_argument("--crs", default="EPSG:4326")
    parser.add_argument("--max-export-area-km2", type=float, default=25_000.0)
    parser.add_argument("--task-prefix", default="global_treeline")
    parser.add_argument("--registry-dir", type=Path, default=PROJECT_ROOT / "outputs" / "tasks")
    return parser


def missing_requirements(args: argparse.Namespace, export: bool = False) -> List[str]:
    missing = []
    if not args.project:
        missing.append("Earth Engine Cloud project (--project or EE_PROJECT)")
    if not args.mountain_asset:
        missing.append("high-mountain asset (--mountain-asset or HIGH_MOUNTAIN_ASSET)")
    if not args.bbox:
        missing.append("export/check ROI (--bbox W S E N)")
    if not args.temperature_asset:
        missing.append("verified CHELSA V2.1 annual-mean-temperature asset")
    if args.temperature_asset and not args.temperature_band:
        missing.append("temperature band (--temperature-band)")
    if args.temperature_asset and args.temperature_scale is None:
        missing.append("temperature scale factor (--temperature-scale)")
    if args.temperature_asset and args.temperature_offset is None:
        missing.append("temperature offset (--temperature-offset)")
    if export and args.temperature_asset and args.temperature_threshold_c is None:
        missing.append("fixed Otsu threshold for consistent tiled exports (--temperature-threshold-c)")
    if export and not args.accept_hole_filling_approximation:
        missing.append("explicit acceptance of the documented closing approximation")
    if not export and args.allow_missing_temperature:
        missing = [item for item in missing if not item.startswith("temperature") and "CHELSA" not in item]
    return missing


def resolved_plan(args: argparse.Namespace) -> Dict[str, object]:
    missing = missing_requirements(args, export=True)
    area_km2 = approximate_bbox_area_km2(args.bbox) if args.bbox else None
    pixels_30m = area_km2 * 1_000_000 / (args.scale_30m**2) if area_km2 is not None else None
    return {
        "mode": "dry-run" if args.dry_run else "check" if args.check else "export",
        "project": args.project,
        "bbox": args.bbox,
        "destination": "Google Drive default destination",
        "compute_risk": {
            "approximate_bbox_area_km2": area_km2,
            "approximate_30m_pixels_before_masking": pixels_30m,
            "level": "high" if area_km2 and area_km2 > args.max_export_area_km2 else "bounded",
            "requires_explicit_smaller_tiles": bool(area_km2 and area_km2 > args.max_export_area_km2),
        },
        "assets": {
            "high_mountain": args.mountain_asset,
            "forest_height_2000": FOREST_HEIGHT_2000,
            "forest_height_2020": FOREST_HEIGHT_2020,
            "dem": NASADEM,
            "worldcover": WORLDCOVER,
            "landforms": LANDFORMS,
            "annual_mean_temperature": args.temperature_asset,
            "temperature_band": args.temperature_band,
        },
        "temperature_transform": {
            "physical_celsius": "raw * scale + offset",
            "scale": args.temperature_scale,
            "offset": args.temperature_offset,
            "otsu_override_c": args.temperature_threshold_c,
        },
        "parameters": {
            "canopy_threshold_m": args.canopy_threshold_m,
            "minimum_patch_ha": args.minimum_patch_ha,
            "hole_radius_m": args.hole_radius_m,
            "median_radius_pixels": args.median_radius_pixels,
            "window_radius_m": args.window_radius_m,
            "welch_test": "two-sided alpha=0.05 with Welch-Satterthwaite df",
            "minimum_window_samples_per_group": args.minimum_window_samples,
            "fine_scale_m": args.scale_30m,
            "aggregate_scale_m": args.scale_1km,
            "crs": args.crs,
            "max_export_area_km2": args.max_export_area_km2,
        },
        "ready_for_export": not missing,
        "missing_requirements": missing,
        "known_method_deviations": [
            "Morphological closing is not equivalent to topology-preserving hole filling and needs explicit acceptance.",
            "Paper does not state Otsu spatial scope; checks can derive it, but exports require one fixed threshold.",
            "NASADEM catalog coverage is 56 S to 60 N, so it cannot alone reproduce polar treelines.",
        ],
    }


def validate_bbox(bbox: Sequence[float]) -> Tuple[float, float, float, float]:
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox must satisfy -180 <= W < E <= 180 and -90 <= S < N <= 90")
    return west, south, east, north


def approximate_bbox_area_km2(bbox: Sequence[float]) -> float:
    west, south, east, north = bbox
    radius_km = 6371.0088
    longitude_span = math.radians(east - west)
    latitude_factor = math.sin(math.radians(north)) - math.sin(math.radians(south))
    return abs(radius_km**2 * longitude_span * latitude_factor)


def class_mask(image: ee.Image, values: Iterable[int]) -> ee.Image:
    masks = [image.eq(value) for value in values]
    result = masks[0]
    for mask in masks[1:]:
        result = result.Or(mask)
    return result


def valid_quarter_degree_domain(mountain_asset: str) -> ee.Image:
    grid_projection = ee.Projection("EPSG:4326", GRID_TRANSFORM)
    mountain = ee.Image(mountain_asset).select("b1")
    high_mountain = class_mask(mountain, HIGH_MOUNTAIN_CLASSES).unmask(0).rename("high_mountain")

    worldcover_tree = ee.ImageCollection(WORLDCOVER).first().select("Map").eq(10).unmask(0)
    tree_at_mountain_scale = (
        worldcover_tree.reduceResolution(ee.Reducer.mean(), maxPixels=1024)
        .reproject(mountain.projection())
        .rename("tree_fraction_250m")
    )
    high_fraction = (
        high_mountain.reduceResolution(ee.Reducer.mean(), maxPixels=65535)
        .reproject(grid_projection)
        .rename("high_fraction")
    )
    tree_fraction = (
        tree_at_mountain_scale.reduceResolution(ee.Reducer.mean(), maxPixels=65535)
        .reproject(grid_projection)
        .rename("tree_fraction")
    )
    valid_grid = high_fraction.gte(0.10).And(tree_fraction.lte(0.95))
    return high_mountain.And(valid_grid).selfMask().rename("analysis_domain")


def clean_forest(canopy: ee.Image, args: argparse.Namespace) -> ee.Image:
    # Clean the full canopy image before applying the analysis domain. Masking
    # first would turn domain boundaries into artificial forest edges.
    forest = canopy.select("b1").gt(args.canopy_threshold_m).unmask(0)
    closed = forest.focalMax(args.hole_radius_m, "circle", "meters").focalMin(
        args.hole_radius_m, "circle", "meters"
    )
    component_pixels = closed.selfMask().connectedPixelCount(maxSize=256, eightConnected=True)
    component_area_m2 = component_pixels.multiply(ee.Image.pixelArea())
    minimum_area_m2 = args.minimum_patch_ha * 10_000.0
    return closed.updateMask(component_area_m2.gte(minimum_area_m2)).unmask(0).rename("forest")


def forest_edges(forest: ee.Image, domain: ee.Image, args: argparse.Namespace) -> ee.Image:
    smoothed = forest.focalMedian(args.median_radius_pixels, "square", "pixels")
    laplacian = smoothed.convolve(ee.Kernel.laplacian8())
    return laplacian.zeroCrossing().gt(0).And(domain).selfMask().rename("edge")


def otsu_threshold(histogram: ee.Dictionary) -> ee.Number:
    counts = ee.Array(histogram.get("histogram"))
    means = ee.Array(histogram.get("bucketMeans"))
    size = means.length().get([0])
    total = counts.reduce(ee.Reducer.sum(), [0]).get([0])
    weighted_sum = means.multiply(counts).reduce(ee.Reducer.sum(), [0]).get([0])
    global_mean = weighted_sum.divide(total)

    def between_class_variance(index: ee.Number) -> ee.Number:
        index = ee.Number(index)
        a_counts = counts.slice(0, 0, index)
        a_count = a_counts.reduce(ee.Reducer.sum(), [0]).get([0])
        a_mean = (
            means.slice(0, 0, index)
            .multiply(a_counts)
            .reduce(ee.Reducer.sum(), [0])
            .get([0])
            .divide(a_count)
        )
        b_count = total.subtract(a_count)
        b_mean = weighted_sum.subtract(a_count.multiply(a_mean)).divide(b_count)
        return a_count.multiply(a_mean.subtract(global_mean).pow(2)).add(
            b_count.multiply(b_mean.subtract(global_mean).pow(2))
        )

    indices = ee.List.sequence(1, ee.Number(size).subtract(1))
    scores = ee.Array(indices.map(between_class_variance))
    # ee.Array.argmax returns an ee.List of coordinates. For this 1-D array,
    # take its first coordinate with ee.List.get(0).
    return ee.Number(means.toList().get(scores.argmax().get(0)))


def cold_temperature_mask(
    args: argparse.Namespace, region: ee.Geometry, candidate_mask: ee.Image
) -> Tuple[ee.Image, Optional[ee.Number], Optional[ee.Image], Optional[ee.Dictionary]]:
    if not args.temperature_asset:
        return ee.Image.constant(1).rename("cold_zone"), None, None, None
    temperature = (
        ee.Image(args.temperature_asset)
        .select(args.temperature_band)
        .multiply(args.temperature_scale)
        .add(args.temperature_offset)
        .rename("temperature_c")
    )
    candidate_on_temperature_grid = (
        candidate_mask.unmask(0)
        .reduceResolution(ee.Reducer.max(), maxPixels=4096)
        .reproject(temperature.projection())
        .gt(0)
    )
    reducer = (
        ee.Reducer.histogram(maxBuckets=256)
        .combine(ee.Reducer.count(), sharedInputs=True)
        .combine(ee.Reducer.minMax(), sharedInputs=True)
    )
    temperature_qa = ee.Dictionary(
        temperature.updateMask(candidate_on_temperature_grid).reduceRegion(
            reducer=reducer,
            geometry=region,
            maxPixels=100_000_000,
            tileScale=4,
        )
    )
    if args.temperature_threshold_c is None:
        histogram_key = "temperature_c_histogram"
        fallback_histogram = ee.Dictionary({"histogram": [1, 1], "bucketMeans": [0, 1]})
        histogram = ee.Dictionary(
            ee.Algorithms.If(
                temperature_qa.contains(histogram_key),
                temperature_qa.get(histogram_key),
                fallback_histogram,
            )
        )
        sample_count = ee.Number(
            ee.Algorithms.If(
                temperature_qa.contains("temperature_c_count"),
                temperature_qa.get("temperature_c_count"),
                0,
            )
        )
        minimum = ee.Number(
            ee.Algorithms.If(
                temperature_qa.contains("temperature_c_min"),
                temperature_qa.get("temperature_c_min"),
                0,
            )
        )
        maximum = ee.Number(
            ee.Algorithms.If(
                temperature_qa.contains("temperature_c_max"),
                temperature_qa.get("temperature_c_max"),
                0,
            )
        )
        valid_range = sample_count.gte(2).And(maximum.gt(minimum))
        valid_histogram = ee.Algorithms.If(
            temperature_qa.contains(histogram_key), valid_range, False
        )
        threshold = ee.Number(ee.Algorithms.If(valid_histogram, otsu_threshold(histogram), -9999))
    else:
        threshold = ee.Number(args.temperature_threshold_c)
    return (
        temperature.lte(threshold).rename("cold_zone"),
        threshold,
        temperature,
        temperature_qa,
    )


def conservative_t_critical_95(df: float) -> float:
    """Return a conservative two-sided 5% negative t critical value."""
    critical = T_CRITICAL_95_TWO_SIDED[0][1]
    for minimum_df, value in T_CRITICAL_95_TWO_SIDED:
        if df < minimum_df:
            break
        critical = value
    return critical


def t_critical_image_95(df: ee.Image) -> ee.Image:
    critical = ee.Image.constant(T_CRITICAL_95_TWO_SIDED[0][1])
    for minimum_df, value in T_CRITICAL_95_TWO_SIDED[1:]:
        critical = critical.where(df.gte(minimum_df), value)
    return critical


def welch_upper_edge_mask(
    forest: ee.Image, dem: ee.Image, edges: ee.Image, args: argparse.Namespace
) -> ee.Image:
    kernel = ee.Kernel.square(args.window_radius_m, "meters", normalize=False)
    reducer = (
        ee.Reducer.mean()
        .combine(ee.Reducer.variance(), sharedInputs=True)
        .combine(ee.Reducer.count(), sharedInputs=True)
    )
    forest_dem = dem.updateMask(forest).rename("elevation")
    nonforest_dem = dem.updateMask(forest.Not()).rename("elevation")
    # Both masked populations must be summarized at the same candidate edge
    # pixel. The default skipMasked behavior would preserve mutually exclusive
    # centre masks and make the two statistics impossible to combine.
    forest_stats = forest_dem.reduceNeighborhood(reducer, kernel, skipMasked=False)
    nonforest_stats = nonforest_dem.reduceNeighborhood(reducer, kernel, skipMasked=False)

    n_forest = forest_stats.select("elevation_count")
    n_nonforest = nonforest_stats.select("elevation_count")
    forest_variance_of_mean = forest_stats.select("elevation_variance").divide(n_forest)
    nonforest_variance_of_mean = nonforest_stats.select("elevation_variance").divide(n_nonforest)
    standard_error_squared = forest_variance_of_mean.add(nonforest_variance_of_mean)
    standard_error = standard_error_squared.sqrt()
    t_statistic = forest_stats.select("elevation_mean").subtract(
        nonforest_stats.select("elevation_mean")
    ).divide(standard_error)
    degrees_of_freedom = standard_error_squared.pow(2).divide(
        forest_variance_of_mean.pow(2).divide(n_forest.subtract(1)).add(
            nonforest_variance_of_mean.pow(2).divide(n_nonforest.subtract(1))
        )
    )
    critical = t_critical_image_95(degrees_of_freedom)
    enough_samples = n_forest.gte(args.minimum_window_samples).And(
        n_nonforest.gte(args.minimum_window_samples)
    ).And(standard_error.gt(0))
    return edges.And(enough_samples).And(t_statistic.lt(critical)).selfMask()


def observed_treeline(
    canopy_asset: str,
    year: int,
    domain: ee.Image,
    dem: ee.Image,
    cold_mask: ee.Image,
    args: argparse.Namespace,
) -> ee.Image:
    forest = clean_forest(ee.Image(canopy_asset), args)
    edges = forest_edges(forest, domain, args)
    landforms = ee.Image(LANDFORMS).select("constant")
    non_valley = class_mask(landforms, VALLEY_CLASSES).Not()
    candidates = edges.And(non_valley).And(cold_mask)
    upper_edges = welch_upper_edge_mask(forest, dem, candidates, args)
    return dem.updateMask(upper_edges).rename(f"treeline_elevation_{year}_m").toFloat()


def aggregate_mean(
    image: ee.Image, scale: float, crs: str, reference: Optional[ee.Image]
) -> ee.Image:
    aggregated = image.reduceResolution(ee.Reducer.mean(), maxPixels=4096)
    if reference is not None:
        return aggregated.reproject(reference.projection())
    return aggregated.reproject(crs=crs, scale=scale)


def build_products(args: argparse.Namespace, region: ee.Geometry) -> Dict[str, object]:
    domain = valid_quarter_degree_domain(args.mountain_asset)
    dem = ee.Image(NASADEM).select("elevation").rename("elevation")
    preliminary_forest = clean_forest(ee.Image(FOREST_HEIGHT_2020), args)
    preliminary_edges = forest_edges(preliminary_forest, domain, args)
    cold_mask, threshold, temperature, temperature_qa = cold_temperature_mask(
        args, region, preliminary_edges
    )

    treeline_2000 = observed_treeline(FOREST_HEIGHT_2000, 2000, domain, dem, cold_mask, args)
    treeline_2020 = observed_treeline(FOREST_HEIGHT_2020, 2020, domain, dem, cold_mask, args)
    treeline_2000_1km = aggregate_mean(treeline_2000, args.scale_1km, args.crs, temperature)
    treeline_2020_1km = aggregate_mean(treeline_2020, args.scale_1km, args.crs, temperature)
    shift_rate = treeline_2020_1km.subtract(treeline_2000_1km).divide(20).rename(
        "treeline_shift_rate_m_per_year"
    ).toFloat()
    qa_2020 = (
        domain.unmask(0).rename("domain")
        .addBands(preliminary_forest.unmask(0).rename("forest_2020"))
        .addBands(preliminary_edges.unmask(0).rename("preliminary_edges_2020"))
        .toFloat()
    )
    return {
        "treeline_30m": treeline_2000.addBands(treeline_2020).toFloat(),
        "treeline_1km": treeline_2000_1km.addBands(treeline_2020_1km).toFloat(),
        "shift_rate_1km": shift_rate,
        "qa_2020": qa_2020,
        "otsu_threshold_c": threshold,
        "temperature_qa": temperature_qa,
        "aggregation_reference": temperature,
    }


def initialize(project: str) -> None:
    geemap.ee_initialize(project=project)
    ee.data.setDefaultWorkloadTag(WORKLOAD_TAG)


def validate_temperature_qa_info(temperature_qa: Dict[str, object]) -> None:
    """Reject temperature samples that cannot support a two-class Otsu split."""
    sample_count = temperature_qa.get("temperature_c_count", 0)
    minimum = temperature_qa.get("temperature_c_min")
    maximum = temperature_qa.get("temperature_c_max")
    histogram = temperature_qa.get("temperature_c_histogram")
    histogram_counts = histogram.get("histogram", []) if isinstance(histogram, dict) else []
    if (
        not isinstance(sample_count, (int, float))
        or sample_count < 2
        or not isinstance(minimum, (int, float))
        or not isinstance(maximum, (int, float))
        or minimum >= maximum
        or len(histogram_counts) < 2
    ):
        raise ValueError(f"temperature histogram is empty or degenerate: {temperature_qa}")


def run_check(args: argparse.Namespace, products: Dict[str, object], region: ee.Geometry) -> None:
    temperature_qa = products["temperature_qa"]
    temperature_info = None if temperature_qa is None else temperature_qa.getInfo()
    if temperature_info is not None:
        validate_temperature_qa_info(temperature_info)

    summary = {}
    for name in ("treeline_30m", "shift_rate_1km"):
        image = ee.Image(products[name])
        scale = args.scale_30m if name == "treeline_30m" else args.scale_1km
        summary[name] = image.reduceRegion(
            reducer=ee.Reducer.count(),
            geometry=region,
            scale=scale,
            bestEffort=True,
            maxPixels=10_000_000,
            tileScale=4,
        ).getInfo()
    summary["qa_pixel_sums"] = ee.Image(products["qa_2020"]).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=region,
        scale=args.scale_30m,
        bestEffort=True,
        maxPixels=10_000_000,
        tileScale=4,
    ).getInfo()
    threshold = products["otsu_threshold_c"]
    summary["otsu_threshold_c"] = threshold.getInfo() if threshold is not None else None
    if temperature_info is None:
        summary["temperature_qa"] = None
    else:
        histogram = temperature_info.pop("temperature_c_histogram", None)
        temperature_info["temperature_c_histogram_bucket_count"] = (
            len(histogram.get("histogram", [])) if histogram else 0
        )
        summary["temperature_qa"] = temperature_info
    summary["status"] = "online-graph-check-without-temperature" if threshold is None else "online-graph-check"
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def write_registry_atomic(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def start_exports(
    args: argparse.Namespace, products: Dict[str, object], region: ee.Geometry
) -> Path:
    area_km2 = ee.Number(region.area(maxError=1000)).divide(1_000_000).getInfo()
    if area_km2 > args.max_export_area_km2:
        raise ValueError(
            f"ROI area {area_km2:.1f} km2 exceeds --max-export-area-km2 "
            f"{args.max_export_area_km2:.1f}; submit explicit smaller tiles"
        )

    temperature_qa = ee.Dictionary(products["temperature_qa"]).getInfo()
    validate_temperature_qa_info(temperature_qa)

    aggregation_reference = ee.Image(products["aggregation_reference"])
    aggregation_projection = aggregation_reference.projection().getInfo()
    export_specs = [
        ("treeline_30m", args.scale_30m, None),
        ("treeline_1km", None, aggregation_projection),
        ("shift_rate_1km", None, aggregation_projection),
    ]
    records = []
    for product_name, scale, projection in export_specs:
        description = f"{args.task_prefix}_{product_name}"
        records.append(
            {
                "product": product_name,
                "description": description,
                "task_id": None,
                "state": "PLANNED",
                "file_name_prefix": description,
                "destination": "Google Drive default destination",
                "expected_file_pattern": f"{description}*.tif",
                "scale_m": scale,
                "projection": projection,
            }
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    registry = args.registry_dir / f"{timestamp}-{args.task_prefix}.json"
    registry_payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "project": args.project,
        "bbox": args.bbox,
        "area_km2": area_km2,
        "temperature_qa": temperature_qa,
        "tasks": records,
    }
    write_registry_atomic(registry, registry_payload)

    for index, (product_name, scale, projection) in enumerate(export_specs):
        description = records[index]["description"]
        export_kwargs = dict(
            image=ee.Image(products[product_name]).toFloat(),
            description=description,
            fileNamePrefix=description,
            region=region,
            maxPixels=1e13,
            fileFormat="GeoTIFF",
            formatOptions={"cloudOptimized": True},
        )
        if projection is None:
            export_kwargs.update(scale=scale, crs=args.crs)
        else:
            export_kwargs.update(
                crs=projection["crs"],
                crsTransform=projection["transform"],
            )
        try:
            task = ee.batch.Export.image.toDrive(**export_kwargs)
            records[index]["task_id"] = task.id
            records[index]["state"] = "CREATED"
            registry_payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            write_registry_atomic(registry, registry_payload)
            task.start()
            records[index]["state"] = "SUBMITTED"
            registry_payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            write_registry_atomic(registry, registry_payload)
        except Exception as error:
            records[index]["state"] = "FAILED_TO_START"
            records[index]["error"] = f"{type(error).__name__}: {error}"
            registry_payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            write_registry_atomic(registry, registry_payload)
            raise
    print(json.dumps({"registry": str(registry), "tasks": records}, ensure_ascii=False, indent=2))
    return registry


def monitor_once(project: str, registry_path: Path) -> None:
    initialize(project)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    task_ids = [task["task_id"] for task in registry["tasks"] if task.get("task_id")]
    states = ee.data.getTaskStatus(task_ids)
    counts: Dict[str, int] = {}
    details = []
    by_id = {task["task_id"]: task for task in registry["tasks"]}
    for state in states:
        status = state.get("state", "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
        details.append(
            {
                "task_id": state.get("id"),
                "description": by_id.get(state.get("id"), {}).get("description"),
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

    plan = resolved_plan(args)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    missing = missing_requirements(args, export=args.export)
    if missing:
        parser.error("missing requirements: " + "; ".join(missing))
    if args.export and args.allow_missing_temperature:
        parser.error("--export never permits --allow-missing-temperature")

    west, south, east, north = validate_bbox(args.bbox)
    if args.export and (south < -56 or north > 60):
        parser.error("NASA/NASADEM_HGT/001 covers only 56 S to 60 N; split or supply a documented DEM alternative")
    initialize(args.project)
    region = ee.Geometry.Rectangle([west, south, east, north], proj=None, geodesic=False)
    products = build_products(args, region)
    if args.check:
        run_check(args, products, region)
    elif args.export:
        start_exports(args, products, region)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
