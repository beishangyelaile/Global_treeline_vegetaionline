"""Eight-region Python/geemap observed-treeline extraction (revised).

``--check`` joins the manifest to GMBA Basic, runs one representative tracer,
and validates every planned Asset destination without starting a task.
``--export`` creates a geometry planning grid over the selected GMBA mountains,
groups it into bounded 2-degree export shards, and starts one 30 m, 1 km, and
QA30m Asset export per shard.

The scientific defaults restore the original comparison domain and test:
GMBA intersected with Sayre classes 31/32 in valid 0.25-degree cells, plus a
two-sided 5% t test. Formal exports require a frozen BIO1 threshold instead of
silently recalculating Otsu in every arbitrary export shard. Full-GMBA and
dynamic-shard alternatives remain available as explicit sensitivity modes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# This script is named code.py. Remove its directory before importing geemap so
# IPython imports Python's standard-library ``code`` module instead of this file.
SCRIPT_DIR = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0]).resolve() == SCRIPT_DIR:
    sys.path.pop(0)

RUNTIME_IMPORT_ERROR: Optional[ModuleNotFoundError] = None
try:
    import ee
    import geemap
    import google.auth
    from google.auth.transport.requests import Request
except ModuleNotFoundError as error:  # Keep --dry-run useful before environment setup.
    RUNTIME_IMPORT_ERROR = error
    ee = None  # type: ignore[assignment]
    geemap = None  # type: ignore[assignment]
    google = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]


PROJECT_ROOT = SCRIPT_DIR
FOREST_HEIGHT_2000 = "projects/glad/GLCLU2020/Forest_height_2000"
FOREST_HEIGHT_2020 = "projects/glad/GLCLU2020/Forest_height_2020"
AW3D30 = "JAXA/ALOS/AW3D30/V4_1"
ALOS_LANDFORMS = "CSP/ERGo/1_0/Global/ALOS_landforms"
WORLDCOVER = "ESA/WorldCover/v100"
HIGH_MOUNTAIN_ASSET_DEFAULT = "projects/ee-remote/assets/Alpine/high_mountain"
HIGH_MOUNTAIN_CLASSES = (31, 32)
VALLEY_CLASSES = (41, 42)
FINE_CRS = "EPSG:4326"
FINE_TRANSFORM = [0.00025, 0, -180, 0, -0.00025, 90]
CLIMATE_CRS = "EPSG:4326"
CLIMATE_TRANSFORM = [1 / 120, 0, -180, 0, -1 / 120, 90]
WORKLOAD_TAG = "global-treeline-20260821"
REGION_PROPERTIES = (
    "region_id",
    "region_name",
    "region_subtype",
    "hm31_km2",
    "hm32_km2",
    "hm_area_km2",
    "hm_fraction",
)
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
TWO_SIDED_T_CRITICAL_95 = (
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
    (1000, -1.960),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Resolve parameters without an EE call")
    mode.add_argument("--check", action="store_true", help="Validate the 978-mountain shard plan and run one tracer")
    mode.add_argument("--export", action="store_true", help="Start opt-in Asset exports")
    mode.add_argument("--monitor-once", metavar="REGISTRY", help="Read registered task states once")

    parser.add_argument("--project", default=os.environ.get("EE_PROJECT"))
    parser.add_argument("--gmba-asset", default=os.environ.get("GMBA_ASSET"))
    parser.add_argument("--manifest-asset", default=os.environ.get("MANIFEST_ASSET"))
    parser.add_argument(
        "--high-mountain-asset",
        default=os.environ.get("HIGH_MOUNTAIN_ASSET", HIGH_MOUNTAIN_ASSET_DEFAULT),
    )
    parser.add_argument("--chelsa-bio01", default=os.environ.get("CHELSA_BIO01"))
    parser.add_argument("--treeline30m-collection", default=os.environ.get("TREELINE30M_COLLECTION"))
    parser.add_argument("--treeline1km-collection", default=os.environ.get("TREELINE1KM_COLLECTION"))
    parser.add_argument("--qa30m-collection", default=os.environ.get("QA30M_COLLECTION"))
    parser.add_argument("--region-id", action="append", dest="region_ids")
    parser.add_argument(
        "--max-mountains",
        type=int,
        help="Select the first N mountains after numeric GMBA_V2_ID ascending sort",
    )
    parser.add_argument(
        "--mountain-offset",
        type=int,
        default=0,
        help="Skip this many mountains in numeric GMBA_V2_ID order before --max-mountains",
    )
    parser.add_argument("--expected-region-count", type=int, default=8)
    parser.add_argument("--expected-shard-count", type=int, default=427)
    parser.add_argument("--quarter-grid-deg", type=float, default=0.25)
    parser.add_argument("--export-shard-deg", type=float, default=2.0)
    parser.add_argument("--check-region-id")
    parser.add_argument("--check-shard-id")
    parser.add_argument(
        "--check-bbox", type=float, nargs=4, default=[11.2, 47.1, 11.3, 47.2],
        help="Small representative tracer only; never used by formal exports",
    )
    parser.add_argument("--check-max-features", type=int, default=1)
    parser.add_argument(
        "--deep-check",
        action="store_true",
        help="Explicitly evaluate expensive whole-shard diagnostics during --check",
    )
    parser.add_argument(
        "--pixel-counts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Count pixels in the small tracer map; use --no-pixel-counts for complex tracers",
    )
    parser.add_argument("--aspect-mode", choices=("none", "polar-equator"), default="none")
    parser.add_argument(
        "--domain-mode",
        choices=("sayre-intersection", "gmba-full"),
        default="sayre-intersection",
        help=(
            "Scientific output domain. The recommended main analysis is selected GMBA AND "
            "Sayre classes 31/32; gmba-full reproduces the current region script as a sensitivity run."
        ),
    )
    parser.add_argument("--context-buffer-m", type=float, default=16000)
    parser.add_argument("--temperature-scale", type=float, default=0.1)
    parser.add_argument("--temperature-offset", type=float, default=-273.15)
    parser.add_argument("--apply-quarter-degree-screen", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--minimum-high-mountain-fraction", type=float, default=0.10)
    parser.add_argument("--maximum-tree-cover-fraction", type=float, default=0.95)
    parser.add_argument("--canopy-threshold-m", type=float, default=3.0)
    parser.add_argument("--minimum-forest-patch-ha", type=float, default=0.5)
    parser.add_argument("--hole-max-size-pixels", type=int, default=512)
    parser.add_argument("--hole-border-width-m", type=float, default=90)
    parser.add_argument(
        "--accept-hole-filling-assumption",
        action="store_true",
        help="Required for formal export because the source does not state the maximum hole size.",
    )
    parser.add_argument("--patch-count-cap", type=int, default=64)
    parser.add_argument("--median-radius-pixels", type=float, default=1)
    parser.add_argument("--window-radius-m", type=float, default=150)
    parser.add_argument("--minimum-samples-per-group", type=int, default=5)
    parser.add_argument("--t-test-variance", choices=("welch", "pooled"), default="welch")
    parser.add_argument(
        "--t-test-alternative", choices=("two-sided", "less"), default="two-sided",
        help="two-sided restores the original reproduction; less is a directional sensitivity test.",
    )
    parser.add_argument("--aspect-half-width-deg", type=float, default=45)
    parser.add_argument("--minimum-slope-deg", type=float, default=5)
    parser.add_argument("--equator-buffer-deg", type=float, default=0.1)
    parser.add_argument("--strict-aw3d-native-only", action="store_true")
    parser.add_argument(
        "--otsu-scope",
        choices=("global-fixed", "region-fixed", "shard-dynamic"),
        default="global-fixed",
        help=(
            "Formal threshold scope. Fixed modes prevent export-tile seams; shard-dynamic is retained "
            "only as an explicitly acknowledged sensitivity mode."
        ),
    )
    parser.add_argument("--temperature-threshold-c", type=float)
    parser.add_argument(
        "--region-thresholds-json", type=Path,
        help='JSON mapping such as {"R1_WET_HIMALAYA": 1.2, ...} for region-fixed mode.',
    )
    parser.add_argument("--allow-dynamic-shard-otsu", action="store_true")
    parser.add_argument("--otsu-max-pixels", type=float, default=1e8)
    parser.add_argument("--tile-scale", type=float, default=4)
    parser.add_argument("--report-json", type=Path, default=SCRIPT_DIR / "region_check_console.json")
    parser.add_argument("--map-html", type=Path, default=SCRIPT_DIR / "region_check_map.html")
    parser.add_argument("--export-qa", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--max-export-tasks",
        type=int,
        help=(
            "Limit the deterministic export plan to its first N product tasks "
            "(region_id, tile_y, tile_x, then 30m/1km/QA order)"
        ),
    )
    parser.add_argument("--task-prefix", default="treeline_region")
    parser.add_argument(
        "--run-label", default="main_v2",
        help="Version label embedded in child Asset IDs; change it for every sensitivity run.",
    )
    parser.add_argument("--geometry-max-error-m", type=float, default=1000)
    parser.add_argument("--overwrite-assets", action="store_true")
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip completed destinations and matching READY/RUNNING tasks, then submit only missing work",
    )
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
    if not args.manifest_asset:
        missing.append("eight-region manifest (--manifest-asset or MANIFEST_ASSET)")
    if args.domain_mode == "sayre-intersection" and not args.high_mountain_asset:
        missing.append("Sayre high-mountain asset (--high-mountain-asset or HIGH_MOUNTAIN_ASSET)")
    if not args.chelsa_bio01:
        missing.append("CHELSA V2.1 BIO1 asset (--chelsa-bio01 or CHELSA_BIO01)")
    if not args.treeline30m_collection:
        missing.append("30 m ImageCollection (--treeline30m-collection or TREELINE30M_COLLECTION)")
    if not args.treeline1km_collection:
        missing.append("1 km ImageCollection (--treeline1km-collection or TREELINE1KM_COLLECTION)")
    if not args.qa30m_collection:
        missing.append("QA30m ImageCollection (--qa30m-collection or QA30M_COLLECTION)")
    if export and not args.accept_hole_filling_assumption:
        missing.append("explicit acceptance of the undocumented maximum hole size")
    if export and args.otsu_scope == "global-fixed" and args.temperature_threshold_c is None:
        missing.append("frozen global BIO1 threshold (--temperature-threshold-c)")
    if export and args.otsu_scope == "region-fixed":
        if args.region_thresholds_json is None:
            missing.append("per-region BIO1 threshold file (--region-thresholds-json)")
        elif not args.region_thresholds_json.is_file():
            missing.append(f"existing per-region BIO1 threshold file ({args.region_thresholds_json})")
    if export and args.otsu_scope == "shard-dynamic" and not args.allow_dynamic_shard_otsu:
        missing.append("explicit acceptance of tile-dependent Otsu (--allow-dynamic-shard-otsu)")
    return missing


def resolved_plan(args: argparse.Namespace) -> Dict[str, object]:
    missing = missing_requirements(args, export=args.export)
    export_missing = missing_requirements(args, export=True)
    requested_region_count = len(args.region_ids) if args.region_ids else args.expected_region_count
    product_count = 3 if args.export_qa else 2
    online_subset = bool(
        args.region_ids or args.max_mountains is not None or args.mountain_offset
    )
    expected_task_count: object
    if online_subset:
        expected_task_count = "resolved online"
    else:
        expected_task_count = args.expected_shard_count * product_count
    if args.max_export_tasks is not None:
        expected_task_count = (
            min(expected_task_count, args.max_export_tasks)
            if isinstance(expected_task_count, int)
            else f"at most {args.max_export_tasks}; resolved online"
        )
    return {
        "mode": "dry-run" if args.dry_run else "check" if args.check else "export",
        "project": args.project,
        "auth": "Google Application Default Credentials",
        "region_source": args.manifest_asset,
        "region_ids": args.region_ids or "all distinct manifest region_id values",
        "max_mountains": args.max_mountains,
        "mountain_offset": args.mountain_offset,
        "mountain_selection_order": "GMBA_V2_ID ascending (numeric)",
        "expected_region_count": args.expected_region_count,
        "quarter_grid_deg": args.quarter_grid_deg,
        "export_shard_deg": args.export_shard_deg,
        "expected_shard_count": (
            args.expected_shard_count if not online_subset else "resolved online"
        ),
        "expected_export_task_count": expected_task_count,
        "max_export_tasks": args.max_export_tasks,
        "aspect_mode": args.aspect_mode,
        "domain_mode": args.domain_mode,
        "run_label": args.run_label,
        "preflight": {
            "deep_check": args.deep_check,
            "tracer_pixel_counts": args.pixel_counts,
            "processing_support": "selected GMBA geometry plus context buffer, intersected with processing rectangle",
            "export_region": "2-degree rectangular shard",
        },
        "fine_grid": {"crs": FINE_CRS, "transform": FINE_TRANSFORM},
        "climate_grid": {"crs": CLIMATE_CRS, "transform": CLIMATE_TRANSFORM},
        "output": {
            "check_report": str(args.report_json),
            "check_map": str(args.map_html),
            "treeline30m_collection": args.treeline30m_collection,
            "treeline1km_collection": args.treeline1km_collection,
            "qa30m_collection": args.qa30m_collection,
        },
        "temperature_transform": {
            "formula": "temperature_c = raw * scale + offset",
            "scale": args.temperature_scale,
            "offset": args.temperature_offset,
            "otsu_scope": args.otsu_scope,
            "global_threshold_c": args.temperature_threshold_c,
            "region_thresholds_json": (
                str(args.region_thresholds_json) if args.region_thresholds_json else None
            ),
        },
        "assets": {
            "gmba": args.gmba_asset,
            "manifest": args.manifest_asset,
            "sayre_high_mountain": (
                args.high_mountain_asset if args.domain_mode == "sayre-intersection" else None
            ),
            "forest_height_2000": FOREST_HEIGHT_2000,
            "forest_height_2020": FOREST_HEIGHT_2020,
            "chelsa_bio01": args.chelsa_bio01,
            "aw3d30": AW3D30,
            "alos_landforms": ALOS_LANDFORMS,
            "worldcover": WORLDCOVER,
        },
        "ready": not missing,
        "missing_requirements": missing,
        "ready_for_formal_export": not export_missing,
        "missing_export_requirements": export_missing,
        "export_guard": "task.start() is reachable only with --export after online shard resolution",
        "overwrite_assets": args.overwrite_assets,
        "resume": args.resume,
        "scientific_choices": {
            "sayre_classes": list(HIGH_MOUNTAIN_CLASSES),
            "minimum_high_mountain_fraction": args.minimum_high_mountain_fraction,
            "maximum_tree_cover_fraction": args.maximum_tree_cover_fraction,
            "t_test_variance": args.t_test_variance,
            "t_test_alternative": args.t_test_alternative,
            "hole_max_size_pixels": args.hole_max_size_pixels,
            "hole_filling_assumption_accepted": args.accept_hole_filling_assumption,
        },
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


def sanitize_asset_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    if not component:
        raise ValueError(f"asset component is empty after sanitizing: {value!r}")
    return component


def signed_grid_component(value: int) -> str:
    return f"p{value:03d}" if value >= 0 else f"m{abs(value):03d}"


def shard_key(region_id: str, tile_x: int, tile_y: int) -> str:
    return sanitize_asset_component(
        f"{region_id}_x{signed_grid_component(tile_x)}_y{signed_grid_component(tile_y)}"
    )


def child_asset_id(
    collection_id: str,
    region_id: str,
    tile_x: int,
    tile_y: int,
    aspect_mode: str,
    run_label: str,
) -> str:
    child = sanitize_asset_component(
        f"{shard_key(region_id, tile_x, tile_y)}_{aspect_mode.replace('-', '_')}_{run_label}"
    )
    return f"{collection_id.rstrip('/')}/{child}"


def shard_bounds(tile_x: int, tile_y: int, shard_degrees: float) -> Tuple[float, float, float, float]:
    west = tile_x * shard_degrees
    south = tile_y * shard_degrees
    return west, south, west + shard_degrees, south + shard_degrees


def add_manifest_join_key(feature: ee.Feature) -> ee.Feature:
    feature = ee.Feature(feature)
    key = ee.Number(feature.get("GMBA_V2_ID")).format("%.0f")
    return feature.set("gmba_join_id", key)


def select_manifest_gmba(args: argparse.Namespace) -> Tuple[ee.FeatureCollection, ee.FeatureCollection]:
    gmba_basic = (
        ee.FeatureCollection(args.gmba_asset)
        .filter(ee.Filter.eq("MapUnit", "Basic"))
        .map(add_manifest_join_key)
    )
    manifest = ee.FeatureCollection(args.manifest_asset).map(add_manifest_join_key)
    joined = ee.FeatureCollection(
        ee.Join.saveFirst("manifest_row").apply(
            gmba_basic,
            manifest,
            ee.Filter.equals(leftField="gmba_join_id", rightField="gmba_join_id"),
        )
    ).filter(ee.Filter.notNull(["manifest_row"]))

    def copy_manifest_properties(feature: ee.Feature) -> ee.Feature:
        feature = ee.Feature(feature)
        row = ee.Feature(feature.get("manifest_row"))
        return feature.copyProperties(row, list(REGION_PROPERTIES))

    return joined.map(copy_manifest_properties), manifest


def select_analysis_mountains(
    selected: ee.FeatureCollection,
    region_ids: Sequence[str],
    args: argparse.Namespace,
) -> ee.FeatureCollection:
    scoped = selected.filter(ee.Filter.inList("region_id", list(region_ids)))
    if args.max_mountains is not None:
        ordered = scoped.sort("GMBA_V2_ID")
        scoped = ee.FeatureCollection(
            ordered.toList(args.max_mountains, args.mountain_offset)
        )
    return scoped


def resolve_region_selection(
    args: argparse.Namespace,
) -> Tuple[ee.FeatureCollection, List[str], Dict[str, object]]:
    selected, manifest = select_manifest_gmba(args)
    manifest_count = int(manifest.size().getInfo())
    selected_count = int(selected.size().getInfo())
    if selected_count != manifest_count:
        raise ValueError(
            f"manifest/GMBA join lost rows: manifest={manifest_count}, matched={selected_count}"
        )
    histogram = {
        str(key): int(value)
        for key, value in selected.aggregate_histogram("region_id").getInfo().items()
    }
    all_region_ids = sorted(histogram)
    if len(all_region_ids) != args.expected_region_count:
        raise ValueError(
            f"expected {args.expected_region_count} manifest regions, found {len(all_region_ids)}: "
            f"{all_region_ids}"
        )
    if args.region_ids:
        requested = list(dict.fromkeys(args.region_ids))
        unknown = sorted(set(requested) - set(all_region_ids))
        if unknown:
            raise ValueError(f"requested region IDs are absent from manifest: {unknown}")
        region_ids = requested
    else:
        region_ids = all_region_ids
    analysis_selected = select_analysis_mountains(selected, region_ids, args)
    analysis_count = int(analysis_selected.size().getInfo())
    if args.max_mountains is not None and analysis_count != args.max_mountains:
        raise ValueError(
            f"requested {args.max_mountains} mountains, resolved {analysis_count}"
        )
    analysis_histogram = {
        str(key): int(value)
        for key, value in analysis_selected.aggregate_histogram("region_id").getInfo().items()
    }
    analysis_region_ids = [
        region_id for region_id in region_ids if region_id in analysis_histogram
    ]
    analysis_gmba_v2_ids = analysis_selected.aggregate_array("GMBA_V2_ID").getInfo()
    return analysis_selected, analysis_region_ids, {
        "manifest_count": manifest_count,
        "matched_gmba_basic_count": selected_count,
        "all_region_ids": all_region_ids,
        "selected_region_ids": analysis_region_ids,
        "count_by_region": histogram,
        "analysis_mountain_count": analysis_count,
        "analysis_count_by_region": analysis_histogram,
        "analysis_gmba_v2_ids": analysis_gmba_v2_ids,
        "mountain_selection_order": "GMBA_V2_ID ascending (numeric)",
        "mountain_offset": args.mountain_offset,
        "mountain_ordinal_range": (
            [args.mountain_offset + 1, args.mountain_offset + analysis_count]
            if args.max_mountains is not None else None
        ),
    }


def target_collection_ids(args: argparse.Namespace) -> Dict[str, str]:
    return {
        "treeline30m": args.treeline30m_collection,
        "treeline1km": args.treeline1km_collection,
        "qa30m": args.qa30m_collection,
    }


def validate_target_collections(args: argparse.Namespace) -> Dict[str, Dict[str, object]]:
    summaries: Dict[str, Dict[str, object]] = {}
    for product, collection_id in target_collection_ids(args).items():
        info = ee.data.getAsset(collection_id)
        if info.get("type") != "IMAGE_COLLECTION":
            raise ValueError(
                f"target for {product} must be IMAGE_COLLECTION, got {info.get('type')}: {collection_id}"
            )
        children = ee.data.listAssets({"parent": collection_id}).get("assets", [])
        summaries[product] = {
            "id": collection_id,
            "type": info.get("type"),
            "existing_child_count": len(children),
            "existing_child_ids": [child.get("id") or child.get("name") for child in children],
        }
    return summaries


def active_tasks_by_description() -> Dict[str, Dict[str, object]]:
    active_states = {"READY", "RUNNING"}
    return {
        str(task.get("description")): task
        for task in ee.data.getTaskList()
        if task.get("state") in active_states and task.get("description")
    }


def apply_resume_guards(
    records: Sequence[Dict[str, object]],
    existing_ids: Sequence[str],
    active_by_description: Mapping[str, Mapping[str, object]],
    resume: bool,
    overwrite_assets: bool,
) -> None:
    existing_set = set(existing_ids)
    existing_records = [record for record in records if record["destination"] in existing_set]
    active_records = [
        record for record in records if record["description"] in active_by_description
    ]
    if existing_records and not (resume or overwrite_assets):
        raise ValueError(
            "refusing to overwrite existing target assets; pass --resume to skip them or "
            "--overwrite-assets only after review: "
            + ", ".join(str(record["destination"]) for record in existing_records)
        )
    if active_records and not resume:
        raise ValueError(
            "matching READY/RUNNING tasks already exist; pass --resume to skip them: "
            + ", ".join(str(record["description"]) for record in active_records)
        )
    if not resume:
        return
    for record in existing_records:
        record["state"] = "SKIPPED_EXISTING"
    for record in active_records:
        active = active_by_description[str(record["description"])]
        record["state"] = "SKIPPED_ACTIVE"
        record["task_id"] = active.get("id") or active.get("task_id")


def asset_exists(asset_id: str) -> bool:
    try:
        ee.data.getAsset(asset_id)
        return True
    except ee.EEException as error:
        message = str(error).lower()
        if "not found" in message or "does not exist" in message:
            return False
        raise


def quarter_grid_projection(args: argparse.Namespace) -> ee.Projection:
    degree = args.quarter_grid_deg
    return ee.Projection(FINE_CRS, [degree, 0, -180, 0, -degree, 90])


def region_quarter_cells(
    selected_all: ee.FeatureCollection, region_id: str, args: argparse.Namespace
) -> ee.FeatureCollection:
    selected_region = selected_all.filter(ee.Filter.eq("region_id", region_id))
    geometry = selected_region.geometry(maxError=args.geometry_max_error_m)
    cells = geometry.coveringGrid(quarter_grid_projection(args))

    def add_parent_key(feature: ee.Feature) -> ee.Feature:
        feature = ee.Feature(feature)
        coordinates = feature.geometry().centroid(args.geometry_max_error_m).coordinates()
        tile_x = ee.Number(coordinates.get(0)).divide(args.export_shard_deg).floor()
        tile_y = ee.Number(coordinates.get(1)).divide(args.export_shard_deg).floor()
        key = (
            ee.String(region_id).cat("|")
            .cat(tile_x.format("%.0f")).cat("|")
            .cat(tile_y.format("%.0f"))
        )
        return feature.set(
            {"region_id": region_id, "tile_x": tile_x, "tile_y": tile_y, "shard_plan_key": key}
        )

    return ee.FeatureCollection(cells.map(add_parent_key))


def resolve_shard_plan(
    selected_all: ee.FeatureCollection, region_ids: Sequence[str], args: argparse.Namespace
) -> List[Dict[str, object]]:
    plan: List[Dict[str, object]] = []
    for requested_region_id in region_ids:
        histogram = region_quarter_cells(
            selected_all, requested_region_id, args
        ).aggregate_histogram("shard_plan_key").getInfo()
        for key, count in histogram.items():
            region_id, tile_x_text, tile_y_text = str(key).split("|")
            tile_x = int(tile_x_text)
            tile_y = int(tile_y_text)
            plan.append(
                {
                    "region_id": region_id,
                    "tile_x": tile_x,
                    "tile_y": tile_y,
                    "shard_id": shard_key(region_id, tile_x, tile_y),
                    "quarter_cell_count": int(count),
                    "bounds": list(shard_bounds(tile_x, tile_y, args.export_shard_deg)),
                }
            )
    plan.sort(key=lambda item: (str(item["region_id"]), int(item["tile_y"]), int(item["tile_x"])))
    if (
        not args.region_ids
        and args.max_mountains is None
        and len(plan) != args.expected_shard_count
    ):
        raise ValueError(
            f"expected {args.expected_shard_count} export shards, resolved {len(plan)}"
        )
    return plan


def planned_export_records(
    args: argparse.Namespace, shard_plan: Sequence[Mapping[str, object]]
) -> List[Dict[str, object]]:
    product_specs = [
        (
            "treeline30m", args.treeline30m_collection, FINE_CRS, FINE_TRANSFORM,
            {".default": "mean"},
        ),
        (
            "treeline1km", args.treeline1km_collection, CLIMATE_CRS, CLIMATE_TRANSFORM,
            {".default": "mean"},
        ),
    ]
    if args.export_qa:
        qa_policy = {".default": "mode", "dem_stk": "mean"}
        if args.aspect_mode == "polar-equator":
            qa_policy.update(
                {"aspect_deg": "sample", "slope_deg": "mean"}
            )
        product_specs.append(
            ("qa30m", args.qa30m_collection, FINE_CRS, FINE_TRANSFORM, qa_policy)
        )
    records: List[Dict[str, object]] = []
    for shard in shard_plan:
        region_id = str(shard["region_id"])
        tile_x = int(shard["tile_x"])
        tile_y = int(shard["tile_y"])
        current_shard_key = str(shard["shard_id"])
        for product, collection_id, crs, transform, pyramiding_policy in product_specs:
            description = sanitize_asset_component(
                f"{args.task_prefix}_{args.run_label}_{current_shard_key}_{args.aspect_mode}_{product}"
            )
            records.append(
                {
                    "region_id": region_id,
                    "tile_x": tile_x,
                    "tile_y": tile_y,
                    "shard_id": current_shard_key,
                    "quarter_cell_count": int(shard["quarter_cell_count"]),
                    "bounds": list(shard["bounds"]),
                    "product": product,
                    "description": description,
                    "task_id": None,
                    "state": "PLANNED",
                    "destination": child_asset_id(
                        collection_id, region_id, tile_x, tile_y, args.aspect_mode,
                        args.run_label,
                    ),
                    "crs": crs,
                    "crs_transform": transform,
                    "pyramiding_policy": dict(pyramiding_policy),
                }
            )
    if args.max_export_tasks is not None:
        return records[: args.max_export_tasks]
    return records


def expected_product_bands(args: argparse.Namespace) -> Dict[str, List[str]]:
    if args.aspect_mode == "none":
        return {
            "treeline30m": ["treeline_all_2000_m", "treeline_all_2020_m"],
            "treeline1km": [
                "treeline_2000_mean_m",
                "treeline_2020_mean_m",
                "shift_2000_2020_m_per_year",
            ],
            "qa30m": [
                "analysis_domain", "forest_2000", "forest_2020", "edge_2000",
                "edge_2020", "upper_2000", "upper_2020", "cold_zone",
                "non_valley", "dem_msk", "dem_stk",
            ],
        }
    return {
        "treeline30m": [
            "treeline_polar_2000_m", "treeline_equator_2000_m",
            "treeline_polar_2020_m", "treeline_equator_2020_m",
        ],
        "treeline1km": [
            "polar_2000_mean_m", "equator_2000_mean_m", "polar_2020_mean_m",
            "equator_2020_mean_m", "polar_shift_m_per_year",
            "equator_shift_m_per_year",
        ],
        "qa30m": [
            "analysis_domain", "aspect_class", "aspect_deg", "slope_deg",
            "forest_2000", "forest_2020", "upper_polar_2000",
            "upper_equator_2000", "upper_polar_2020", "upper_equator_2020",
            "cold_zone", "dem_msk", "dem_stk",
        ],
    }


def shard_geometry(shard: Mapping[str, object]) -> ee.Geometry:
    return ee.Geometry.Rectangle(list(shard["bounds"]), proj=FINE_CRS, geodesic=False)


def build_processing_support(
    selected_gmba: ee.FeatureCollection,
    processing_rectangle: ee.Geometry,
    args: argparse.Namespace,
) -> ee.Geometry:
    gmba_geometry = selected_gmba.geometry(maxError=args.geometry_max_error_m)
    return gmba_geometry.buffer(
        args.context_buffer_m, args.geometry_max_error_m
    ).intersection(processing_rectangle, args.geometry_max_error_m)


def shard_counts_by_region(shard_plan: Sequence[Mapping[str, object]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for shard in shard_plan:
        region_id = str(shard["region_id"])
        counts[region_id] = counts.get(region_id, 0) + 1
    return counts


def quarter_degree_screen(
    processing_region: ee.Geometry, args: argparse.Namespace
) -> Dict[str, ee.Image]:
    grid_projection = quarter_grid_projection(args)
    worldcover_tree = (
        ee.ImageCollection(WORLDCOVER).first().select("Map").eq(10)
        .clip(processing_region).unmask(0)
    )

    if args.domain_mode == "sayre-intersection":
        sayre = ee.Image(args.high_mountain_asset).select("b1")
        high_mountain = (
            class_mask(sayre, HIGH_MOUNTAIN_CLASSES).unmask(0)
            .clip(processing_region).rename("sayre_high_mountain")
        )
        intermediate_projection = sayre.projection()
        high_fraction = (
            high_mountain.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=65535)
            .reproject(grid_projection).clip(processing_region)
            .rename("high_mountain_fraction")
        )
    else:
        high_mountain = ee.Image.constant(1).clip(processing_region).rename("sayre_high_mountain")
        high_fraction = ee.Image.constant(1).clip(processing_region).rename(
            "high_mountain_fraction"
        )
        intermediate_projection = ee.Projection(CLIMATE_CRS, CLIMATE_TRANSFORM)

    tree_at_intermediate_scale = (
        worldcover_tree.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=16384)
        .reproject(intermediate_projection).clip(processing_region)
    )
    tree_fraction = (
        tree_at_intermediate_scale.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=65535)
        .reproject(grid_projection).clip(processing_region)
        .rename("tree_cover_fraction")
    )
    valid_grid = tree_fraction.lte(args.maximum_tree_cover_fraction)
    if args.domain_mode == "sayre-intersection":
        valid_grid = valid_grid.And(
            high_fraction.gte(args.minimum_high_mountain_fraction)
        )
    return {
        "valid_grid": valid_grid.rename("valid_quarter_degree_cell"),
        "high_mountain": high_mountain,
        "high_fraction": high_fraction,
        "tree_fraction": tree_fraction,
    }


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


def otsu_threshold_ee(
    histogram: ee.ComputedObject, scale: float, offset: float
) -> ee.Dictionary:
    fallback = ee.Dictionary({"histogram": [1, 1], "bucketMeans": [0, 1]})
    is_null = ee.Algorithms.IsEqual(histogram, None)
    raw_dictionary = ee.Dictionary(ee.Algorithms.If(is_null, fallback, histogram))
    raw_counts = ee.Array(raw_dictionary.get("histogram"))
    raw_means = ee.Array(raw_dictionary.get("bucketMeans"))
    raw_bucket_count = ee.Number(raw_means.length().get([0]))
    raw_total = ee.Number(raw_counts.reduce(ee.Reducer.sum(), [0]).get([0]))
    reported_bucket_count = ee.Number(ee.Algorithms.If(is_null, 0, raw_bucket_count))
    reported_total = ee.Number(ee.Algorithms.If(is_null, 0, raw_total))
    nonempty_buckets = ee.Number(raw_counts.gt(0).reduce(ee.Reducer.sum(), [0]).get([0]))
    valid = (
        ee.Number(ee.Algorithms.If(is_null, 0, 1))
        .multiply(raw_bucket_count.gte(2))
        .multiply(nonempty_buckets.gte(2))
    )
    safe_dictionary = ee.Dictionary(ee.Algorithms.If(valid.eq(1), raw_dictionary, fallback))
    counts = ee.Array(safe_dictionary.get("histogram"))
    means = ee.Array(safe_dictionary.get("bucketMeans"))
    size = ee.Number(means.length().get([0]))
    total = ee.Number(counts.reduce(ee.Reducer.sum(), [0]).get([0]))
    weighted_sum = ee.Number(means.multiply(counts).reduce(ee.Reducer.sum(), [0]).get([0]))
    global_mean = weighted_sum.divide(total)

    def between_class_score(split: ee.Number) -> ee.Number:
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

    scores = ee.Array(ee.List.sequence(1, size.subtract(1)).map(between_class_score))
    candidate_thresholds = means.slice(0, 0, size.subtract(1))
    threshold_raw = ee.Number(candidate_thresholds.sort(scores).get([-1]))
    threshold_c = threshold_raw.multiply(scale).add(offset)
    return ee.Dictionary(
        {
            "valid": valid,
            "threshold_raw": threshold_raw,
            "threshold_c": threshold_c,
            "candidate_sample_count": reported_total,
            "histogram_bucket_count": reported_bucket_count,
        }
    )


def convert_raw_temperature(raw_value: float, scale: float, offset: float) -> float:
    return raw_value * scale + offset


def load_fixed_thresholds(
    args: argparse.Namespace,
    region_ids: Sequence[str],
    *,
    fallback_threshold_c: Optional[float] = None,
) -> Dict[str, float]:
    """Resolve immutable thresholds; a check-only fallback never authorizes export."""
    if args.otsu_scope == "shard-dynamic":
        return {}
    if args.otsu_scope == "global-fixed":
        value = args.temperature_threshold_c
        if value is None:
            value = fallback_threshold_c
        if value is None or not math.isfinite(float(value)):
            raise ValueError("global-fixed Otsu requires a finite --temperature-threshold-c")
        return {str(region_id): float(value) for region_id in region_ids}

    payload: object = {}
    if args.region_thresholds_json is not None and args.region_thresholds_json.is_file():
        payload = json.loads(args.region_thresholds_json.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("thresholds_c"), dict):
        payload = payload["thresholds_c"]
    if not isinstance(payload, dict):
        raise ValueError("region threshold JSON must be an object or contain a thresholds_c object")
    thresholds: Dict[str, float] = {}
    for region_id in region_ids:
        value = payload.get(region_id)
        if value is None:
            value = fallback_threshold_c
        if value is None or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"missing finite Celsius threshold for region_id={region_id}")
        thresholds[str(region_id)] = float(value)
    return thresholds


def fixed_threshold_dictionary(
    args: argparse.Namespace, threshold_c: float
) -> ee.Dictionary:
    threshold_raw = (float(threshold_c) - args.temperature_offset) / args.temperature_scale
    return ee.Dictionary(
        {
            "valid": 1,
            "threshold_raw": threshold_raw,
            "threshold_c": float(threshold_c),
            "candidate_sample_count": -1,
            "histogram_bucket_count": -1,
            "source": args.otsu_scope,
        }
    )


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


def lower_tail_t_critical_image(df: ee.Image, alternative: str) -> ee.Image:
    table = (
        TWO_SIDED_T_CRITICAL_95
        if alternative == "two-sided"
        else ONE_SIDED_T_CRITICAL_95
    )
    critical = ee.Image.constant(table[0][1])
    for minimum_df, value in table[1:]:
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
        statistic.lt(
            lower_tail_t_critical_image(degrees_of_freedom, args.t_test_alternative)
        )
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


def build_common(
    args: argparse.Namespace,
    batch_region: ee.Geometry,
    processing_rectangle: ee.Geometry,
    processing_support: ee.Geometry,
    selected_gmba: Optional[ee.FeatureCollection] = None,
) -> Dict[str, object]:
    if selected_gmba is None:
        selected_gmba = select_manifest_gmba(args)[0].filterBounds(processing_rectangle)
    fine_projection = ee.Projection(FINE_CRS, FINE_TRANSFORM)
    gmba_mask = (
        ee.Image(0).byte().paint(selected_gmba, 1).setDefaultProjection(fine_projection)
        .clip(processing_rectangle).rename("gmba_selected").unmask(0)
    )
    # Fractions use complete 0.25-degree cells, not GMBA-clipped denominators.
    screens = quarter_degree_screen(processing_rectangle, args)
    grid_screen = (
        ee.Image(screens["valid_grid"])
        if args.apply_quarter_degree_screen
        else ee.Image.constant(1).clip(processing_rectangle)
    )
    domain = gmba_mask.And(grid_screen)
    if args.domain_mode == "sayre-intersection":
        domain = domain.And(ee.Image(screens["high_mountain"]))
    domain = domain.selfMask().rename("analysis_domain")
    aw3d = build_aw3d(processing_support, args.strict_aw3d_native_only)
    dem = aw3d.select("elevation").toFloat()
    landforms = ee.Image(ALOS_LANDFORMS).select("constant").clip(processing_support)
    nonvalley = class_mask(landforms, VALLEY_CLASSES).Not().rename("non_valley")
    forest2020 = clean_forest(FOREST_HEIGHT_2020, processing_support, args)
    preliminary_edges2020 = forest_edges(forest2020, domain, args).And(nonvalley)
    return {
        "gmba": selected_gmba,
        "selected_gmba": selected_gmba,
        "domain": domain,
        "valid_quarter_degree_grid": grid_screen,
        "sayre_high_mountain": screens["high_mountain"],
        "high_mountain_fraction": screens["high_fraction"],
        "tree_cover_fraction": screens["tree_fraction"],
        "aw3d": aw3d,
        "dem": dem,
        "nonvalley": nonvalley,
        "forest2020": forest2020,
        "preliminary_edges2020": preliminary_edges2020,
    }


def build_products(
    args: argparse.Namespace,
    common: Mapping[str, object],
    processing_support: ee.Geometry,
    threshold_c: float,
    temperature: ee.Image,
) -> Dict[str, object]:
    domain = ee.Image(common["domain"])
    dem = ee.Image(common["dem"])
    nonvalley = ee.Image(common["nonvalley"])
    forest2020 = ee.Image(common["forest2020"])
    cold_mask = temperature.lte(threshold_c).rename("cold_zone")
    if args.aspect_mode == "none":
        group_mask = ee.Image.constant(1).clip(processing_support)
        result2000 = extract_treeline(
            FOREST_HEIGHT_2000, 2000, "all", group_mask, domain, dem, nonvalley,
            cold_mask, processing_support, args
        )
        result2020 = extract_treeline(
            FOREST_HEIGHT_2020, 2020, "all", group_mask, domain, dem, nonvalley,
            cold_mask, processing_support, args, forest2020
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
            .toFloat().updateMask(domain)
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
        cold_mask, processing_support, args
    )
    equator2000 = extract_treeline(
        FOREST_HEIGHT_2000, 2000, "equator", groups["equator"], domain, dem, nonvalley,
        cold_mask, processing_support, args, polar2000["forest"]
    )
    polar2020 = extract_treeline(
        FOREST_HEIGHT_2020, 2020, "polar", groups["polar"], domain, dem, nonvalley,
        cold_mask, processing_support, args, forest2020
    )
    equator2020 = extract_treeline(
        FOREST_HEIGHT_2020, 2020, "equator", groups["equator"], domain, dem, nonvalley,
        cold_mask, processing_support, args, forest2020
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
        .toFloat().updateMask(domain)
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


def build_shard_export_bundle(
    args: argparse.Namespace,
    selected_all: ee.FeatureCollection,
    shard: Mapping[str, object],
    thresholds_by_region: Optional[Mapping[str, float]] = None,
) -> Dict[str, object]:
    shard_id = str(shard["shard_id"])
    region_id = str(shard["region_id"])
    batch_region = shard_geometry(shard)
    processing_rectangle = batch_region.buffer(
        args.context_buffer_m, args.geometry_max_error_m
    )
    selected_shard = (
        selected_all.filter(ee.Filter.eq("region_id", region_id))
        .filterBounds(processing_rectangle)
    )
    processing_support = build_processing_support(
        selected_shard, processing_rectangle, args
    )
    common = build_common(
        args, batch_region, processing_rectangle, processing_support, selected_shard
    )
    if args.otsu_scope == "shard-dynamic":
        temperature_info = temperature_graph(
            ee.Image(common["preliminary_edges2020"]), batch_region, args
        )
        otsu = otsu_threshold_ee(
            temperature_info["histogram"], args.temperature_scale, args.temperature_offset
        ).set("source", "shard-dynamic")
    else:
        if thresholds_by_region is None or region_id not in thresholds_by_region:
            raise ValueError(f"no frozen temperature threshold resolved for {region_id}")
        raw = ee.Image(args.chelsa_bio01).select([0]).rename("bio01_raw")
        temperature_info = {
            "raw": raw,
            "temperature": raw.multiply(args.temperature_scale)
            .add(args.temperature_offset).rename("temperature_c"),
            "range_qa": None,
            "histogram": None,
        }
        otsu = fixed_threshold_dictionary(args, thresholds_by_region[region_id])
    products = build_products(
        args, common, processing_support, ee.Number(otsu.get("threshold_c")),
        ee.Image(temperature_info["temperature"]),
    )
    otsu_valid_mask = ee.Image.constant(ee.Number(otsu.get("valid"))).eq(1)
    images = {
        "treeline30m": ee.Image(products["treeline30m"]).updateMask(otsu_valid_mask).clip(batch_region).toFloat(),
        "treeline1km": ee.Image(products["treeline1km"]).updateMask(otsu_valid_mask).clip(batch_region).toFloat(),
        "qa30m": ee.Image(products["qa30m"]).updateMask(otsu_valid_mask).clip(batch_region).toFloat(),
    }
    metadata = {
        "region_id": region_id,
        "shard_id": shard_id,
        "tile_x": int(shard["tile_x"]),
        "tile_y": int(shard["tile_y"]),
        "quarter_cell_count": int(shard["quarter_cell_count"]),
        "gmba_feature_count": selected_shard.size(),
        "gmba_v2_ids": selected_shard.aggregate_array("GMBA_V2_ID"),
        "aspect_mode": args.aspect_mode,
        "run_label": args.run_label,
        "domain_mode": args.domain_mode,
        "sayre_classes": "31,32" if args.domain_mode == "sayre-intersection" else "not_applied",
        "minimum_high_mountain_fraction": args.minimum_high_mountain_fraction,
        "maximum_tree_cover_fraction": args.maximum_tree_cover_fraction,
        "t_test_variance": args.t_test_variance,
        "t_test_alternative": args.t_test_alternative,
        "otsu_scope": args.otsu_scope,
        "otsu_source": otsu.get("source"),
        "otsu_valid": otsu.get("valid"),
        "otsu_candidate_sample_count": otsu.get("candidate_sample_count"),
        "otsu_histogram_bucket_count": otsu.get("histogram_bucket_count"),
        "otsu_threshold_raw": otsu.get("threshold_raw"),
        "otsu_threshold_c": otsu.get("threshold_c"),
        "bio1_temperature_scale": args.temperature_scale,
        "bio1_temperature_offset": args.temperature_offset,
        "source_manifest": args.manifest_asset,
        "processing_support": "selected_gmba_plus_context_buffer",
        "context_buffer_m": args.context_buffer_m,
        "source_sayre": (
            args.high_mountain_asset if args.domain_mode == "sayre-intersection" else "not_applied"
        ),
        "sayre_intersection": args.domain_mode == "sayre-intersection",
    }
    return {
        "batch_region": batch_region,
        "processing_rectangle": processing_rectangle,
        "processing_support": processing_support,
        "selected_shard": selected_shard,
        "common": common,
        "temperature_info": temperature_info,
        "otsu": otsu,
        "products": products,
        "images": images,
        "metadata": metadata,
    }


def make_asset_export_task(
    args: argparse.Namespace,
    record: Mapping[str, object],
    bundle: Mapping[str, object],
) -> ee.batch.Task:
    images = bundle["images"]
    return ee.batch.Export.image.toAsset(
        image=ee.Image(images[str(record["product"])]).set(bundle["metadata"]),
        description=str(record["description"]),
        assetId=str(record["destination"]),
        pyramidingPolicy=dict(record["pyramiding_policy"]),
        region=bundle["batch_region"],
        crs=str(record["crs"]),
        crsTransform=record["crs_transform"],
        maxPixels=1e13,
        overwrite=args.overwrite_assets,
    )


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
    map_object = geemap.Map()
    map_object.centerObject(batch_region, 8)
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
            "pixel_count": (
                image_count(ee.Image(common["domain"]), batch_region, 30, args)
                if args.pixel_counts else None
            ),
            "pixel_count_status": "computed" if args.pixel_counts else "skipped_by_option",
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
                "pixel_count": (
                    image_count(ee.Image(image), batch_region, scale, args)
                    if args.pixel_counts else None
                ),
                "pixel_count_status": "computed" if args.pixel_counts else "skipped_by_option",
            }
        )
    args.map_html.parent.mkdir(parents=True, exist_ok=True)
    map_object.to_html(filename=str(args.map_html), title="GMBA treeline tracer")
    return layer_report


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_formal_shard_preflight(
    args: argparse.Namespace,
    selected_all: ee.FeatureCollection,
    shard_plan: Sequence[Mapping[str, object]],
    export_records: Sequence[Mapping[str, object]],
    thresholds_by_region: Mapping[str, float],
) -> Dict[str, object]:
    planned_shard_ids = {str(record["shard_id"]) for record in export_records}
    available_shards = [
        shard for shard in shard_plan if str(shard["shard_id"]) in planned_shard_ids
    ]
    if not available_shards:
        raise ValueError("formal preflight has no planned export shard")
    if args.check_shard_id:
        candidates = [
            shard for shard in available_shards
            if shard["shard_id"] == args.check_shard_id
        ]
        if not candidates:
            raise ValueError(
                "check shard is absent from the limited export plan: "
                f"{args.check_shard_id}"
            )
        shard = candidates[0]
    else:
        shard = next(
            (
                candidate for candidate in available_shards
                if str(candidate["region_id"]).startswith("R3_")
                and int(candidate["tile_x"]) == 5
                and int(candidate["tile_y"]) == 23
            ),
            min(available_shards, key=lambda candidate: int(candidate["quarter_cell_count"])),
        )
    bundle = build_shard_export_bundle(
        args, selected_all, shard, thresholds_by_region
    )
    shard_records = [
        record for record in export_records if record["shard_id"] == shard["shard_id"]
    ]
    tasks = [make_asset_export_task(args, record, bundle) for record in shard_records]
    images = bundle["images"]
    def serialize_task_value(value: object) -> object:
        if isinstance(value, ee.ComputedObject):
            return ee.serializer.encode(value)
        return str(value)

    config_sizes = [
        len(
            json.dumps(
                task.config, ensure_ascii=False, separators=(",", ":"),
                default=serialize_task_value,
            ).encode("utf-8")
        )
        for task in tasks
    ]
    if args.otsu_scope == "shard-dynamic":
        formal_otsu: object = {
            "source": "shard-dynamic",
            "status": "deferred_to_export_task",
            "reason": "avoid synchronous evaluation of the full-shard dynamic histogram",
        }
        product_bands = expected_product_bands(args)
        band_status = "validated_from_static_schema; dynamic graph serialized"
    else:
        formal_otsu = ee.Dictionary(bundle["otsu"]).getInfo()
        product_bands = {
            product: ee.Image(images[product]).bandNames().getInfo()
            for product in ("treeline30m", "treeline1km", "qa30m")
        }
        band_status = "validated_online"
    online = {
        "gmba_feature_count": int(
            ee.FeatureCollection(bundle["selected_shard"]).size().getInfo()
        ),
        "otsu": formal_otsu,
        "treeline30m_bands": product_bands["treeline30m"],
        "treeline1km_bands": product_bands["treeline1km"],
        "qa30m_bands": product_bands["qa30m"],
        "band_validation_status": band_status,
        "analysis_domain_count_1km": None,
        "analysis_domain_count_status": "skipped_in_default_preflight",
    }
    if args.deep_check:
        online["analysis_domain_count_1km"] = ee.Image(bundle["common"]["domain"]).reduceRegion(
            reducer=ee.Reducer.count(),
            geometry=bundle["batch_region"],
            scale=1000,
            maxPixels=args.otsu_max_pixels,
            tileScale=args.tile_scale,
        ).get("analysis_domain").getInfo()
        online["analysis_domain_count_status"] = "computed_by_explicit_deep_check"
    return {
        "shard": dict(shard),
        "online": online,
        "task_count": len(tasks),
        "task_ids_allocated_but_not_started": [task.id for task in tasks],
        "task_descriptions": [task.config.get("description") for task in tasks],
        "task_config_keys": [sorted(task.config) for task in tasks],
        "task_config_size_bytes": config_sizes,
        "tasks_started": False,
    }


def run_check(args: argparse.Namespace) -> Dict[str, object]:
    auth = initialize_with_adc(args.project)
    target_collections = validate_target_collections(args)
    selected_all, region_ids, manifest_summary = resolve_region_selection(args)
    shard_plan = resolve_shard_plan(selected_all, region_ids, args)
    export_records = planned_export_records(args, shard_plan)
    existing_ids = {
        str(asset_id)
        for summary in target_collections.values()
        for asset_id in summary["existing_child_ids"]
        if asset_id
    }
    for record in export_records:
        record["destination_exists"] = str(record["destination"]) in existing_ids
    default_check_region_id = next(
        (region_id for region_id in region_ids if region_id == "R3" or region_id.startswith("R3_")),
        region_ids[0],
    )
    check_region_id = args.check_region_id or default_check_region_id
    if check_region_id not in manifest_summary["all_region_ids"]:
        raise ValueError(f"check region is absent from manifest: {check_region_id}")
    west, south, east, north = validate_bbox(args.check_bbox)
    batch_region = ee.Geometry.Rectangle(
        [west, south, east, north], proj=FINE_CRS, geodesic=False
    )
    processing_rectangle = batch_region.buffer(
        args.context_buffer_m, args.geometry_max_error_m
    )
    selected_region = selected_all.filter(ee.Filter.eq("region_id", check_region_id))
    selected_check = selected_region.filterBounds(batch_region).limit(args.check_max_features)
    if int(selected_check.size().getInfo()) == 0:
        raise ValueError(
            f"representative check bbox {args.check_bbox} does not hit GMBA features in {check_region_id}"
        )
    assets = {
        name: asset_summary(asset_id)
        for name, asset_id in {
            "gmba": args.gmba_asset,
            "manifest": args.manifest_asset,
            **(
                {"sayre_high_mountain": args.high_mountain_asset}
                if args.domain_mode == "sayre-intersection" else {}
            ),
            "forest_height_2000": FOREST_HEIGHT_2000,
            "forest_height_2020": FOREST_HEIGHT_2020,
            "chelsa_bio01": args.chelsa_bio01,
            "aw3d30": AW3D30,
            "alos_landforms": ALOS_LANDFORMS,
            "worldcover": WORLDCOVER,
        }.items()
    }
    processing_support = build_processing_support(
        selected_check, processing_rectangle, args
    )
    common = build_common(
        args, batch_region, processing_rectangle, processing_support, selected_check
    )
    gmba_hit_count = int(selected_check.size().getInfo())
    gmba_property_names = ee.Feature(selected_check.first()).propertyNames().getInfo()
    temperature_info = temperature_graph(
        ee.Image(common["preliminary_edges2020"]), batch_region, args
    )
    temperature_range = ee.Dictionary(temperature_info["range_qa"]).getInfo()
    histogram = temperature_info["histogram"].getInfo()
    if not isinstance(histogram, dict):
        raise ValueError(f"temperature histogram is empty or degenerate: {histogram}")
    threshold_raw = otsu_threshold_from_histogram(histogram)
    threshold_c = convert_raw_temperature(
        threshold_raw, args.temperature_scale, args.temperature_offset
    )
    server_otsu = otsu_threshold_ee(
        temperature_info["histogram"], args.temperature_scale, args.temperature_offset
    ).getInfo()
    if int(server_otsu["valid"]) != 1 or not math.isclose(
        float(server_otsu["threshold_raw"]), threshold_raw
    ):
        raise ValueError(
            f"server/local Otsu mismatch: server={server_otsu}, local_raw={threshold_raw}"
        )
    thresholds_by_region = load_fixed_thresholds(
        args, region_ids, fallback_threshold_c=threshold_c
    )
    product_threshold_c = thresholds_by_region.get(check_region_id, threshold_c)
    products = build_products(
        args, common, processing_support, product_threshold_c,
        ee.Image(temperature_info["temperature"]),
    )
    formal_preflight = run_formal_shard_preflight(
        args, selected_all, shard_plan, export_records, thresholds_by_region
    )
    layer_report = create_map(args, batch_region, common, products)
    layer_report[0]["feature_count"] = gmba_hit_count
    report = {
        "status": "gmba-quarter-grid-check-passed",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "resolved_plan": resolved_plan(args),
        "authentication": auth,
        "assets": assets,
        "target_collections": target_collections,
        "manifest": manifest_summary,
        "gmba": {
            "map_unit": "Basic",
            "check_region_id": check_region_id,
            "region_feature_count": int(selected_region.size().getInfo()),
            "tracer_feature_count": gmba_hit_count,
            "tracer_bbox": args.check_bbox,
            "first_feature_property_names": gmba_property_names,
        },
        "chelsa_bio01_range": temperature_range,
        "bio1_conversion_assessment": assess_bio1_conversion(
            temperature_range, args.temperature_scale, args.temperature_offset
        ),
        "otsu": {
            "threshold_raw": threshold_raw,
            "threshold_c": threshold_c,
            "source": "Otsu on unchanged integer BIO1; only the threshold is converted to Celsius",
            "histogram_bucket_count": len(histogram.get("histogram", [])),
            "candidate_sample_count": sum(histogram.get("histogram", [])),
            "server_side_result": server_otsu,
            "server_local_match": True,
            "tracer_product_threshold_c": product_threshold_c,
            "formal_scope": args.otsu_scope,
            "formal_thresholds_by_region": thresholds_by_region,
            "check_fallback_note": (
                "If no frozen threshold was supplied, the tracer threshold is used only to serialize "
                "one formal preflight graph; --export remains blocked."
            ),
        },
        "layers": layer_report,
        "formal_shard_preflight": formal_preflight,
        "grid": {
            "analysis_cell_degrees": args.quarter_grid_deg,
            "export_shard_degrees": args.export_shard_deg,
            "quarter_cell_count": sum(int(shard["quarter_cell_count"]) for shard in shard_plan),
            "shard_count": len(shard_plan),
            "shard_count_by_region": shard_counts_by_region(shard_plan),
            "domain_mode": args.domain_mode,
            "sayre_intersection": args.domain_mode == "sayre-intersection",
        },
        "planned_shards": shard_plan,
        "planned_exports": export_records,
        "planned_export_count": len(export_records),
        "existing_planned_destination_count": sum(
            1 for record in export_records if record["destination_exists"]
        ),
        "map_html": str(args.map_html),
        "exports_started": False,
    }
    write_json_atomic(args.report_json, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def start_exports(args: argparse.Namespace) -> Path:
    auth = initialize_with_adc(args.project)
    target_collections = validate_target_collections(args)
    selected_all, region_ids, manifest_summary = resolve_region_selection(args)
    shard_plan = resolve_shard_plan(selected_all, region_ids, args)
    thresholds_by_region = load_fixed_thresholds(args, region_ids)
    records = planned_export_records(args, shard_plan)
    existing_ids = {
        str(asset_id)
        for summary in target_collections.values()
        for asset_id in summary["existing_child_ids"]
        if asset_id
    }
    apply_resume_guards(
        records, existing_ids, active_tasks_by_description(), args.resume, args.overwrite_assets
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    registry = args.registry_dir / f"{timestamp}-{args.task_prefix}.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": args.project,
        "authentication": auth,
        "manifest": manifest_summary,
        "target_collections": target_collections,
        "grid": {
            "analysis_cell_degrees": args.quarter_grid_deg,
            "export_shard_degrees": args.export_shard_deg,
            "shard_count": len(shard_plan),
            "shard_count_by_region": shard_counts_by_region(shard_plan),
            "domain_mode": args.domain_mode,
            "sayre_intersection": args.domain_mode == "sayre-intersection",
        },
        "otsu": {
            "scope": args.otsu_scope,
            "thresholds_by_region": thresholds_by_region,
            "note": (
                "Fixed thresholds are reused by every 2-degree export shard. "
                "An empty mapping is expected only for explicitly accepted shard-dynamic mode."
            ),
        },
        "scientific_choices": {
            "t_test_variance": args.t_test_variance,
            "t_test_alternative": args.t_test_alternative,
            "hole_max_size_pixels": args.hole_max_size_pixels,
            "hole_filling_assumption_accepted": args.accept_hole_filling_assumption,
        },
        "overwrite_assets": args.overwrite_assets,
        "resume": args.resume,
        "max_export_tasks": args.max_export_tasks,
        "max_mountains": args.max_mountains,
        "mountain_offset": args.mountain_offset,
        "planned_export_task_count": len(records),
        "phase": "PREFLIGHT",
        "tasks": records,
    }
    write_json_atomic(registry, payload)

    records_by_shard = {
        str(shard["shard_id"]): [
            record for record in records if record["shard_id"] == shard["shard_id"]
        ]
        for shard in shard_plan
    }

    # Phase 1: construct and serialize every formal graph/config before any task starts.
    for shard in shard_plan:
        shard_id = str(shard["shard_id"])
        shard_records = [
            record for record in records_by_shard[shard_id] if record["state"] == "PLANNED"
        ]
        if not shard_records:
            continue
        try:
            bundle = build_shard_export_bundle(
                args, selected_all, shard, thresholds_by_region
            )
            preflight_tasks = [
                make_asset_export_task(args, record, bundle) for record in shard_records
            ]
            for task, record in zip(preflight_tasks, shard_records):
                if not task.config:
                    raise ValueError(f"empty export config for {record['description']}")
                record["state"] = "PREFLIGHTED"
            write_json_atomic(registry, payload)
        except Exception as error:
            for record in shard_records:
                if record["state"] in {"PLANNED", "PREFLIGHTED"}:
                    record["state"] = "FAILED_PREFLIGHT"
                record["error"] = f"{type(error).__name__}: {error}"
            payload["phase"] = "PREFLIGHT_FAILED"
            write_json_atomic(registry, payload)
            raise RuntimeError(
                f"formal export preflight failed before any task was started; see {registry}"
            ) from error

    # Recheck external state after preflight, then enter the separate submit phase.
    refreshed_collections = validate_target_collections(args)
    refreshed_existing_ids = {
        str(asset_id)
        for summary in refreshed_collections.values()
        for asset_id in summary["existing_child_ids"]
        if asset_id
    }
    apply_resume_guards(
        records, refreshed_existing_ids, active_tasks_by_description(),
        args.resume, args.overwrite_assets,
    )
    payload["target_collections_after_preflight"] = refreshed_collections
    payload["phase"] = "SUBMITTING"
    write_json_atomic(registry, payload)

    # Phase 2: rebuild the already validated configs and submit only missing records.
    for shard in shard_plan:
        shard_id = str(shard["shard_id"])
        shard_records = [
            record for record in records_by_shard[shard_id]
            if record["state"] == "PREFLIGHTED"
        ]
        if not shard_records:
            continue
        try:
            bundle = build_shard_export_bundle(
                args, selected_all, shard, thresholds_by_region
            )
            for record in shard_records:
                task = make_asset_export_task(args, record, bundle)
                record["state"] = "CREATED"
                task.start()
                record["task_id"] = task.id
                record["state"] = "SUBMITTED"
            write_json_atomic(registry, payload)
        except Exception as error:
            for record in shard_records:
                if record["state"] == "PREFLIGHTED":
                    record["state"] = "FAILED_TO_REBUILD"
                elif record["state"] == "CREATED":
                    record["state"] = "FAILED_TO_START"
                else:
                    continue
                record["error"] = f"{type(error).__name__}: {error}"
            payload["phase"] = "SUBMIT_FAILED_RESUMABLE"
            write_json_atomic(registry, payload)
            raise RuntimeError(
                f"submission stopped after a partial queue update; rerun with --resume using {registry}"
            ) from error

    payload["phase"] = "SUBMITTED"
    write_json_atomic(registry, payload)
    print(
        json.dumps(
            {
                "registry": str(registry),
                "submitted_count": sum(record["state"] == "SUBMITTED" for record in records),
                "skipped_existing_count": sum(record["state"] == "SKIPPED_EXISTING" for record in records),
                "skipped_active_count": sum(record["state"] == "SKIPPED_ACTIVE" for record in records),
                "shard_count": len(shard_plan),
                "task_count": len(records),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return registry


def recover_task_ids(
    registry: Dict[str, object], remote_tasks: Sequence[Mapping[str, object]]
) -> int:
    by_description: Dict[str, Mapping[str, object]] = {}
    for remote in remote_tasks:
        description = remote.get("description")
        remote_id = remote.get("id") or remote.get("task_id")
        if description and remote_id:
            by_description.setdefault(str(description), remote)
    recovered = 0
    for task in registry["tasks"]:
        if task.get("task_id"):
            continue
        remote = by_description.get(str(task.get("description")))
        if remote is None:
            continue
        task["task_id"] = remote.get("id") or remote.get("task_id")
        recovered += 1
    return recovered


def monitor_once(project: str, registry_path: Path) -> None:
    initialize_with_adc(project)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    recovered = recover_task_ids(registry, ee.data.getTaskList())
    if recovered:
        registry["task_id_recovery"] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "recovered_count": recovered,
            "method": "matched unique task descriptions from ee.data.getTaskList()",
        }
        write_json_atomic(registry_path, registry)
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
    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "recovered_task_ids": recovered,
        "counts": counts,
        "tasks": details,
    }
    registry["last_monitor"] = result
    write_json_atomic(registry_path, registry)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.dry_run and RUNTIME_IMPORT_ERROR is not None:
        parser.error(
            "online modes require earthengine-api, geemap, and google-auth; "
            f"first missing import: {RUNTIME_IMPORT_ERROR.name}"
        )
    if args.monitor_once:
        if not args.project:
            parser.error("--monitor-once requires --project or EE_PROJECT")
        monitor_once(args.project, Path(args.monitor_once))
        return 0
    if not 0 < args.aspect_half_width_deg <= 90:
        parser.error("--aspect-half-width-deg must be in (0, 90]")
    if args.check_max_features < 1:
        parser.error("--check-max-features must be at least 1")
    if args.geometry_max_error_m <= 0:
        parser.error("--geometry-max-error-m must be positive")
    minimum_context_buffer_m = args.hole_max_size_pixels * 30 + args.window_radius_m
    if args.context_buffer_m < minimum_context_buffer_m:
        parser.error(
            f"--context-buffer-m must be at least {minimum_context_buffer_m:g} m for "
            "the configured hole and neighborhood scales"
        )
    if args.expected_region_count < 1:
        parser.error("--expected-region-count must be at least 1")
    if args.expected_shard_count < 1:
        parser.error("--expected-shard-count must be at least 1")
    if args.max_export_tasks is not None and args.max_export_tasks < 1:
        parser.error("--max-export-tasks must be at least 1")
    if args.max_mountains is not None and args.max_mountains < 1:
        parser.error("--max-mountains must be at least 1")
    if args.mountain_offset < 0:
        parser.error("--mountain-offset must be non-negative")
    if args.mountain_offset and args.max_mountains is None:
        parser.error("--mountain-offset requires --max-mountains")
    if args.max_mountains is not None and args.max_export_tasks is not None:
        parser.error("--max-mountains and --max-export-tasks are mutually exclusive")
    if args.quarter_grid_deg <= 0 or args.export_shard_deg <= 0:
        parser.error("--quarter-grid-deg and --export-shard-deg must be positive")
    cells_per_side = args.export_shard_deg / args.quarter_grid_deg
    if not math.isclose(cells_per_side, round(cells_per_side)):
        parser.error("--export-shard-deg must be an integer multiple of --quarter-grid-deg")
    if not 0 <= args.maximum_tree_cover_fraction <= 1:
        parser.error("--maximum-tree-cover-fraction must be in [0, 1]")
    if not 0 <= args.minimum_high_mountain_fraction <= 1:
        parser.error("--minimum-high-mountain-fraction must be in [0, 1]")
    if not math.isfinite(args.temperature_scale) or math.isclose(args.temperature_scale, 0):
        parser.error("--temperature-scale must be finite and non-zero")
    if not math.isfinite(args.temperature_offset):
        parser.error("--temperature-offset must be finite")
    if args.temperature_threshold_c is not None and not math.isfinite(
        args.temperature_threshold_c
    ):
        parser.error("--temperature-threshold-c must be finite")
    if args.hole_max_size_pixels < 1:
        parser.error("--hole-max-size-pixels must be at least 1")
    if args.minimum_samples_per_group < 2:
        parser.error("--minimum-samples-per-group must be at least 2")
    try:
        sanitize_asset_component(args.run_label)
    except ValueError as error:
        parser.error(str(error))
    if args.check:
        validate_bbox(args.check_bbox)
    if args.resume and args.overwrite_assets:
        parser.error("--resume and --overwrite-assets are mutually exclusive")
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
