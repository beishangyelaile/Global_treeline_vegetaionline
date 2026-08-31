#!/usr/bin/env python3
"""Step 2: extract per-mountain treelines from validated Step 1 tiles.

The analysis-area table is deliberately an explicit input. The offline
dry-run never initializes Earth Engine; only ``--export`` starts tasks.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    import ee
    import google.auth
    from google.auth.transport.requests import Request
except ImportError as error:  # pragma: no cover - only minimal runtimes
    ee = None  # type: ignore[assignment]
    google = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]
    RUNTIME_IMPORT_ERROR: Optional[ImportError] = error
else:
    RUNTIME_IMPORT_ERROR = None


GLOBAL_TREE_3M = "projects/ee-alpine-506212/assets/Global_tree_3m"
GLOBAL_TREE_5M = "projects/ee-alpine-506212/assets/Global_tree_5m"
CHELSA_BIO01 = "projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21"
TREELINE30M_COLLECTION = (
    "projects/ee-alpine-506212/assets/Treeline_30m_Collection"
)
TREELINE1KM_COLLECTION = (
    "projects/ee-alpine-506212/assets/Treeline_1km_Collection"
)
QA30M_COLLECTION = "projects/ee-alpine-506212/assets/Treeline_QA30m_Collection"
ANALYSIS_MOUNTAINS_ASSET = "projects/ee-wsc/assets/Alpine/GMBA_Sayre"
WORLDCOVER_2021 = "ESA/WorldCover/v200"
WORLDCOVER_BAND = "Map"
WORLDCOVER_TREE_CLASS = 10
MIN_HM_FRACTION = 0.50
MAX_TREE_FRACTION = 0.90
AW3D30 = "JAXA/ALOS/AW3D30/V4_1"
ALOS_LANDFORMS = "CSP/ERGo/1_0/Global/ALOS_landforms"
VALLEY_CLASSES = (41, 42)
FINE_CRS = "EPSG:4326"
FINE_TRANSFORM = [0.00025, 0, -180, 0, -0.00025, 90]
CLIMATE_CRS = "EPSG:4326"
CLIMATE_TRANSFORM = [1 / 120, 0, -180, 0, -1 / 120, 90]
FOREST_MOSAIC_PROJECTION_PLACEMENT = (
    "after_mosaic_select_before_pixel_neighborhood"
)
WORKFLOW = "step2-per-gmba-sayre-treeline-v2"
WORKLOAD_TAG = "globaltreeline-step2"
ADC_SCOPES = tuple(ee.oauth.SCOPES) if ee is not None else ()
THRESHOLDS = (("h3m", 3), ("h5m", 5))
EXPORT_PRODUCTS = ("treeline30m", "treeline1km", "qa30m")
DEFAULT_EXPORT_PRODUCTS = ("treeline30m", "qa30m")

ONE_SIDED_T_CRITICAL_95 = (
    (1, -6.314), (2, -2.920), (3, -2.353), (4, -2.132), (5, -2.015),
    (6, -1.943), (7, -1.895), (8, -1.860), (9, -1.833), (10, -1.812),
    (12, -1.782), (15, -1.753), (20, -1.725), (30, -1.697),
    (40, -1.684), (60, -1.671), (120, -1.658), (1000, -1.645),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract treelines inside each selected complete GMBA Basic geometry."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--export", action="store_true")
    mode.add_argument("--monitor-once", type=Path, metavar="REGISTRY")

    parser.add_argument("--project", default=os.environ.get("EE_PROJECT", "ee-wsc"))
    parser.add_argument(
        "--analysis-mountains-asset",
        default=os.environ.get("GMBA_SAYRE_ASSET", ANALYSIS_MOUNTAINS_ASSET),
        help="filtered GMBA Basic TABLE used as the per-mountain analysis domain",
    )
    parser.add_argument("--step1-manifest", type=Path)
    parser.add_argument("--global-tree-3m", default=GLOBAL_TREE_3M)
    parser.add_argument("--global-tree-5m", default=GLOBAL_TREE_5M)
    parser.add_argument("--chelsa-bio01", default=CHELSA_BIO01)
    parser.add_argument("--treeline30m-collection", default=TREELINE30M_COLLECTION)
    parser.add_argument("--treeline1km-collection", default=TREELINE1KM_COLLECTION)
    parser.add_argument("--qa30m-collection", default=QA30M_COLLECTION)
    parser.add_argument(
        "--export-products",
        nargs="+",
        choices=EXPORT_PRODUCTS,
        default=list(DEFAULT_EXPORT_PRODUCTS),
        help=(
            "products to export; defaults to Step 2A treeline30m + qa30m; "
            "explicit treeline1km selection uses the legacy direct graph"
        ),
    )

    parser.add_argument("--max-mountains", type=int)
    parser.add_argument("--mountain-offset", type=int, default=0)
    parser.add_argument("--check-mountain-id")
    parser.add_argument("--deep-check", action="store_true")
    parser.add_argument("--queue-safety-limit", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-label", default="gmba_sayre_step2_v2")
    parser.add_argument("--task-prefix", default="treeline_gmba_sayre")
    parser.add_argument(
        "--registry-dir",
        type=Path,
        default=Path(os.environ.get("GLOBALTREELINE_ARTIFACTS", "outputs/tasks")),
    )
    parser.add_argument(
        "--report-json",
        type=Path,
        default=Path(__file__).with_name("step2_check_report.json"),
    )

    parser.add_argument("--geometry-max-error-m", type=float, default=1000)
    parser.add_argument("--median-radius-pixels", type=float, default=1)
    parser.add_argument("--window-radius-m", type=float, default=150)
    parser.add_argument("--minimum-samples-per-group", type=int, default=5)
    parser.add_argument("--minimum-elevation-difference-m", type=float, default=0)
    parser.add_argument("--temperature-scale", type=float, default=0.1)
    parser.add_argument("--temperature-offset", type=float, default=-273.15)
    parser.add_argument("--otsu-min-samples", type=int, default=20)
    parser.add_argument("--otsu-max-pixels", type=float, default=1e8)
    parser.add_argument("--tile-scale", type=float, default=4)
    parser.add_argument("--strict-aw3d-native-only", action="store_true")
    return parser


def sanitize_asset_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")
    if not component:
        raise ValueError(f"empty Asset component: {value!r}")
    return component


def implementation_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def canonical_json_sha256(path: Optional[Path]) -> Optional[str]:
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def current_git_commit() -> Optional[str]:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def scientific_configuration(args: argparse.Namespace) -> Dict[str, object]:
    return {
        "workflow": WORKFLOW,
        "implementation_sha256": implementation_sha256(),
        "analysis_domain": "complete_filtered_GMBA_v2_Standard_Basic_geometry",
        "analysis_mountains_asset": args.analysis_mountains_asset,
        "minimum_high_mountain_fraction": MIN_HM_FRACTION,
        "maximum_tree_cover_fraction": MAX_TREE_FRACTION,
        "selection_rule": "hm_fraction_gte_0.50_and_tree_fraction_lte_0.90",
        "worldcover_2021": WORLDCOVER_2021,
        "worldcover_band": WORLDCOVER_BAND,
        "worldcover_tree_class": WORLDCOVER_TREE_CLASS,
        "global_tree_3m": args.global_tree_3m,
        "global_tree_5m": args.global_tree_5m,
        "step1_manifest_sha256": canonical_json_sha256(args.step1_manifest),
        "chelsa_bio01": args.chelsa_bio01,
        "dem": AW3D30,
        "landforms": ALOS_LANDFORMS,
        "valley_classes": list(VALLEY_CLASSES),
        "edge_order": (
            "mosaic_then_select_then_set_fine_default_projection_then_median_"
            "then_laplacian8_zero_crossing_then_domain"
        ),
        "forest_mosaic_default_projection": {
            "crs": FINE_CRS,
            "transform": list(FINE_TRANSFORM),
            "placement": FOREST_MOSAIC_PROJECTION_PLACEMENT,
        },
        "median_radius_pixels": args.median_radius_pixels,
        "otsu_scope": "per_gmba_threshold",
        "otsu_year_pooling": "2000_2020",
        "otsu_native_cells_counted_once": True,
        "otsu_same_threshold_both_years": True,
        "otsu_invalid_policy": "flag_no_fallback",
        "window_diameter_m": args.window_radius_m * 2,
        "local_test": "one_sided_welch_t",
        "local_population": "analysis_domain_only_no_buffer",
        "minimum_samples_per_group": args.minimum_samples_per_group,
        "minimum_elevation_difference_m": args.minimum_elevation_difference_m,
        "fine_grid": {"crs": FINE_CRS, "transform": list(FINE_TRANSFORM)},
        "climate_grid": {"crs": CLIMATE_CRS, "transform": list(CLIMATE_TRANSFORM)},
        "export_products": list(args.export_products),
        "treeline1km_execution": (
            "legacy_direct_full_graph"
            if "treeline1km" in args.export_products
            else "separate_step2b_from_materialized_treeline30m"
        ),
    }


def configuration_hash(args: argparse.Namespace) -> str:
    encoded = json.dumps(
        scientific_configuration(args), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def missing_requirements(args: argparse.Namespace) -> List[str]:
    missing: List[str] = []
    if not args.analysis_mountains_asset:
        missing.append("analysis_mountains_asset")
    if args.step1_manifest is None:
        missing.append("step1_manifest")
    elif not args.step1_manifest.is_file():
        missing.append("step1_manifest_not_found")
    return missing


def resolved_plan(args: argparse.Namespace) -> Dict[str, object]:
    count = args.max_mountains or 0
    missing = missing_requirements(args)
    return {
        "status": "offline-step2-plan",
        "ready": not missing,
        "missing_requirements": missing,
        "project": args.project,
        "analysis_mountains_asset": args.analysis_mountains_asset,
        "step1_manifest": str(args.step1_manifest) if args.step1_manifest else None,
        "selection": {
            "mountain_offset": args.mountain_offset,
            "max_mountains": args.max_mountains,
        },
        "forest_inputs": {"h3m": args.global_tree_3m, "h5m": args.global_tree_5m},
        "products": list(args.export_products),
        "legacy_direct_1km": "treeline1km" in args.export_products,
        "expected_task_count": count * len(args.export_products),
        "configuration_hash": configuration_hash(args),
        "scientific_configuration": scientific_configuration(args),
    }


def initialize_with_adc(project: str) -> Dict[str, object]:
    credentials, detected_project = google.auth.default(scopes=list(ADC_SCOPES))
    credentials.refresh(Request())
    ee.Initialize(credentials=credentials, project=project)
    ee.data.setDefaultWorkloadTag(WORKLOAD_TAG)
    return {
        "credential_type": type(credentials).__name__,
        "detected_project": detected_project,
        "quota_project": getattr(credentials, "quota_project_id", None),
        "ee_project": project,
    }


def add_analysis_keys(feature: "ee.Feature") -> "ee.Feature":
    feature = ee.Feature(feature)
    gmba_id = ee.Number(feature.get("GMBA_V2_ID"))
    return feature.set(
        "gmba_id_text", gmba_id.format("%.0f"), "gmba_sort_key", gmba_id
    )


def analysis_mountains(args: argparse.Namespace) -> "ee.FeatureCollection":
    return (
        ee.FeatureCollection(args.analysis_mountains_asset)
        .filter(ee.Filter.eq("MapUnit", "Basic"))
        .filter(ee.Filter.gte("hm_fraction", MIN_HM_FRACTION))
        .filter(ee.Filter.lte("tree_fraction", MAX_TREE_FRACTION))
        .map(add_analysis_keys)
    )


def validate_analysis_table(args: argparse.Namespace) -> Dict[str, object]:
    info = ee.data.getAsset(args.analysis_mountains_asset)
    if info.get("type") != "TABLE":
        raise ValueError("--analysis-mountains-asset must be a TABLE")
    source = ee.FeatureCollection(args.analysis_mountains_asset)
    required_properties = [
        "GMBA_V2_ID", "MapUnit", "hm_area_km2", "gmba_area_km2",
        "hm_fraction", "tree_area_km2", "tree_fraction",
    ]
    complete = source.filter(ee.Filter.notNull(required_properties))
    selected = analysis_mountains(args)
    first = ee.Feature(source.first())
    report = ee.Dictionary(
        {
            "source_feature_count": source.size(),
            "complete_property_count": complete.size(),
            "selected_feature_count": selected.size(),
            "distinct_gmba_id_count": selected.aggregate_count_distinct(
                "gmba_id_text"
            ),
            "mapunit_histogram": source.aggregate_histogram("MapUnit"),
            "hm_fraction_min": selected.aggregate_min("hm_fraction"),
            "hm_fraction_max": selected.aggregate_max("hm_fraction"),
            "tree_fraction_min": selected.aggregate_min("tree_fraction"),
            "tree_fraction_max": selected.aggregate_max("tree_fraction"),
            "below_minimum_hm_fraction_count": source.filter(
                ee.Filter.lt("hm_fraction", MIN_HM_FRACTION)
            ).size(),
            "above_maximum_tree_fraction_count": source.filter(
                ee.Filter.gt("tree_fraction", MAX_TREE_FRACTION)
            ).size(),
            "first_geometry_area_km2": first.geometry().area(
                maxError=100
            ).divide(1e6),
            "first_gmba_area_km2": first.get("gmba_area_km2"),
            "first_hm_area_km2": first.get("hm_area_km2"),
        }
    ).getInfo()
    source_count = int(report["source_feature_count"])
    selected_count = int(report["selected_feature_count"])
    if int(report["complete_property_count"]) != source_count:
        raise ValueError("analysis TABLE has null required selection properties")
    if selected_count < 1:
        raise ValueError("analysis TABLE resolves no mountains after fixed filters")
    if int(report["distinct_gmba_id_count"]) != selected_count:
        raise ValueError("analysis TABLE has missing or duplicate GMBA_V2_ID values")
    if int(report["below_minimum_hm_fraction_count"]) != 0:
        raise ValueError("analysis TABLE contains hm_fraction below 0.50")
    if int(report["above_maximum_tree_fraction_count"]) != 0:
        raise ValueError("analysis TABLE contains tree_fraction above 0.90")
    return {
        "id": args.analysis_mountains_asset,
        "type": info.get("type"),
        "size_bytes": info.get("sizeBytes"),
        "minimum_high_mountain_fraction": MIN_HM_FRACTION,
        "maximum_tree_cover_fraction": MAX_TREE_FRACTION,
        "worldcover_2021": WORLDCOVER_2021,
        **report,
    }


def list_child_assets(parent: str) -> List[Mapping[str, object]]:
    children: List[Mapping[str, object]] = []
    request: Dict[str, object] = {"parent": parent, "pageSize": 1000}
    while True:
        response = ee.data.listAssets(request)
        children.extend(response.get("assets", []))
        token = response.get("nextPageToken")
        if not token:
            return children
        request["pageToken"] = token


def inventory_fixture(tile_id: str, canopy_threshold_m: int) -> Dict[str, object]:
    """Return a minimal valid inventory row for pure offline tests."""
    return {
        "id": f"projects/example/assets/GFC_2000_2020_{tile_id}",
        "type": "IMAGE",
        "sizeBytes": "1",
        "bands": [{"id": "tree_2000"}, {"id": "tree_2020"}],
        "properties": {
            "tile_id": tile_id,
            "canopy_threshold_m": canopy_threshold_m,
            "mmu_max_size": 500,
            "configuration_hash": "test-step1-hash",
            "grid_crs": FINE_CRS,
            "grid_transform": json.dumps(FINE_TRANSFORM, separators=(",", ":")),
        },
    }


def _integer_size(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def validate_step1_inventory(
    expected_tile_ids: Sequence[str],
    inventories: Mapping[str, Sequence[Mapping[str, object]]],
    expected_configuration_hash: Optional[str] = None,
) -> Dict[str, object]:
    errors: List[str] = []
    expected = set(map(str, expected_tile_ids))
    resolved: Dict[str, Dict[str, Mapping[str, object]]] = {}
    hashes: set[str] = set()
    grids: set[Tuple[str, str]] = set()
    tile_sets: Dict[str, set[str]] = {}
    for product, expected_threshold in (("h3m", 3), ("h5m", 5)):
        by_tile: Dict[str, Mapping[str, object]] = {}
        seen: set[str] = set()
        for asset in inventories.get(product, []):
            properties = asset.get("properties") or {}
            identifier = str(properties.get("tile_id") or "")
            if not identifier:
                errors.append(f"{product} Asset lacks tile_id: {asset.get('id')}")
                continue
            if identifier in seen:
                errors.append(f"duplicate {product} tile: {identifier}")
            seen.add(identifier)
            by_tile[identifier] = asset
            bands = [str(band.get("id")) for band in asset.get("bands", [])]
            if bands != ["tree_2000", "tree_2020"]:
                errors.append(f"invalid bands for {product}/{identifier}: {bands}")
            if asset.get("type") != "IMAGE":
                errors.append(f"invalid Asset type for {product}/{identifier}")
            if _integer_size(asset.get("sizeBytes")) <= 0:
                errors.append(f"empty Asset for {product}/{identifier}")
            if int(properties.get("mmu_max_size", -1)) != 500:
                errors.append(f"invalid max size for {product}/{identifier}")
            if int(properties.get("canopy_threshold_m", -1)) != expected_threshold:
                errors.append(f"wrong canopy threshold for {product}/{identifier}")
            config_hash = str(properties.get("configuration_hash") or "")
            if not config_hash:
                errors.append(f"missing configuration hash for {product}/{identifier}")
            else:
                hashes.add(config_hash)
            grid = (
                str(properties.get("grid_crs") or ""),
                str(properties.get("grid_transform") or ""),
            )
            grids.add(grid)
            if properties.get("task_state") in {"FAILED", "CANCELLED"}:
                errors.append(f"failed tile recorded for {product}/{identifier}")
        resolved[product] = by_tile
        tile_sets[product] = set(by_tile)
        missing = expected - set(by_tile)
        if missing:
            errors.append(f"missing {product} tiles: {sorted(missing)}")
    if tile_sets.get("h3m", set()) != tile_sets.get("h5m", set()):
        errors.append("3 m and 5 m tile IDs do not match")
    if len(hashes) != 1:
        errors.append(f"inconsistent Step 1 configuration hashes: {sorted(hashes)}")
    elif expected_configuration_hash and hashes != {expected_configuration_hash}:
        errors.append("Step 1 configuration hash differs from the manifest")
    if len(grids) != 1 or grids != {
        (FINE_CRS, json.dumps(FINE_TRANSFORM, separators=(",", ":")))
    }:
        errors.append(f"inconsistent or unexpected grids: {sorted(grids)}")
    return {
        "ready": not errors,
        "errors": errors,
        "expected_tile_count": len(expected),
        "h3m_tile_count": len(tile_sets.get("h3m", set())),
        "h5m_tile_count": len(tile_sets.get("h5m", set())),
        "configuration_hashes": sorted(hashes),
    }


def fetch_collection_inventory(collection_id: str) -> List[Mapping[str, object]]:
    info = ee.data.getAsset(collection_id)
    if info.get("type") != "IMAGE_COLLECTION":
        raise ValueError(f"forest input must be IMAGE_COLLECTION: {collection_id}")
    return [ee.data.getAsset(str(item["id"])) for item in list_child_assets(collection_id)]


def load_step1_manifest(path: Path) -> Dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("tiles"), list):
        raise ValueError("Step 1 manifest lacks a tiles list")
    ids = [str(tile.get("tile_id")) for tile in payload["tiles"]]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("Step 1 manifest has missing or duplicate tile IDs")
    return payload


def run_step1_integrity_check(
    args: argparse.Namespace, required_tile_ids: Optional[Sequence[str]] = None
) -> Dict[str, object]:
    manifest = load_step1_manifest(args.step1_manifest)
    expected = [str(tile["tile_id"]) for tile in manifest["tiles"]]
    inventories = {
        "h3m": fetch_collection_inventory(args.global_tree_3m),
        "h5m": fetch_collection_inventory(args.global_tree_5m),
    }
    result = validate_step1_inventory(
        expected, inventories, str(manifest.get("configuration_hash") or "") or None
    )
    result["manifest"] = str(args.step1_manifest)
    result["collections"] = {
        "h3m": args.global_tree_3m,
        "h5m": args.global_tree_5m,
    }
    required = set(map(str, required_tile_ids or []))
    missing_required = sorted(required - set(expected))
    result["required_tile_ids"] = sorted(required)
    result["missing_required_tile_ids"] = missing_required
    if missing_required:
        result["ready"] = False
        result["errors"].append(
            f"current mountains need tiles absent from manifest: {missing_required}"
        )
    if not result["ready"]:
        raise ValueError("Step 1 integrity check failed: " + "; ".join(result["errors"]))
    return result


def _grid_tile_id(min_lon: int, min_lat: int) -> str:
    lat = ("N" if min_lat >= 0 else "S") + f"{abs(min_lat):02d}"
    lon = ("E" if min_lon >= 0 else "W") + f"{abs(min_lon):03d}"
    return f"{lat}_{lon}"


def resolve_required_tile_ids(
    args: argparse.Namespace, mountains: Sequence[Mapping[str, object]]
) -> List[str]:
    identifiers = [str(mountain["mountain_id"]) for mountain in mountains]
    selected = analysis_mountains(args).filter(
        ee.Filter.inList("gmba_id_text", identifiers)
    )
    tiles = []
    for min_lat in range(-90, 90, 10):
        for min_lon in range(-180, 180, 10):
            identifier = _grid_tile_id(min_lon, min_lat)
            tiles.append(
                ee.Feature(
                    ee.Geometry.Rectangle(
                        [min_lon, min_lat, min_lon + 10, min_lat + 10],
                        proj=FINE_CRS,
                        geodesic=False,
                    ),
                    {"tile_id": identifier},
                )
            )

    def mark_required(feature: "ee.Feature") -> "ee.Feature":
        feature = ee.Feature(feature)
        return feature.set(
            "required", selected.filterBounds(feature.geometry()).size().gt(0)
        )

    return list(
        map(
            str,
            ee.FeatureCollection(tiles)
            .map(mark_required)
            .filter(ee.Filter.eq("required", 1))
            .aggregate_array("tile_id")
            .getInfo(),
        )
    )


def class_mask(image: "ee.Image", values: Iterable[int]) -> "ee.Image":
    masks = [image.eq(value) for value in values]
    result = masks[0]
    for mask in masks[1:]:
        result = result.Or(mask)
    return result


def build_global_forest_inputs(args: argparse.Namespace) -> Dict[str, "ee.Image"]:
    projection = ee.Projection(FINE_CRS, transform=FINE_TRANSFORM)
    forest_h3m = (
        ee.ImageCollection(args.global_tree_3m)
        .mosaic()
        .select(["tree_2000", "tree_2020"])
        .setDefaultProjection(projection)
    )
    forest_h5m = (
        ee.ImageCollection(args.global_tree_5m)
        .mosaic()
        .select(["tree_2000", "tree_2020"])
        .setDefaultProjection(projection)
    )
    return {"h3m": forest_h3m, "h5m": forest_h5m}


def forest_edges_global(forest: "ee.Image", args: argparse.Namespace) -> "ee.Image":
    smoothed_global = forest.focalMedian(
        radius=args.median_radius_pixels,
        kernelType="square",
        units="pixels",
    ).toFloat()
    return (
        smoothed_global.convolve(ee.Kernel.laplacian8())
        .zeroCrossing()
        .gt(0)
        .rename("forest_edge")
    )


def build_aw3d(region: "ee.Geometry", strict_native_only: bool) -> "ee.Image":
    def prepare(image: "ee.Image") -> "ee.Image":
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


def build_mountain_context(args: argparse.Namespace, mountain_id: str) -> Dict[str, object]:
    feature = ee.Feature(
        analysis_mountains(args)
        .filter(ee.Filter.eq("gmba_id_text", str(mountain_id)))
        .first()
    )
    geometry = feature.geometry(maxError=args.geometry_max_error_m)
    bounds = geometry.bounds(args.geometry_max_error_m)
    projection = ee.Projection(FINE_CRS, FINE_TRANSFORM)
    domain = (
        ee.Image(0).byte().paint(ee.FeatureCollection([feature]), 1)
        .setDefaultProjection(projection).clip(bounds).selfMask().rename("analysis_domain")
    )
    aw3d = build_aw3d(geometry, args.strict_aw3d_native_only)
    dem = aw3d.select("elevation").toFloat()
    landforms = ee.Image(ALOS_LANDFORMS).select("constant").clip(geometry)
    nonvalley = class_mask(landforms, VALLEY_CLASSES).Not().rename("non_valley")
    return {
        "feature": feature,
        "geometry": geometry,
        "bounds": bounds,
        "domain": domain,
        "aw3d": aw3d,
        "dem": dem,
        "nonvalley": nonvalley,
    }


def otsu_threshold_from_histogram(
    histogram: Mapping[str, object], minimum_samples: int = 1
) -> float:
    counts = [float(value) for value in histogram.get("histogram", [])]
    means = [float(value) for value in histogram.get("bucketMeans", [])]
    total = sum(counts)
    if (
        len(counts) < 2
        or len(counts) != len(means)
        or total < minimum_samples
        or sum(value > 0 for value in counts) < 2
    ):
        raise ValueError("temperature histogram is empty, too small, or degenerate")
    weighted_sum = sum(mean * count for mean, count in zip(means, counts))
    global_mean = weighted_sum / total
    scores: List[float] = []
    for split in range(1, len(means)):
        a_count = sum(counts[:split])
        b_count = total - a_count
        if a_count <= 0 or b_count <= 0:
            scores.append(float("-inf"))
            continue
        a_mean = sum(
            mean * count for mean, count in zip(means[:split], counts[:split])
        ) / a_count
        b_mean = (weighted_sum - a_count * a_mean) / b_count
        scores.append(
            a_count * (a_mean - global_mean) ** 2
            + b_count * (b_mean - global_mean) ** 2
        )
    best = max(range(len(scores)), key=scores.__getitem__)
    if not math.isfinite(scores[best]):
        raise ValueError("temperature histogram has no valid Otsu split")
    return means[best]


def otsu_threshold_ee(
    histogram: "ee.ComputedObject", scale: float, offset: float, minimum_samples: int
) -> "ee.Dictionary":
    safe_dummy = ee.Dictionary({"histogram": [1, 1], "bucketMeans": [0, 1]})
    is_null = ee.Algorithms.IsEqual(histogram, None)
    raw = ee.Dictionary(ee.Algorithms.If(is_null, safe_dummy, histogram))
    raw_counts = ee.Array(raw.get("histogram"))
    raw_means = ee.Array(raw.get("bucketMeans"))
    bucket_count = ee.Number(raw_means.length().get([0]))
    sample_count = ee.Number(raw_counts.reduce(ee.Reducer.sum(), [0]).get([0]))
    nonempty = ee.Number(raw_counts.gt(0).reduce(ee.Reducer.sum(), [0]).get([0]))
    valid = (
        ee.Number(ee.Algorithms.If(is_null, 0, 1))
        .multiply(bucket_count.gte(2))
        .multiply(nonempty.gte(2))
        .multiply(sample_count.gte(minimum_samples))
    )
    safe = ee.Dictionary(ee.Algorithms.If(valid.eq(1), raw, safe_dummy))
    counts = ee.Array(safe.get("histogram"))
    means = ee.Array(safe.get("bucketMeans"))
    size = ee.Number(means.length().get([0]))
    total = ee.Number(counts.reduce(ee.Reducer.sum(), [0]).get([0]))
    weighted_sum = ee.Number(
        means.multiply(counts).reduce(ee.Reducer.sum(), [0]).get([0])
    )
    global_mean = weighted_sum.divide(total)

    def score(split: "ee.Number") -> "ee.Number":
        split = ee.Number(split)
        a_counts = counts.slice(0, 0, split)
        a_count = ee.Number(a_counts.reduce(ee.Reducer.sum(), [0]).get([0]))
        a_means = means.slice(0, 0, split)
        a_mean = ee.Number(
            a_means.multiply(a_counts).reduce(ee.Reducer.sum(), [0]).get([0])
        ).divide(a_count)
        b_count = total.subtract(a_count)
        b_mean = weighted_sum.subtract(a_count.multiply(a_mean)).divide(b_count)
        return a_count.multiply(a_mean.subtract(global_mean).pow(2)).add(
            b_count.multiply(b_mean.subtract(global_mean).pow(2))
        )

    scores = ee.Array(ee.List.sequence(1, size.subtract(1)).map(score))
    candidates = means.slice(0, 0, size.subtract(1))
    raw_threshold = ee.Number(candidates.sort(scores).get([-1]))
    return ee.Dictionary(
        {
            "valid": valid,
            "threshold_raw": raw_threshold,
            "threshold_c": raw_threshold.multiply(scale).add(offset),
            "candidate_sample_count": ee.Number(
                ee.Algorithms.If(is_null, 0, sample_count)
            ),
            "histogram_bucket_count": ee.Number(
                ee.Algorithms.If(is_null, 0, bucket_count)
            ),
            "source": "per-gmba-pooled-2000-2020",
        }
    )


def temperature_graph(
    pooled_edges: "ee.Image", geometry: "ee.Geometry", args: argparse.Namespace
) -> Dict[str, object]:
    raw = ee.Image(args.chelsa_bio01).select([0]).rename("bio01_raw")
    temperature = raw.multiply(args.temperature_scale).add(
        args.temperature_offset
    ).rename("temperature_c")
    candidate_at_native_grid = (
        pooled_edges.unmask(0)
        .reduceResolution(ee.Reducer.max(), maxPixels=4096)
        .reproject(raw.projection())
        .gt(0)
    )
    histogram = raw.updateMask(candidate_at_native_grid).reduceRegion(
        reducer=ee.Reducer.histogram(maxBuckets=256, minBucketWidth=1),
        geometry=geometry,
        crs=raw.projection(),
        scale=raw.projection().nominalScale(),
        maxPixels=args.otsu_max_pixels,
        tileScale=args.tile_scale,
    ).get("bio01_raw")
    return {"temperature": temperature, "histogram": histogram}


def lower_tail_t_critical_image(df: "ee.Image") -> "ee.Image":
    critical = ee.Image.constant(ONE_SIDED_T_CRITICAL_95[0][1])
    for minimum_df, value in ONE_SIDED_T_CRITICAL_95[1:]:
        critical = critical.where(df.gte(minimum_df), value)
    return critical


def upper_edge_test(
    forest: "ee.Image",
    dem: "ee.Image",
    candidates: "ee.Image",
    analysis_domain: "ee.Image",
    args: argparse.Namespace,
) -> Dict[str, "ee.Image"]:
    kernel = ee.Kernel.square(args.window_radius_m, "meters", False)
    reducer = ee.Reducer.mean().combine(
        ee.Reducer.variance(), sharedInputs=True
    ).combine(ee.Reducer.count(), sharedInputs=True)
    forest_population = forest.eq(1).And(analysis_domain)
    nonforest_population = forest.eq(0).And(analysis_domain)
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
    vf_mean = forest_stats.select("elevation_variance").divide(nf)
    vn_mean = nonforest_stats.select("elevation_variance").divide(nn)
    standard_error_squared = vf_mean.add(vn_mean)
    standard_error = standard_error_squared.sqrt()
    degrees_of_freedom = standard_error_squared.pow(2).divide(
        vf_mean.pow(2).divide(nf.subtract(1)).add(
            vn_mean.pow(2).divide(nn.subtract(1))
        )
    )
    statistic = mf.subtract(mn).divide(standard_error)
    elevation_difference = mn.subtract(mf)
    enough = (
        nf.gte(args.minimum_samples_per_group)
        .And(nn.gte(args.minimum_samples_per_group))
        .And(standard_error.gt(0))
    )
    upper = (
        candidates.And(enough)
        .And(elevation_difference.gte(args.minimum_elevation_difference_m))
        .And(statistic.lt(lower_tail_t_critical_image(degrees_of_freedom)))
        .selfMask()
        .rename("upper_edge")
    )
    return {
        "upper": upper,
        "elevation_difference": elevation_difference,
        "t_statistic": statistic,
        "forest_count": nf,
        "nonforest_count": nn,
    }


def aggregate_to_climate_grid(image: "ee.Image") -> "ee.Image":
    return image.reduceResolution(ee.Reducer.mean(), maxPixels=4096).reproject(
        ee.Projection(CLIMATE_CRS, CLIMATE_TRANSFORM)
    )


def add_band(base: Optional["ee.Image"], band: "ee.Image") -> "ee.Image":
    return band if base is None else base.addBands(band)


def expected_product_bands() -> Dict[str, List[str]]:
    treeline30m: List[str] = []
    treeline1km: List[str] = []
    qa = [
        "analysis_domain", "sayre_high", "gmba_mask", "hm_fraction",
        "tree_fraction", "non_valley", "dem_elevation_m", "dem_msk", "dem_stk",
    ]
    for label, _ in THRESHOLDS:
        for year in (2000, 2020):
            treeline30m.append(f"treeline_{year}_{label}_m")
            qa.extend(
                [
                    f"forest_clean_{year}_{label}",
                    f"candidate_edge_{year}_{label}",
                    f"edge_post_landform_{year}_{label}",
                    f"upper_{year}_{label}",
                    f"elevation_difference_{year}_{label}_m",
                    f"t_statistic_{year}_{label}",
                    f"forest_sample_count_{year}_{label}",
                    f"nonforest_sample_count_{year}_{label}",
                ]
            )
            treeline1km.append(f"treeline_{year}_{label}_mean_m")
        treeline1km.append(f"shift_2000_2020_{label}_m_per_year")
        qa.extend([f"cold_zone_{label}", f"otsu_valid_{label}"])
    return {"treeline30m": treeline30m, "treeline1km": treeline1km, "qa30m": qa}


def product_pyramiding_policies() -> Dict[str, Dict[str, str]]:
    qa = {".default": "mode"}
    for band in expected_product_bands()["qa30m"]:
        if (
            band in {"hm_fraction", "tree_fraction", "dem_elevation_m", "dem_stk"}
            or band.startswith("elevation_difference_")
            or band.startswith("t_statistic_")
        ):
            qa[band] = "mean"
        else:
            qa[band] = "mode"
    return {
        "treeline30m": {".default": "mean"},
        "treeline1km": {".default": "mean"},
        "qa30m": qa,
    }


def build_mountain_bundle(args: argparse.Namespace, mountain: Mapping[str, object]) -> Dict[str, object]:
    forests = build_global_forest_inputs(args)
    global_edges: Dict[str, Dict[int, "ee.Image"]] = {}
    for label, _ in THRESHOLDS:
        global_edges[label] = {
            year: forest_edges_global(forests[label].select(f"tree_{year}"), args)
            for year in (2000, 2020)
        }

    mountain_id = str(mountain["mountain_id"])
    context = build_mountain_context(args, mountain_id)
    feature = ee.Feature(context["feature"])
    domain = ee.Image(context["domain"])
    dem = ee.Image(context["dem"])
    nonvalley = ee.Image(context["nonvalley"])
    aw3d = ee.Image(context["aw3d"])
    qa = (
        domain.unmask(0).rename("analysis_domain")
        .addBands(ee.Image.constant(1).updateMask(domain).rename("sayre_high"))
        .addBands(domain.unmask(0).rename("gmba_mask"))
        .addBands(
            ee.Image.constant(ee.Number(feature.get("hm_fraction")))
            .updateMask(domain).rename("hm_fraction")
        )
        .addBands(
            ee.Image.constant(ee.Number(feature.get("tree_fraction")))
            .updateMask(domain).rename("tree_fraction")
        )
        .addBands(nonvalley.unmask(0))
        .addBands(dem.rename("dem_elevation_m"))
        .addBands(aw3d.select("dem_msk").unmask(255))
        .addBands(aw3d.select("dem_stk").unmask(0))
    )
    treeline30m: Optional["ee.Image"] = None
    treeline1km: Optional["ee.Image"] = None
    otsu: Dict[str, "ee.Dictionary"] = {}
    for label, _ in THRESHOLDS:
        forest_by_year = {
            year: forests[label].select(f"tree_{year}").updateMask(domain)
            for year in (2000, 2020)
        }
        candidate_edges = {
            year: global_edges[label][year].updateMask(domain).selfMask()
            for year in (2000, 2020)
        }
        post_landform = {
            year: candidate_edges[year].And(nonvalley).selfMask()
            for year in (2000, 2020)
        }
        pooled = post_landform[2000].Or(post_landform[2020]).selfMask()
        temp_graph = temperature_graph(pooled, context["geometry"], args)
        threshold = otsu_threshold_ee(
            temp_graph["histogram"],
            args.temperature_scale,
            args.temperature_offset,
            args.otsu_min_samples,
        )
        otsu[label] = threshold
        valid = ee.Image.constant(ee.Number(threshold.get("valid"))).eq(1)
        cold = ee.Image(temp_graph["temperature"]).lte(
            ee.Number(threshold.get("threshold_c"))
        ).rename(f"cold_zone_{label}")
        per_year: Dict[int, "ee.Image"] = {}
        for year in (2000, 2020):
            candidates = post_landform[year].And(cold).And(valid).selfMask()
            test = upper_edge_test(forest_by_year[year], dem, candidates, domain, args)
            elevation = dem.updateMask(test["upper"]).rename(
                f"treeline_{year}_{label}_m"
            ).toFloat()
            per_year[year] = elevation
            treeline30m = add_band(treeline30m, elevation)
            qa = (
                qa.addBands(forest_by_year[year].unmask(0).rename(f"forest_clean_{year}_{label}"))
                .addBands(candidate_edges[year].unmask(0).rename(f"candidate_edge_{year}_{label}"))
                .addBands(post_landform[year].unmask(0).rename(f"edge_post_landform_{year}_{label}"))
                .addBands(test["upper"].unmask(0).rename(f"upper_{year}_{label}"))
                .addBands(test["elevation_difference"].rename(f"elevation_difference_{year}_{label}_m"))
                .addBands(test["t_statistic"].rename(f"t_statistic_{year}_{label}"))
                .addBands(test["forest_count"].rename(f"forest_sample_count_{year}_{label}"))
                .addBands(test["nonforest_count"].rename(f"nonforest_sample_count_{year}_{label}"))
            )
        mean2000 = aggregate_to_climate_grid(per_year[2000]).rename(
            f"treeline_2000_{label}_mean_m"
        )
        mean2020 = aggregate_to_climate_grid(per_year[2020]).rename(
            f"treeline_2020_{label}_mean_m"
        )
        shift = mean2020.subtract(mean2000).divide(20).rename(
            f"shift_2000_2020_{label}_m_per_year"
        ).toFloat()
        treeline1km = add_band(treeline1km, mean2000)
        treeline1km = add_band(treeline1km, mean2020)
        treeline1km = add_band(treeline1km, shift)
        qa = qa.addBands(cold.unmask(0)).addBands(valid.rename(f"otsu_valid_{label}"))

    if treeline30m is None or treeline1km is None:
        raise ValueError("no forest products resolved")
    metadata: Dict[str, object] = {
        "mountain_id": mountain_id,
        "analysis_domain": "complete_filtered_GMBA_v2_Standard_Basic_geometry",
        "analysis_mountains_asset": args.analysis_mountains_asset,
        "minimum_high_mountain_fraction": MIN_HM_FRACTION,
        "maximum_tree_cover_fraction": MAX_TREE_FRACTION,
        "hm_fraction": feature.get("hm_fraction"),
        "tree_fraction": feature.get("tree_fraction"),
        "worldcover_2021": WORLDCOVER_2021,
        "worldcover_tree_class": WORLDCOVER_TREE_CLASS,
        "sayre_high_qa_semantics": "mountain_selection_flag_not_pixelwise_mask",
        "source_global_tree_3m": args.global_tree_3m,
        "source_global_tree_5m": args.global_tree_5m,
        "source_chelsa_bio01": args.chelsa_bio01,
        "run_label": args.run_label,
        "configuration_hash": configuration_hash(args),
        "git_commit": current_git_commit() or "unknown",
        "workflow": WORKFLOW,
        "forest_edge_order": (
            "mosaic_select_set_fine_default_projection_median_"
            "zero_crossing_domain_mask"
        ),
        "forest_mosaic_default_projection_crs": FINE_CRS,
        "forest_mosaic_default_projection_transform": list(FINE_TRANSFORM),
        "forest_mosaic_default_projection_placement": (
            FOREST_MOSAIC_PROJECTION_PLACEMENT
        ),
        "mountain_buffer_m": 0,
        "otsu_population": "pooled_2000_2020_post_landform_native_bio01_cells",
        "otsu_same_threshold_both_years": True,
        "window_size_m": args.window_radius_m * 2,
        "t_test_variance": "welch",
        "t_test_alternative": "less",
    }
    for label, info in otsu.items():
        metadata[f"otsu_valid_{label}"] = info.get("valid")
        metadata[f"otsu_threshold_raw_{label}"] = info.get("threshold_raw")
        metadata[f"otsu_threshold_c_{label}"] = info.get("threshold_c")
        metadata[f"otsu_sample_count_{label}"] = info.get("candidate_sample_count")
    return {
        "context": context,
        "images": {
            "treeline30m": treeline30m.clip(context["bounds"]).toFloat(),
            "treeline1km": treeline1km.clip(context["bounds"]).toFloat(),
            "qa30m": qa.updateMask(domain).clip(context["bounds"]).toFloat(),
        },
        "metadata": metadata,
        "otsu": otsu,
    }


def resolve_mountain_plan(args: argparse.Namespace) -> List[Dict[str, object]]:
    collection = analysis_mountains(args).sort("gmba_sort_key")
    if args.max_mountains is not None:
        collection = ee.FeatureCollection(
            collection.toList(args.max_mountains, args.mountain_offset)
        )
    count = int(collection.size().getInfo())
    identifiers = collection.aggregate_array("gmba_id_text").getInfo()
    if count != len(identifiers) or len(set(map(str, identifiers))) != count:
        raise ValueError("analysis table has missing or duplicate gmba_id_text values")
    return [
        {
            "mountain_id": str(identifier),
            "mountain_key": sanitize_asset_component(f"gmba_{identifier}"),
        }
        for identifier in identifiers
    ]


def choose_check_mountain(
    plan: Sequence[Mapping[str, object]], args: argparse.Namespace
) -> Mapping[str, object]:
    if not plan:
        raise ValueError("selected mountain plan is empty")
    if args.check_mountain_id:
        matches = [item for item in plan if item["mountain_id"] == args.check_mountain_id]
        if not matches:
            raise ValueError("requested check mountain is absent")
        return matches[0]
    return plan[len(plan) // 2]


def validate_output_collections(args: argparse.Namespace) -> Dict[str, Dict[str, object]]:
    summaries: Dict[str, Dict[str, object]] = {}
    for product, asset_id, _, _ in selected_product_specs(args):
        info = ee.data.getAsset(asset_id)
        if info.get("type") != "IMAGE_COLLECTION":
            raise ValueError(f"target for {product} must be IMAGE_COLLECTION")
        children = list_child_assets(asset_id)
        summaries[product] = {
            "id": asset_id,
            "children": {str(child.get("id")): child for child in children},
            "existing_child_count": len(children),
        }
    return summaries


def selected_product_specs(
    args: argparse.Namespace,
) -> Tuple[Tuple[str, str, str, Sequence[float]], ...]:
    selected = set(args.export_products)
    return tuple(
        spec
        for spec in (
            ("treeline30m", args.treeline30m_collection, FINE_CRS, FINE_TRANSFORM),
            ("treeline1km", args.treeline1km_collection, CLIMATE_CRS, CLIMATE_TRANSFORM),
            ("qa30m", args.qa30m_collection, FINE_CRS, FINE_TRANSFORM),
        )
        if spec[0] in selected
    )


def planned_export_records(
    args: argparse.Namespace, mountains: Sequence[Mapping[str, object]]
) -> List[Dict[str, object]]:
    config_hash = configuration_hash(args)
    policies = product_pyramiding_policies()
    specs = selected_product_specs(args)
    records: List[Dict[str, object]] = []
    for mountain in mountains:
        for product, collection, crs, transform in specs:
            child = sanitize_asset_component(
                f"{mountain['mountain_key']}_{args.run_label}_{config_hash[:10]}"
            )
            records.append(
                {
                    **dict(mountain),
                    "product": product,
                    "description": sanitize_asset_component(
                        f"{args.task_prefix}_{args.run_label}_{mountain['mountain_key']}_{product}_{config_hash[:10]}"
                    ),
                    "destination": f"{collection.rstrip('/')}/{child}",
                    "crs": crs,
                    "crs_transform": list(transform),
                    "pyramiding_policy": dict(policies[product]),
                    "configuration_hash": config_hash,
                    "state": "PLANNED",
                    "task_id": None,
                }
            )
    return records


def make_asset_export_task(
    record: Mapping[str, object], bundle: Mapping[str, object]
) -> "ee.batch.Task":
    return ee.batch.Export.image.toAsset(
        image=ee.Image(bundle["images"][str(record["product"])]).set(bundle["metadata"]),
        description=str(record["description"]),
        assetId=str(record["destination"]),
        pyramidingPolicy=dict(record["pyramiding_policy"]),
        region=bundle["context"]["bounds"],
        crs=str(record["crs"]),
        crsTransform=record["crs_transform"],
        maxPixels=1e13,
    )


def serialized_export_expression_bytes(task: "ee.batch.Task", product: str) -> int:
    expression = task.config.get("expression")
    export_options = task.config.get("assetExportOptions")
    if expression is None or not export_options:
        raise ValueError(f"incomplete export task configuration: {product}")
    encoded = expression.serialize(pretty=False, for_cloud_api=True)
    return len(encoded.encode("utf-8"))


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def run_check(args: argparse.Namespace) -> Dict[str, object]:
    auth = initialize_with_adc(args.project)
    table_validation = validate_analysis_table(args)
    plan = resolve_mountain_plan(args)
    required_tiles = resolve_required_tile_ids(args, plan)
    integrity = run_step1_integrity_check(args, required_tiles)
    targets = validate_output_collections(args)
    mountain = choose_check_mountain(plan, args)
    bundle = build_mountain_bundle(args, mountain)
    records = planned_export_records(args, [mountain])
    sizes = []
    for record in records:
        task = make_asset_export_task(record, bundle)
        sizes.append(
            serialized_export_expression_bytes(task, str(record["product"]))
        )
    otsu_report: Dict[str, object] = {
        "status": "deferred_to_export_task",
        "execution_feasibility_verified": False,
    }
    if args.deep_check:
        otsu_report = {
            "status": "evaluated",
            "execution_feasibility_verified": True,
            "thresholds": {
                label: ee.Dictionary(info).getInfo() for label, info in bundle["otsu"].items()
            },
        }
    report = {
        "status": "step2-integrity-and-graph-preflight-passed",
        "exports_started": False,
        "authentication": auth,
        "analysis_table": table_validation,
        "step1_integrity": integrity,
        "check_mountain": dict(mountain),
        "serialized_task_config_bytes": sizes,
        "expected_product_bands": {
            product: expected_product_bands()[product]
            for product in args.export_products
        },
        "otsu": otsu_report,
        "targets": {
            product: {"id": info["id"], "existing_child_count": info["existing_child_count"]}
            for product, info in targets.items()
        },
        "configuration_hash": configuration_hash(args),
    }
    write_json_atomic(args.report_json, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def active_tasks_by_description(tasks: Iterable[Mapping[str, object]]) -> Dict[str, Mapping[str, object]]:
    return {
        str(task["description"]): task
        for task in tasks
        if task.get("description") and task.get("state") in {"READY", "RUNNING"}
    }


def apply_resume_guards(
    records: Sequence[Dict[str, object]],
    targets: Mapping[str, Mapping[str, object]],
    active: Mapping[str, Mapping[str, object]],
    args: argparse.Namespace,
) -> None:
    children: Dict[str, Mapping[str, object]] = {}
    for target in targets.values():
        children.update(target["children"])
    for record in records:
        destination = str(record["destination"])
        description = str(record["description"])
        if destination in children:
            if not args.resume:
                raise ValueError("existing output Asset found; use --resume")
            info = ee.data.getAsset(destination)
            remote_hash = (info.get("properties") or {}).get("configuration_hash")
            if remote_hash != record["configuration_hash"]:
                raise ValueError(f"resume refused for different configuration: {destination}")
            record["state"] = "SKIPPED_EXISTING"
        elif description in active:
            if not args.resume:
                raise ValueError("matching READY/RUNNING task found; use --resume")
            record["state"] = "SKIPPED_ACTIVE"
            record["task_id"] = active[description].get("id")


def enforce_queue_limit(
    records: Sequence[Mapping[str, object]],
    tasks: Sequence[Mapping[str, object]],
    limit: int,
) -> Dict[str, int]:
    ready = sum(task.get("state") == "READY" for task in tasks)
    new = sum(record.get("state") == "PREFLIGHTED" for record in records)
    if ready + new > limit:
        raise ValueError(f"queue safety limit exceeded: READY {ready} + new {new} > {limit}")
    return {"existing_ready": ready, "new_tasks": new, "projected_ready": ready + new}


def start_exports(args: argparse.Namespace) -> Path:
    initialize_with_adc(args.project)
    validate_analysis_table(args)
    plan = resolve_mountain_plan(args)
    required_tiles = resolve_required_tile_ids(args, plan)
    run_step1_integrity_check(args, required_tiles)
    targets = validate_output_collections(args)
    if not plan:
        raise ValueError("selected mountain plan is empty")
    records = planned_export_records(args, plan)
    remote_tasks = ee.data.getTaskList()
    apply_resume_guards(records, targets, active_tasks_by_description(remote_tasks), args)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    registry = args.registry_dir / f"{timestamp}-{args.task_prefix}.json"
    payload: Dict[str, object] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "phase": "PREFLIGHT",
        "project": args.project,
        "analysis_mountains_asset": args.analysis_mountains_asset,
        "step1_manifest": str(args.step1_manifest),
        "configuration_hash": configuration_hash(args),
        "tasks": records,
    }
    write_json_atomic(registry, payload)

    by_mountain = {
        str(mountain["mountain_id"]): [
            record for record in records if record["mountain_id"] == mountain["mountain_id"]
        ]
        for mountain in plan
    }
    for mountain in plan:
        pending = [record for record in by_mountain[str(mountain["mountain_id"])] if record["state"] == "PLANNED"]
        if not pending:
            continue
        bundle = build_mountain_bundle(args, mountain)
        for record in pending:
            task = make_asset_export_task(record, bundle)
            if not task.config:
                raise ValueError(f"empty export configuration: {record['description']}")
            record["state"] = "PREFLIGHTED"
        write_json_atomic(registry, payload)
    payload["queue_projection"] = enforce_queue_limit(
        records, remote_tasks, args.queue_safety_limit
    )
    payload["phase"] = "SUBMITTING"
    write_json_atomic(registry, payload)
    for mountain in plan:
        pending = [record for record in by_mountain[str(mountain["mountain_id"])] if record["state"] == "PREFLIGHTED"]
        if not pending:
            continue
        bundle = build_mountain_bundle(args, mountain)
        for record in pending:
            task = make_asset_export_task(record, bundle)
            try:
                task.start()
                record["task_id"] = task.id
                record["state"] = "SUBMITTED"
                write_json_atomic(registry, payload)
            except Exception as error:
                record["state"] = "FAILED_TO_START"
                record["error"] = f"{type(error).__name__}: {error}"
                payload["phase"] = "SUBMIT_FAILED_RESUMABLE"
                write_json_atomic(registry, payload)
                raise RuntimeError(f"submission stopped; rerun with --resume; see {registry}") from error
    payload["phase"] = "SUBMITTED"
    write_json_atomic(registry, payload)
    print(json.dumps({"registry": str(registry), "task_count": len(records)}, indent=2))
    return registry


def recover_task_ids(registry: Dict[str, object], tasks: Sequence[Mapping[str, object]]) -> int:
    by_description = {
        str(task.get("description")): task
        for task in tasks
        if task.get("description") and (task.get("id") or task.get("task_id"))
    }
    recovered = 0
    for record in registry["tasks"]:
        if record.get("task_id"):
            continue
        remote = by_description.get(str(record.get("description")))
        if remote is not None:
            record["task_id"] = remote.get("id") or remote.get("task_id")
            recovered += 1
    return recovered


def monitor_once(project: str, registry_path: Path) -> None:
    initialize_with_adc(project)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    recovered = recover_task_ids(registry, ee.data.getTaskList())
    identifiers = [record["task_id"] for record in registry["tasks"] if record.get("task_id")]
    states = ee.data.getTaskStatus(identifiers)
    counts: Dict[str, int] = {}
    records = {record.get("task_id"): record for record in registry["tasks"]}
    details: List[Dict[str, object]] = []
    for state in states:
        status = str(state.get("state", "UNKNOWN"))
        counts[status] = counts.get(status, 0) + 1
        record = records.get(state.get("id"), {})
        details.append(
            {
                "task_id": state.get("id"),
                "mountain_id": record.get("mountain_id"),
                "product": record.get("product"),
                "state": status,
                "error_message": state.get("error_message"),
            }
        )
    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "recovered_task_ids": recovered,
        "counts": counts,
        "tasks": details,
    }
    registry["last_monitor"] = result
    write_json_atomic(registry_path, registry)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.max_mountains is not None and args.max_mountains < 1:
        parser.error("--max-mountains must be at least 1")
    if args.mountain_offset < 0:
        parser.error("--mountain-offset must be non-negative")
    if args.mountain_offset and args.max_mountains is None:
        parser.error("--mountain-offset requires --max-mountains")
    if not 1 <= args.queue_safety_limit <= 3000:
        parser.error("--queue-safety-limit must be in [1,3000]")
    if len(set(args.export_products)) != len(args.export_products):
        parser.error("--export-products must not contain duplicates")
    if "treeline1km" in args.export_products and len(args.export_products) != 1:
        parser.error("legacy treeline1km must be selected alone")
    if args.window_radius_m != 150:
        parser.error("--window-radius-m is fixed at 150 (300 m window)")
    if args.median_radius_pixels != 1:
        parser.error("--median-radius-pixels is fixed at 1")
    if args.check or args.export:
        errors = []
        if not args.analysis_mountains_asset:
            errors.append("--analysis-mountains-asset")
        if args.step1_manifest is None:
            errors.append("--step1-manifest")
        if args.export and args.max_mountains is None:
            errors.append("--max-mountains")
        if errors:
            parser.error(("--export" if args.export else "--check") + " requires " + ", ".join(errors))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    if args.dry_run:
        print(json.dumps(resolved_plan(args), ensure_ascii=False, indent=2))
        return 0
    if RUNTIME_IMPORT_ERROR is not None:
        parser.error(f"Earth Engine runtime unavailable: {RUNTIME_IMPORT_ERROR}")
    if args.check:
        run_check(args)
    elif args.export:
        start_exports(args)
    else:
        monitor_once(args.project, args.monitor_once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
