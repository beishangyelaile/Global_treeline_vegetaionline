"""Per-GMBA observed-treeline extraction for 2000 and 2020.

The workflow has two explicit stages.

1. ``--prepare-mountains`` joins GMBA v2 Standard Basic features to the
   reviewed GMBA/Sayre manifest and exports the selected, non-overlapping
   mountain units to one Earth Engine table Asset.  Original GMBA vector
   geometry is preserved.
2. ``--check`` and ``--export`` process one output unit per prepared GMBA.
   The full GMBA Basic geometry is the processing domain. Binary forest masks
   use the fixed JRC GFC2020-style MMU post-processing order: remove forest
   components with area <=0.5 ha, fill internal non-forest gaps with area
   <0.5 ha, then median-filter and detect edges. Valleys are removed, and one
   per-mountain Otsu BIO1
   threshold is estimated from the union of the 2000/2020 post-landform edge
   candidates.  The same threshold is applied to both years before the 300 m
   local elevation test.

Canopy height >3 m is primary; >5 m is exported as sensitivity by default.
Zero crossing is the paper-aligned edge detector and Canny is an optional
sensitivity mode. This aligns only the binary MMU post-processing with JRC;
the forest definition remains based on GLAD canopy height. Parameters omitted
by the source paper are explicit in the CLI and written to output metadata.
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


SCRIPT_DIR = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0]).resolve() == SCRIPT_DIR:
    sys.path.pop(0)

RUNTIME_IMPORT_ERROR: Optional[ModuleNotFoundError] = None
try:
    import ee
    import geemap
    import google.auth
    from google.auth.transport.requests import Request
except ModuleNotFoundError as error:  # Keep --dry-run and offline tests useful.
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
VALLEY_CLASSES = (41, 42)
JRC_MMU_HA = 0.5
JRC_MMU_M2 = JRC_MMU_HA * 10_000
JRC_MMU_CONNECTIVITY = 8
CONNECTED_COMPONENT_MAX_SIZE = 512
FINE_CRS = "EPSG:4326"
FINE_TRANSFORM = [0.00025, 0, -180, 0, -0.00025, 90]
CLIMATE_CRS = "EPSG:4326"
CLIMATE_TRANSFORM = [1 / 120, 0, -180, 0, -1 / 120, 90]
WORKLOAD_TAG = "global-treeline-per-gmba-20260823"
REGION_PROPERTIES = (
    "region_id", "region_name", "region_subtype", "hm31_km2",
    "hm32_km2", "hm_area_km2", "hm_fraction",
)
ADC_SCOPES = (
    "https://www.googleapis.com/auth/earthengine",
    "https://www.googleapis.com/auth/cloud-platform",
)
ONE_SIDED_T_CRITICAL_95 = (
    (1, -6.314), (2, -2.920), (3, -2.353), (4, -2.132), (5, -2.015),
    (6, -1.943), (7, -1.895), (8, -1.860), (9, -1.833), (10, -1.812),
    (12, -1.782), (15, -1.753), (20, -1.725), (30, -1.697),
    (40, -1.684), (60, -1.671), (120, -1.658), (1000, -1.645),
)
TWO_SIDED_T_CRITICAL_95 = (
    (1, -12.706), (2, -4.303), (3, -3.182), (4, -2.776), (5, -2.571),
    (6, -2.447), (7, -2.365), (8, -2.306), (9, -2.262), (10, -2.228),
    (12, -2.179), (15, -2.131), (20, -2.086), (30, -2.042),
    (40, -2.021), (60, -2.000), (120, -1.980), (1000, -1.960),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--prepare-mountains", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--export", action="store_true")
    mode.add_argument("--monitor-once", metavar="REGISTRY")

    parser.add_argument("--project", default=os.environ.get("EE_PROJECT"))
    parser.add_argument("--gmba-asset", default=os.environ.get("GMBA_ASSET"))
    parser.add_argument("--manifest-asset", default=os.environ.get("MANIFEST_ASSET"))
    parser.add_argument(
        "--prepared-mountains-asset",
        default=os.environ.get("PREPARED_MOUNTAINS_ASSET"),
        help="Destination for preparation; required input for check/export",
    )
    parser.add_argument("--chelsa-bio01", default=os.environ.get("CHELSA_BIO01"))
    parser.add_argument("--treeline30m-collection", default=os.environ.get("TREELINE30M_COLLECTION"))
    parser.add_argument("--treeline1km-collection", default=os.environ.get("TREELINE1KM_COLLECTION"))
    parser.add_argument("--qa30m-collection", default=os.environ.get("QA30M_COLLECTION"))

    parser.add_argument("--region-id", action="append", dest="region_ids")
    parser.add_argument("--max-mountains", type=int)
    parser.add_argument("--mountain-offset", type=int, default=0)
    parser.add_argument(
        "--allow-large-batch-submit", action="store_true",
        help=(
            "Acknowledge submission of more than 100 mountains in one invocation. "
            "This does not bypass the Earth Engine READY-task queue safety limit."
        ),
    )
    parser.add_argument(
        "--queue-safety-limit", type=int, default=2900,
        help="Refuse submission when existing READY tasks plus new exports exceed this value",
    )
    parser.add_argument("--expected-mountain-count", type=int, default=978)
    parser.add_argument("--expected-region-count", type=int, default=8)
    parser.add_argument("--check-mountain-id")
    parser.add_argument(
        "--check-strategy", choices=("median", "largest", "smallest"), default="median"
    )
    parser.add_argument("--deep-check", action="store_true")
    parser.add_argument(
        "--pixel-counts", action=argparse.BooleanOptionalAction, default=False
    )

    parser.add_argument("--context-buffer-m", type=float, default=16000)
    parser.add_argument("--geometry-max-error-m", type=float, default=1000)

    parser.add_argument(
        "--canopy-thresholds-m", type=float, nargs="+", default=[3.0, 5.0]
    )
    parser.add_argument("--median-radius-pixels", type=float, default=1)
    parser.add_argument(
        "--edge-method", choices=("zero-crossing", "canny"), default="zero-crossing"
    )
    parser.add_argument("--canny-threshold", type=float, default=0.1)
    parser.add_argument("--canny-sigma", type=float, default=1.0)

    parser.add_argument("--window-radius-m", type=float, default=150)
    parser.add_argument("--minimum-samples-per-group", type=int, default=5)
    parser.add_argument("--minimum-elevation-difference-m", type=float, default=0.0)
    parser.add_argument("--t-test-variance", choices=("welch", "pooled"), default="welch")
    parser.add_argument(
        "--t-test-alternative", choices=("less", "two-sided"), default="less"
    )
    parser.add_argument("--aspect-mode", choices=("none", "polar-equator"), default="none")
    parser.add_argument("--aspect-half-width-deg", type=float, default=45)
    parser.add_argument("--minimum-slope-deg", type=float, default=5)
    parser.add_argument("--equator-buffer-deg", type=float, default=0.1)

    parser.add_argument("--strict-aw3d-native-only", action="store_true")
    parser.add_argument("--temperature-scale", type=float, default=0.1)
    parser.add_argument("--temperature-offset", type=float, default=-273.15)
    parser.add_argument(
        "--otsu-scope",
        choices=("mountain-pooled", "mountain-fixed"),
        default="mountain-pooled",
    )
    parser.add_argument("--mountain-thresholds-json", type=Path)
    parser.add_argument("--otsu-min-samples", type=int, default=20)
    parser.add_argument("--otsu-max-pixels", type=float, default=1e8)
    parser.add_argument("--tile-scale", type=float, default=4)

    parser.add_argument("--export-1km", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--export-qa", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--task-prefix", default="treeline_gmba")
    parser.add_argument("--run-label", default="mountain_v4_jrc_mmu")
    parser.add_argument("--overwrite-assets", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--table-max-vertices", type=int, default=1_000_000)
    parser.add_argument("--report-json", type=Path, default=SCRIPT_DIR / "gmba_check_console.json")
    parser.add_argument("--map-html", type=Path, default=SCRIPT_DIR / "gmba_check_map.html")
    parser.add_argument("--write-map", action="store_true")
    parser.add_argument("--registry-dir", type=Path, default=PROJECT_ROOT / "outputs" / "tasks")
    return parser


def sanitize_asset_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")
    if not component:
        raise ValueError(f"asset component is empty after sanitizing: {value!r}")
    return component


def threshold_label(value: float) -> str:
    text = f"{float(value):g}".replace("-", "m").replace(".", "p")
    return sanitize_asset_component(f"h{text}m")


def normalized_canopy_thresholds(values: Sequence[float]) -> List[float]:
    normalized = sorted({float(value) for value in values})
    if not normalized or any(not math.isfinite(value) or value <= 0 for value in normalized):
        raise ValueError("--canopy-thresholds-m must contain finite positive values")
    labels = [threshold_label(value) for value in normalized]
    if len(labels) != len(set(labels)):
        raise ValueError("canopy threshold labels are not unique after normalization")
    return normalized


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def current_git_commit() -> Optional[str]:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=SCRIPT_DIR, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def canonical_json_file_sha256(path: Optional[Path]) -> Optional[str]:
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def implementation_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def scientific_configuration(args: argparse.Namespace) -> Dict[str, object]:
    return {
        "workflow": "per-gmba-v4-jrc-mmu",
        "implementation_sha256": implementation_sha256(),
        "prepared_mountains_asset": args.prepared_mountains_asset,
        "analysis_domain": "complete_GMBA_v2_Standard_Basic_geometry",
        "chelsa_bio01": args.chelsa_bio01,
        "forest_height_2000": FOREST_HEIGHT_2000,
        "forest_height_2020": FOREST_HEIGHT_2020,
        "dem": AW3D30,
        "landforms": ALOS_LANDFORMS,
        "valley_classes": list(VALLEY_CLASSES),
        "fine_grid": {"crs": FINE_CRS, "transform": FINE_TRANSFORM},
        "climate_grid": {"crs": CLIMATE_CRS, "transform": CLIMATE_TRANSFORM},
        "canopy_thresholds_m": normalized_canopy_thresholds(args.canopy_thresholds_m),
        "mmu_area_ha": JRC_MMU_HA,
        "mmu_connectivity": JRC_MMU_CONNECTIVITY,
        "mmu_area_measure": "sum_pixelArea_m2_per_connected_component",
        "mmu_operation_order": "remove_small_forest_then_fill_small_nonforest_gaps",
        "connected_component_max_size_pixels": CONNECTED_COMPONENT_MAX_SIZE,
        "connected_component_max_size_role": "compute_protection_only",
        "jrc_alignment": "binary_mmu_postprocessing_only",
        "forest_definition": "GLAD_canopy_height_threshold",
        "median_radius_pixels": args.median_radius_pixels,
        "edge_method": args.edge_method,
        "canny_threshold": args.canny_threshold,
        "canny_sigma": args.canny_sigma,
        "window_radius_m": args.window_radius_m,
        "minimum_samples_per_group": args.minimum_samples_per_group,
        "minimum_elevation_difference_m": args.minimum_elevation_difference_m,
        "t_test_variance": args.t_test_variance,
        "t_test_alternative": args.t_test_alternative,
        "t_test_alpha": 0.05,
        "aspect_mode": args.aspect_mode,
        "aspect_half_width_deg": args.aspect_half_width_deg,
        "minimum_slope_deg": args.minimum_slope_deg,
        "equator_buffer_deg": args.equator_buffer_deg,
        "otsu_scope": args.otsu_scope,
        "mountain_thresholds_sha256": canonical_json_file_sha256(
            args.mountain_thresholds_json
        ),
        "otsu_population": "union_2000_2020_post_landform_edges_at_native_bio01_cells",
        "otsu_min_samples": args.otsu_min_samples,
        "temperature_scale": args.temperature_scale,
        "temperature_offset": args.temperature_offset,
        "strict_aw3d_native_only": args.strict_aw3d_native_only,
        "context_buffer_m": args.context_buffer_m,
        "geometry_max_error_m": args.geometry_max_error_m,
    }


def configuration_hash(args: argparse.Namespace) -> str:
    encoded = json.dumps(
        scientific_configuration(args), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def mode_name(args: argparse.Namespace) -> str:
    for attribute, label in (
        ("dry_run", "dry-run"), ("prepare_mountains", "prepare-mountains"),
        ("check", "check"), ("export", "export"),
    ):
        if getattr(args, attribute):
            return label
    return "monitor-once"


def missing_requirements(args: argparse.Namespace) -> List[str]:
    missing: List[str] = []
    if not args.project:
        missing.append("Earth Engine Cloud project (--project or EE_PROJECT)")
    if args.monitor_once:
        return missing
    if args.prepare_mountains:
        if not args.gmba_asset:
            missing.append("GMBA source Asset (--gmba-asset or GMBA_ASSET)")
        if not args.manifest_asset:
            missing.append("reviewed GMBA/Sayre manifest (--manifest-asset or MANIFEST_ASSET)")
        if not args.prepared_mountains_asset:
            missing.append("prepared mountain destination (--prepared-mountains-asset)")
        return missing
    if not args.prepared_mountains_asset:
        missing.append("prepared GMBA/Sayre mountain Asset (--prepared-mountains-asset)")
    if not args.chelsa_bio01:
        missing.append("CHELSA V2.1 BIO1 Asset (--chelsa-bio01)")
    if not args.treeline30m_collection:
        missing.append("30 m output ImageCollection (--treeline30m-collection)")
    if args.export_1km and not args.treeline1km_collection:
        missing.append("1 km output ImageCollection (--treeline1km-collection)")
    if args.export_qa and not args.qa30m_collection:
        missing.append("QA output ImageCollection (--qa30m-collection)")
    if args.otsu_scope == "mountain-fixed":
        if args.mountain_thresholds_json is None:
            missing.append("per-mountain threshold JSON (--mountain-thresholds-json)")
        elif not args.mountain_thresholds_json.is_file():
            missing.append(f"existing threshold JSON ({args.mountain_thresholds_json})")
    return missing


def resolved_plan(args: argparse.Namespace) -> Dict[str, object]:
    products = ["treeline30m"]
    if args.export_1km:
        products.append("treeline1km")
    if args.export_qa:
        products.append("qa30m")
    mountain_count = args.max_mountains or args.expected_mountain_count
    return {
        "mode": mode_name(args),
        "project": args.project,
        "prepared_mountains_asset": args.prepared_mountains_asset,
        "selection": {
            "region_ids": args.region_ids or "all",
            "mountain_offset": args.mountain_offset,
            "max_mountains": args.max_mountains,
            "expected_full_count": args.expected_mountain_count,
            "order": "gmba_sort_key ascending",
        },
        "analysis_unit": "one GMBA v2 Standard Basic feature",
        "analysis_domain": "complete GMBA v2 Standard Basic geometry",
        "otsu": {
            "scope": args.otsu_scope,
            "population": "union of 2000/2020 post-landform edge candidates",
            "native_temperature_cells_counted_once": True,
            "same_threshold_reused_for_both_years": True,
        },
        "canopy_thresholds_m": normalized_canopy_thresholds(args.canopy_thresholds_m),
        "edge_method": args.edge_method,
        "products": products,
        "expected_task_count": mountain_count * len(products),
        "configuration_hash": configuration_hash(args),
        "git_commit": current_git_commit(),
        "ready": not missing_requirements(args),
        "missing_requirements": missing_requirements(args),
        "scientific_configuration": scientific_configuration(args),
    }


def initialize_with_adc(project: str) -> Dict[str, object]:
    credentials, detected_project = google.auth.default(scopes=list(ADC_SCOPES))
    credentials.refresh(Request())
    ee.Initialize(credentials=credentials, project=project)
    ee.data.setDefaultWorkloadTag(WORKLOAD_TAG)
    return {
        "credential_type": type(credentials).__name__, "valid": bool(credentials.valid),
        "detected_project": detected_project,
        "quota_project": getattr(credentials, "quota_project_id", None),
        "ee_project": project,
    }


def class_mask(image: "ee.Image", values: Iterable[int]) -> "ee.Image":
    masks = [image.eq(value) for value in values]
    result = masks[0]
    for mask in masks[1:]:
        result = result.Or(mask)
    return result


def add_manifest_join_key(feature: "ee.Feature") -> "ee.Feature":
    feature = ee.Feature(feature)
    return feature.set(
        "gmba_join_id", ee.Number(feature.get("GMBA_V2_ID")).format("%.0f")
    )


def select_manifest_gmba(args: argparse.Namespace) -> Tuple["ee.FeatureCollection", "ee.FeatureCollection"]:
    gmba_basic = (
        ee.FeatureCollection(args.gmba_asset)
        .filter(ee.Filter.eq("MapUnit", "Basic"))
        .map(add_manifest_join_key)
    )
    manifest = ee.FeatureCollection(args.manifest_asset).map(add_manifest_join_key)
    joined = ee.FeatureCollection(
        ee.Join.saveFirst("manifest_row").apply(
            gmba_basic, manifest,
            ee.Filter.equals(leftField="gmba_join_id", rightField="gmba_join_id"),
        )
    ).filter(ee.Filter.notNull(["manifest_row"]))

    def copy_properties(feature: "ee.Feature") -> "ee.Feature":
        feature = ee.Feature(feature)
        row = ee.Feature(feature.get("manifest_row"))
        gmba_id = ee.Number(feature.get("GMBA_V2_ID"))
        return (
            ee.Feature(feature.geometry())
            .copyProperties(feature, ["GMBA_V2_ID", "MapUnit"])
            .copyProperties(row, list(REGION_PROPERTIES))
            .set("gmba_id_text", gmba_id.format("%.0f"))
            .set("gmba_sort_key", gmba_id)
            .set("analysis_unit", "GMBA_v2_Standard_Basic")
            .set("sayre_classes", "31,32")
            .set("selection_basis", "reviewed_manifest_positive_Sayre_intersection")
        )

    selected = joined.map(copy_properties).filter(ee.Filter.gt("hm_area_km2", 0))
    return selected, manifest


def asset_exists(asset_id: str) -> bool:
    try:
        ee.data.getAsset(asset_id)
        return True
    except ee.EEException as error:
        if "not found" in str(error).lower() or "does not exist" in str(error).lower():
            return False
        raise


def prepare_mountains(args: argparse.Namespace) -> Path:
    auth = initialize_with_adc(args.project)
    selected, manifest = select_manifest_gmba(args)
    count = int(selected.size().getInfo())
    manifest_count = int(manifest.size().getInfo())
    distinct_count = int(selected.aggregate_count_distinct("gmba_id_text").getInfo())
    region_count = int(selected.aggregate_count_distinct("region_id").getInfo())
    if count != args.expected_mountain_count:
        raise ValueError(f"expected {args.expected_mountain_count} mountains, found {count}")
    if distinct_count != count:
        raise ValueError(f"GMBA IDs are not unique: features={count}, distinct={distinct_count}")
    if region_count != args.expected_region_count:
        raise ValueError(f"expected {args.expected_region_count} regions, found {region_count}")
    if asset_exists(args.prepared_mountains_asset) and not args.overwrite_assets:
        raise ValueError("prepared mountain Asset already exists; choose a new ID or --overwrite-assets")
    task = ee.batch.Export.table.toAsset(
        collection=selected,
        description=sanitize_asset_component(f"{args.task_prefix}_prepare_mountains"),
        assetId=args.prepared_mountains_asset,
        maxVertices=args.table_max_vertices,
        overwrite=args.overwrite_assets,
    )
    task.start()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    registry = args.registry_dir / f"{timestamp}-{args.task_prefix}-prepare.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(), "phase": "SUBMITTED",
        "mode": "prepare-mountains", "authentication": auth,
        "source_gmba": args.gmba_asset, "source_manifest": args.manifest_asset,
        "source_manifest_count": manifest_count, "selected_mountain_count": count,
        "distinct_gmba_id_count": distinct_count, "region_count": region_count,
        "destination": args.prepared_mountains_asset,
        "geometry_note": (
            "Original non-overlapping GMBA Basic geometries are preserved and "
            "used in full as the processing domain. Sayre is selection-only."
        ),
        "tasks": [{"description": task.config.get("description"), "task_id": task.id,
                   "state": "SUBMITTED", "destination": args.prepared_mountains_asset}],
    }
    write_json_atomic(registry, payload)
    print(json.dumps({"registry": str(registry), "task_id": task.id}, indent=2))
    return registry


def prepared_mountains(args: argparse.Namespace) -> "ee.FeatureCollection":
    return ee.FeatureCollection(args.prepared_mountains_asset)


def select_analysis_mountains(
    collection: "ee.FeatureCollection", args: argparse.Namespace
) -> "ee.FeatureCollection":
    scoped = collection
    if args.region_ids:
        scoped = scoped.filter(ee.Filter.inList("region_id", list(dict.fromkeys(args.region_ids))))
    ordered = scoped.sort("gmba_sort_key")
    if args.max_mountains is not None:
        ordered = ee.FeatureCollection(ordered.toList(args.max_mountains, args.mountain_offset))
    return ordered


def resolve_mountain_plan(
    args: argparse.Namespace,
) -> Tuple["ee.FeatureCollection", List[Dict[str, object]], Dict[str, object]]:
    source = prepared_mountains(args)
    source_count = int(source.size().getInfo())
    if not args.region_ids and source_count != args.expected_mountain_count:
        raise ValueError(
            f"prepared Asset expected {args.expected_mountain_count} features, found {source_count}"
        )
    selected = select_analysis_mountains(source, args)
    count = int(selected.size().getInfo())
    if args.max_mountains is not None and count != args.max_mountains:
        raise ValueError(f"requested {args.max_mountains} mountains, resolved {count}")
    ids = selected.aggregate_array("gmba_id_text").getInfo()
    sort_keys = selected.aggregate_array("gmba_sort_key").getInfo()
    region_ids = selected.aggregate_array("region_id").getInfo()
    areas = selected.aggregate_array("hm_area_km2").getInfo()
    if not (len(ids) == len(sort_keys) == len(region_ids) == len(areas) == count):
        raise ValueError("prepared mountain properties are incomplete or misaligned")
    plan = [
        {
            "mountain_id": str(ids[index]), "gmba_sort_key": float(sort_keys[index]),
            "region_id": str(region_ids[index]),
            "high_mountain_area_km2": float(areas[index]),
            "mountain_key": sanitize_asset_component(f"gmba_{ids[index]}"),
        }
        for index in range(count)
    ]
    if len({item["mountain_id"] for item in plan}) != len(plan):
        raise ValueError("selected plan contains duplicate GMBA IDs")
    return selected, plan, {
        "prepared_asset_count": source_count, "selected_mountain_count": count,
        "mountain_offset": args.mountain_offset, "max_mountains": args.max_mountains,
        "selected_region_ids": sorted({str(value) for value in region_ids}),
        "selected_gmba_ids": [item["mountain_id"] for item in plan],
    }


def selected_feature(selected: "ee.FeatureCollection", mountain_id: str) -> "ee.Feature":
    return ee.Feature(
        selected.filter(ee.Filter.eq("gmba_id_text", str(mountain_id))).first()
    )


def list_child_assets(parent: str) -> List[Mapping[str, object]]:
    children: List[Mapping[str, object]] = []
    request: Dict[str, object] = {"parent": parent, "pageSize": 1000}
    while True:
        response = ee.data.listAssets(request)
        children.extend(response.get("assets", []))
        token = response.get("nextPageToken")
        if not token:
            break
        request["pageToken"] = token
    return children


def target_collection_ids(args: argparse.Namespace) -> Dict[str, str]:
    result = {"treeline30m": args.treeline30m_collection}
    if args.export_1km:
        result["treeline1km"] = args.treeline1km_collection
    if args.export_qa:
        result["qa30m"] = args.qa30m_collection
    return result


def validate_target_collections(args: argparse.Namespace) -> Dict[str, Dict[str, object]]:
    summaries: Dict[str, Dict[str, object]] = {}
    for product, collection_id in target_collection_ids(args).items():
        info = ee.data.getAsset(collection_id)
        if info.get("type") != "IMAGE_COLLECTION":
            raise ValueError(
                f"target for {product} must be IMAGE_COLLECTION, got {info.get('type')}"
            )
        children = list_child_assets(collection_id)
        summaries[product] = {
            "id": collection_id,
            "type": info.get("type"),
            "existing_child_count": len(children),
            "children": {
                str(child.get("id") or child.get("name")): child for child in children
            },
        }
    return summaries


def active_tasks_by_description(
    remote_tasks: Optional[Sequence[Mapping[str, object]]] = None,
) -> Dict[str, Dict[str, object]]:
    tasks = list(remote_tasks) if remote_tasks is not None else ee.data.getTaskList()
    return {
        str(task.get("description")): task
        for task in tasks
        if task.get("state") in {"READY", "RUNNING"} and task.get("description")
    }


def enforce_ready_queue_limit(
    records: Sequence[Mapping[str, object]],
    remote_tasks: Sequence[Mapping[str, object]],
    args: argparse.Namespace,
) -> Dict[str, int]:
    existing_ready = sum(task.get("state") == "READY" for task in remote_tasks)
    new_tasks = sum(
        record.get("state") in {"PLANNED", "PREFLIGHTED"} for record in records
    )
    projected_ready = existing_ready + new_tasks
    if projected_ready > args.queue_safety_limit:
        raise ValueError(
            "submission refused: existing READY tasks plus planned exports would be "
            f"{projected_ready}, above --queue-safety-limit={args.queue_safety_limit}; "
            "use a smaller --max-mountains batch or wait for queued tasks"
        )
    return {
        "existing_ready": existing_ready,
        "new_tasks": new_tasks,
        "projected_ready": projected_ready,
        "queue_safety_limit": args.queue_safety_limit,
    }


def child_asset_id(
    collection_id: str,
    mountain_key: str,
    args: argparse.Namespace,
    config_hash: str,
) -> str:
    thresholds = "_".join(
        threshold_label(value) for value in normalized_canopy_thresholds(args.canopy_thresholds_m)
    )
    child = sanitize_asset_component(
        f"{mountain_key}_{args.edge_method}_{args.aspect_mode}_{thresholds}_"
        f"{args.run_label}_{config_hash[:10]}"
    )
    return f"{collection_id.rstrip('/')}/{child}"


def planned_export_records(
    args: argparse.Namespace, mountain_plan: Sequence[Mapping[str, object]]
) -> List[Dict[str, object]]:
    config_hash = configuration_hash(args)
    product_specs: List[
        Tuple[str, str, str, Sequence[float], Mapping[str, str]]
    ] = [
        ("treeline30m", args.treeline30m_collection, FINE_CRS, FINE_TRANSFORM,
         {".default": "mean"}),
    ]
    if args.export_1km:
        product_specs.append(
            ("treeline1km", args.treeline1km_collection, CLIMATE_CRS,
             CLIMATE_TRANSFORM, {".default": "mean"})
        )
    if args.export_qa:
        qa_policy = {
            ".default": "mode", "dem_elevation_m": "mean", "dem_stk": "mean",
        }
        if args.aspect_mode == "polar-equator":
            qa_policy.update({"aspect_deg": "sample", "slope_deg": "mean"})
        for threshold in normalized_canopy_thresholds(args.canopy_thresholds_m):
            label = threshold_label(threshold)
            for group in analysis_groups(args):
                for year in (2000, 2020):
                    suffix = qa_test_suffix(group, year, label, args)
                    qa_policy[f"elev_delta_nonforest_minus_forest_{suffix}_m"] = "mean"
                    qa_policy[f"t_statistic_{suffix}"] = "mean"
                    qa_policy[f"forest_sample_count_{suffix}"] = "mean"
                    qa_policy[f"nonforest_sample_count_{suffix}"] = "mean"
        product_specs.append(
            ("qa30m", args.qa30m_collection, FINE_CRS, FINE_TRANSFORM, qa_policy)
        )
    records: List[Dict[str, object]] = []
    for mountain in mountain_plan:
        for product, collection_id, crs, transform, policy in product_specs:
            description = sanitize_asset_component(
                f"{args.task_prefix}_{args.run_label}_{mountain['mountain_key']}_"
                f"{product}_{config_hash[:10]}"
            )
            records.append({
                **dict(mountain),
                "product": product,
                "description": description,
                "destination": child_asset_id(
                    collection_id, str(mountain["mountain_key"]), args, config_hash
                ),
                "crs": crs,
                "crs_transform": list(transform),
                "pyramiding_policy": dict(policy),
                "configuration_hash": config_hash,
                "task_id": None,
                "state": "PLANNED",
            })
    return records


def apply_resume_guards(
    records: Sequence[Dict[str, object]],
    target_collections: Mapping[str, Mapping[str, object]],
    active_by_description: Mapping[str, Mapping[str, object]],
    args: argparse.Namespace,
) -> None:
    existing_by_id: Dict[str, Mapping[str, object]] = {}
    for summary in target_collections.values():
        existing_by_id.update(summary["children"])
    existing_records = [record for record in records if record["destination"] in existing_by_id]
    active_records = [record for record in records if record["description"] in active_by_description]
    if existing_records and not (args.resume or args.overwrite_assets):
        raise ValueError("refusing to overwrite existing Assets; use --resume or --overwrite-assets")
    if active_records and not args.resume:
        raise ValueError("matching READY/RUNNING tasks already exist; use --resume")
    if not args.resume:
        return
    for record in existing_records:
        child = existing_by_id[str(record["destination"])]
        remote_hash = (child.get("properties") or {}).get("configuration_hash")
        if remote_hash is None:
            # listAssets may omit user properties; fetch the individual Asset before
            # deciding whether a resume is scientifically safe.
            child = ee.data.getAsset(str(record["destination"]))
            remote_hash = (child.get("properties") or {}).get("configuration_hash")
        if remote_hash != record["configuration_hash"]:
            raise ValueError(
                "resume refused: existing Asset has a missing/different configuration_hash: "
                f"{record['destination']}"
            )
        record["state"] = "SKIPPED_EXISTING"
    for record in active_records:
        if record["state"] == "SKIPPED_EXISTING":
            continue
        active = active_by_description[str(record["description"])]
        record["state"] = "SKIPPED_ACTIVE"
        record["task_id"] = active.get("id") or active.get("task_id")


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


def build_common(feature: "ee.Feature", args: argparse.Namespace) -> Dict[str, object]:
    mountain_geometry = feature.geometry(maxError=args.geometry_max_error_m)
    mountain_bounds = mountain_geometry.bounds(args.geometry_max_error_m)
    processing_region = mountain_geometry.buffer(
        args.context_buffer_m, args.geometry_max_error_m
    )
    processing_bounds = processing_region.bounds(args.geometry_max_error_m)
    projection = ee.Projection(FINE_CRS, FINE_TRANSFORM)
    mountain_mask = (
        ee.Image(0).byte().paint(ee.FeatureCollection([feature]), 1)
        .setDefaultProjection(projection).clip(processing_bounds).unmask(0)
        .rename("gmba_mountain")
    )
    domain = mountain_mask.selfMask().rename("analysis_domain")
    aw3d = build_aw3d(processing_region, args.strict_aw3d_native_only)
    dem = aw3d.select("elevation").toFloat()
    landforms = ee.Image(ALOS_LANDFORMS).select("constant").clip(processing_region)
    nonvalley = class_mask(landforms, VALLEY_CLASSES).Not().rename("non_valley")
    return {
        "feature": feature,
        "mountain_geometry": mountain_geometry,
        "mountain_bounds": mountain_bounds,
        "processing_region": processing_region,
        "processing_bounds": processing_bounds,
        "mountain_mask": mountain_mask,
        "domain": domain,
        "aw3d": aw3d,
        "dem": dem,
        "nonvalley": nonvalley,
    }


def small_component_mask(binary: "ee.Image", comparison: str) -> "ee.Image":
    """Return components selected by the fixed 0.5 ha MMU area rule."""
    foreground = ee.Image(binary).unmask(0).eq(1).selfMask()
    labels = foreground.connectedComponents(
        ee.Kernel.square(1), CONNECTED_COMPONENT_MAX_SIZE
    ).select("labels")
    component_area = (
        labels.addBands(ee.Image.pixelArea().rename("component_area_m2"))
        .reduceConnectedComponents(
            ee.Reducer.sum(), "labels", CONNECTED_COMPONENT_MAX_SIZE
        )
        .select("component_area_m2")
    )
    if comparison == "lt":
        selected = component_area.lt(JRC_MMU_M2)
    elif comparison == "lte":
        selected = component_area.lte(JRC_MMU_M2)
    else:
        raise ValueError("comparison must be 'lt' or 'lte'")
    return selected.unmask(0).rename("small_component")


def clean_forest(
    canopy_asset: str,
    canopy_threshold_m: float,
    processing_region: "ee.Geometry",
) -> Dict[str, "ee.Image"]:
    """Apply the fixed JRC-style binary MMU before median filtering."""
    raw = (
        ee.Image(canopy_asset).select([0]).gt(canopy_threshold_m)
        .unmask(0).clip(processing_region).rename("forest_raw")
    )
    forest_small_patch_removed = small_component_mask(raw, "lte")
    retained = raw.And(forest_small_patch_removed.Not()).rename("forest_mmu_retained")
    nonforest = retained.Not().clip(processing_region).rename("nonforest")
    nonforest_small_gap_filled = small_component_mask(nonforest, "lt")
    forest = retained.Or(nonforest_small_gap_filled).rename("forest_clean")
    return {
        "forest": forest,
        "forest_small_patch_removed": forest_small_patch_removed,
        "nonforest_small_gap_filled": nonforest_small_gap_filled,
    }


def forest_edges(
    forest: "ee.Image", domain: "ee.Image", args: argparse.Namespace
) -> "ee.Image":
    smoothed = forest.focalMedian(args.median_radius_pixels, "square", "pixels").toFloat()
    if args.edge_method == "canny":
        edge = ee.Algorithms.CannyEdgeDetector(
            smoothed, args.canny_threshold, args.canny_sigma
        ).gt(0)
    else:
        edge = smoothed.convolve(ee.Kernel.laplacian8()).zeroCrossing().gt(0)
    return edge.And(domain).selfMask().rename("forest_edge")


def otsu_threshold_from_histogram(
    histogram: Mapping[str, object], minimum_samples: int = 1
) -> float:
    counts = [float(value) for value in histogram.get("histogram", [])]
    means = [float(value) for value in histogram.get("bucketMeans", [])]
    total = sum(counts)
    if (
        len(counts) < 2 or len(counts) != len(means) or total < minimum_samples
        or sum(value > 0 for value in counts) < 2
    ):
        raise ValueError(f"temperature histogram is empty, too small, or degenerate: {histogram}")
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
    best_index = max(range(len(scores)), key=scores.__getitem__)
    if not math.isfinite(scores[best_index]):
        raise ValueError(f"temperature histogram has no valid Otsu split: {histogram}")
    return means[best_index]


def otsu_threshold_ee(
    histogram: "ee.ComputedObject", scale: float, offset: float, minimum_samples: int
) -> "ee.Dictionary":
    fallback = ee.Dictionary({"histogram": [1, 1], "bucketMeans": [0, 1]})
    is_null = ee.Algorithms.IsEqual(histogram, None)
    raw_dictionary = ee.Dictionary(ee.Algorithms.If(is_null, fallback, histogram))
    raw_counts = ee.Array(raw_dictionary.get("histogram"))
    raw_means = ee.Array(raw_dictionary.get("bucketMeans"))
    raw_bucket_count = ee.Number(raw_means.length().get([0]))
    raw_total = ee.Number(raw_counts.reduce(ee.Reducer.sum(), [0]).get([0]))
    nonempty = ee.Number(raw_counts.gt(0).reduce(ee.Reducer.sum(), [0]).get([0]))
    valid = (
        ee.Number(ee.Algorithms.If(is_null, 0, 1)).multiply(raw_bucket_count.gte(2))
        .multiply(nonempty.gte(2)).multiply(raw_total.gte(minimum_samples))
    )
    safe = ee.Dictionary(ee.Algorithms.If(valid.eq(1), raw_dictionary, fallback))
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
    threshold_raw = ee.Number(candidates.sort(scores).get([-1]))
    return ee.Dictionary({
        "valid": valid,
        "threshold_raw": threshold_raw,
        "threshold_c": threshold_raw.multiply(scale).add(offset),
        "candidate_sample_count": ee.Number(ee.Algorithms.If(is_null, 0, raw_total)),
        "histogram_bucket_count": ee.Number(
            ee.Algorithms.If(is_null, 0, raw_bucket_count)
        ),
        "source": "mountain-pooled",
    })


def temperature_graph(
    pooled_post_landform_edges: "ee.Image",
    mountain_bounds: "ee.Geometry",
    args: argparse.Namespace,
) -> Dict[str, object]:
    raw = ee.Image(args.chelsa_bio01).select([0]).rename("bio01_raw")
    temperature = raw.multiply(args.temperature_scale).add(
        args.temperature_offset
    ).rename("temperature_c")
    # Count each native CHELSA cell once rather than pseudo-replicating its
    # value for every intersecting 30 m edge pixel.
    candidate_at_bio01 = (
        pooled_post_landform_edges.unmask(0)
        .reduceResolution(ee.Reducer.max(), maxPixels=4096)
        .reproject(raw.projection()).gt(0)
    )
    histogram = raw.updateMask(candidate_at_bio01).reduceRegion(
        reducer=ee.Reducer.histogram(maxBuckets=256, minBucketWidth=1),
        geometry=mountain_bounds,
        crs=raw.projection(),
        scale=raw.projection().nominalScale(),
        maxPixels=args.otsu_max_pixels,
        tileScale=args.tile_scale,
    ).get("bio01_raw")
    return {
        "raw": raw, "temperature": temperature,
        "candidate_at_bio01": candidate_at_bio01, "histogram": histogram,
    }


def load_fixed_mountain_thresholds(args: argparse.Namespace) -> Dict[str, Dict[str, float]]:
    if args.otsu_scope != "mountain-fixed":
        return {}
    payload = json.loads(args.mountain_thresholds_json.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("thresholds_c"), dict):
        payload = payload["thresholds_c"]
    if not isinstance(payload, dict):
        raise ValueError("mountain threshold JSON must be an object")
    expected = {
        threshold_label(value) for value in normalized_canopy_thresholds(args.canopy_thresholds_m)
    }
    result: Dict[str, Dict[str, float]] = {}
    for mountain_id, values in payload.items():
        if not isinstance(values, dict):
            raise ValueError(f"threshold entry for {mountain_id} must be an object")
        missing = expected - set(values)
        if missing:
            raise ValueError(f"threshold entry for {mountain_id} lacks {sorted(missing)}")
        parsed: Dict[str, float] = {}
        for label in expected:
            value = values[label]
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"non-finite threshold for {mountain_id}/{label}")
            parsed[label] = float(value)
        result[str(mountain_id)] = parsed
    return result


def fixed_threshold_dictionary(threshold_c: float, args: argparse.Namespace) -> "ee.Dictionary":
    raw = (threshold_c - args.temperature_offset) / args.temperature_scale
    return ee.Dictionary({
        "valid": 1, "threshold_raw": raw, "threshold_c": threshold_c,
        "candidate_sample_count": -1, "histogram_bucket_count": -1,
        "source": "mountain-fixed",
    })


def lower_tail_t_critical_image(df: "ee.Image", alternative: str) -> "ee.Image":
    table = (
        TWO_SIDED_T_CRITICAL_95 if alternative == "two-sided"
        else ONE_SIDED_T_CRITICAL_95
    )
    critical = ee.Image.constant(table[0][1])
    for minimum_df, value in table[1:]:
        critical = critical.where(df.gte(minimum_df), value)
    return critical


def upper_edge_test(
    forest: "ee.Image",
    dem: "ee.Image",
    candidates: "ee.Image",
    population_mask: "ee.Image",
    args: argparse.Namespace,
) -> Dict[str, "ee.Image"]:
    kernel = ee.Kernel.square(args.window_radius_m, "meters", False)
    reducer = (
        ee.Reducer.mean().combine(ee.Reducer.variance(), sharedInputs=True)
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
        pooled_variance = vf.multiply(nf.subtract(1)).add(
            vn.multiply(nn.subtract(1))
        ).divide(nf.add(nn).subtract(2))
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
        .And(statistic.lt(lower_tail_t_critical_image(
            degrees_of_freedom, args.t_test_alternative
        )))
        .selfMask().rename("upper_edge")
    )
    return {
        "upper": upper,
        "elevation_difference": elevation_difference,
        "t_statistic": statistic,
        "forest_count": nf,
        "nonforest_count": nn,
    }


def aspect_groups(dem: "ee.Image", args: argparse.Namespace) -> Dict[str, "ee.Image"]:
    aspect = ee.Terrain.aspect(dem).rename("aspect_deg")
    slope = ee.Terrain.slope(dem).rename("slope_deg")
    half = args.aspect_half_width_deg
    north = aspect.gte(360 - half).Or(aspect.lt(half))
    south = aspect.gte(180 - half).And(aspect.lt(180 + half))
    latitude = ee.Image.pixelLonLat().select("latitude")
    north_hemisphere = latitude.gt(args.equator_buffer_deg)
    south_hemisphere = latitude.lt(-args.equator_buffer_deg)
    valid_slope = slope.gte(args.minimum_slope_deg)
    polar = north_hemisphere.And(north).Or(south_hemisphere.And(south)).And(valid_slope)
    equator = north_hemisphere.And(south).Or(south_hemisphere.And(north)).And(valid_slope)
    classification = (
        ee.Image(0).byte().where(polar, 1).where(equator, 2).rename("aspect_class")
    )
    return {
        "aspect": aspect,
        "slope": slope,
        "polar": polar.rename("polar_facing"),
        "equator": equator.rename("equator_facing"),
        "class": classification,
    }


def aggregate_to_climate_grid(image: "ee.Image") -> "ee.Image":
    return image.reduceResolution(ee.Reducer.mean(), maxPixels=4096).reproject(
        ee.Projection(CLIMATE_CRS, CLIMATE_TRANSFORM)
    )


def add_band(base: Optional["ee.Image"], band: "ee.Image") -> "ee.Image":
    return band if base is None else base.addBands(band)


def analysis_groups(args: argparse.Namespace) -> Tuple[str, ...]:
    return ("polar", "equator") if args.aspect_mode == "polar-equator" else ("all",)


def qa_test_suffix(group: str, year: int, label: str, args: argparse.Namespace) -> str:
    if args.aspect_mode == "none":
        return f"{year}_{label}"
    return f"{group}_{year}_{label}"


def expected_product_bands(args: argparse.Namespace) -> Dict[str, List[str]]:
    bands30: List[str] = []
    bands1k: List[str] = []
    qa = ["analysis_domain", "non_valley", "dem_elevation_m", "dem_msk", "dem_stk"]
    groups = analysis_groups(args)
    if args.aspect_mode == "polar-equator":
        qa.extend(["aspect_class", "aspect_deg", "slope_deg"])
    for threshold in normalized_canopy_thresholds(args.canopy_thresholds_m):
        label = threshold_label(threshold)
        for group in groups:
            for year in (2000, 2020):
                prefix = "treeline" if group == "all" else f"treeline_{group}"
                bands30.append(f"{prefix}_{year}_{label}_m")
                bands1k.append(f"{prefix}_{year}_{label}_mean_m")
            shift = "shift" if group == "all" else f"shift_{group}"
            bands1k.append(f"{shift}_2000_2020_{label}_m_per_year")
        qa.extend([
            f"forest_clean_2000_{label}", f"forest_clean_2020_{label}",
            f"forest_small_patch_removed_2000_{label}",
            f"forest_small_patch_removed_2020_{label}",
            f"nonforest_small_gap_filled_2000_{label}",
            f"nonforest_small_gap_filled_2020_{label}",
            f"edge_post_landform_2000_{label}", f"edge_post_landform_2020_{label}",
            f"cold_zone_{label}", f"otsu_valid_{label}",
        ])
        for group in groups:
            for year in (2000, 2020):
                suffix = qa_test_suffix(group, year, label, args)
                qa.extend([
                    f"upper_{suffix}",
                    f"elev_delta_nonforest_minus_forest_{suffix}_m",
                    f"t_statistic_{suffix}",
                    f"forest_sample_count_{suffix}",
                    f"nonforest_sample_count_{suffix}",
                ])
    return {"treeline30m": bands30, "treeline1km": bands1k, "qa30m": qa}


def build_mountain_bundle(
    args: argparse.Namespace,
    selected: "ee.FeatureCollection",
    mountain: Mapping[str, object],
    fixed_thresholds: Mapping[str, Mapping[str, float]],
) -> Dict[str, object]:
    mountain_id = str(mountain["mountain_id"])
    feature = selected_feature(selected, mountain_id)
    common = build_common(feature, args)
    domain = ee.Image(common["domain"])
    mountain_mask = ee.Image(common["mountain_mask"])
    dem = ee.Image(common["dem"])
    nonvalley = ee.Image(common["nonvalley"])
    aspect_info = aspect_groups(dem, args) if args.aspect_mode == "polar-equator" else None
    group_masks: Dict[str, "ee.Image"] = (
        {"polar": aspect_info["polar"], "equator": aspect_info["equator"]}
        if aspect_info is not None else {"all": ee.Image.constant(1)}
    )
    treeline30m: Optional["ee.Image"] = None
    treeline1km: Optional["ee.Image"] = None
    qa: "ee.Image" = (
        domain.unmask(0).rename("analysis_domain")
        .addBands(nonvalley.unmask(0))
        .addBands(dem.rename("dem_elevation_m"))
        .addBands(ee.Image(common["aw3d"]).select("dem_msk").unmask(255))
        .addBands(ee.Image(common["aw3d"]).select("dem_stk").unmask(0))
    )
    if aspect_info is not None:
        qa = (
            qa.addBands(aspect_info["class"].unmask(0))
            .addBands(aspect_info["aspect"].unmask(-1))
            .addBands(aspect_info["slope"].unmask(-1))
        )
    otsu_infos: Dict[str, "ee.Dictionary"] = {}

    for canopy_threshold in normalized_canopy_thresholds(args.canopy_thresholds_m):
        label = threshold_label(canopy_threshold)
        forest2000_info = clean_forest(
            FOREST_HEIGHT_2000, canopy_threshold, common["processing_region"]
        )
        forest2020_info = clean_forest(
            FOREST_HEIGHT_2020, canopy_threshold, common["processing_region"]
        )
        forest2000 = ee.Image(forest2000_info["forest"])
        forest2020 = ee.Image(forest2020_info["forest"])
        edge2000 = forest_edges(forest2000, domain, args)
        edge2020 = forest_edges(forest2020, domain, args)
        post_landform2000 = edge2000.And(nonvalley).selfMask()
        post_landform2020 = edge2020.And(nonvalley).selfMask()
        pooled_edges = post_landform2000.Or(post_landform2020).selfMask()
        temp_graph = temperature_graph(pooled_edges, common["mountain_bounds"], args)
        temperature = ee.Image(temp_graph["temperature"])
        if args.otsu_scope == "mountain-fixed":
            if mountain_id not in fixed_thresholds:
                raise ValueError(f"missing fixed threshold entry for GMBA {mountain_id}")
            otsu = fixed_threshold_dictionary(fixed_thresholds[mountain_id][label], args)
        else:
            otsu = otsu_threshold_ee(
                temp_graph["histogram"], args.temperature_scale,
                args.temperature_offset, args.otsu_min_samples,
            )
        otsu_infos[label] = otsu
        valid = ee.Image.constant(ee.Number(otsu.get("valid"))).eq(1)
        cold = temperature.lte(ee.Number(otsu.get("threshold_c"))).rename(
            f"cold_zone_{label}"
        )
        qa = (
            qa.addBands(forest2000.rename(f"forest_clean_2000_{label}"))
            .addBands(forest2020.rename(f"forest_clean_2020_{label}"))
            .addBands(ee.Image(forest2000_info["forest_small_patch_removed"]).rename(
                f"forest_small_patch_removed_2000_{label}"
            ))
            .addBands(ee.Image(forest2020_info["forest_small_patch_removed"]).rename(
                f"forest_small_patch_removed_2020_{label}"
            ))
            .addBands(ee.Image(forest2000_info["nonforest_small_gap_filled"]).rename(
                f"nonforest_small_gap_filled_2000_{label}"
            ))
            .addBands(ee.Image(forest2020_info["nonforest_small_gap_filled"]).rename(
                f"nonforest_small_gap_filled_2020_{label}"
            ))
            .addBands(post_landform2000.unmask(0).rename(
                f"edge_post_landform_2000_{label}"
            ))
            .addBands(post_landform2020.unmask(0).rename(
                f"edge_post_landform_2020_{label}"
            ))
            .addBands(cold.unmask(0))
            .addBands(valid.rename(f"otsu_valid_{label}"))
        )
        for group_name, group_mask in group_masks.items():
            per_year: Dict[int, "ee.Image"] = {}
            for year, forest, post_landform in (
                (2000, forest2000, post_landform2000),
                (2020, forest2020, post_landform2020),
            ):
                candidates = post_landform.And(cold).And(group_mask).And(valid).selfMask()
                test = upper_edge_test(
                    forest, dem, candidates, mountain_mask.And(group_mask), args
                )
                prefix = "treeline" if group_name == "all" else f"treeline_{group_name}"
                band_name = f"{prefix}_{year}_{label}_m"
                elevation = dem.updateMask(test["upper"]).rename(band_name).toFloat()
                per_year[year] = elevation
                treeline30m = add_band(treeline30m, elevation)
                suffix = qa_test_suffix(group_name, year, label, args)
                qa = (
                    qa.addBands(test["upper"].unmask(0).rename(f"upper_{suffix}"))
                    .addBands(test["elevation_difference"].rename(
                        f"elev_delta_nonforest_minus_forest_{suffix}_m"
                    ))
                    .addBands(test["t_statistic"].rename(f"t_statistic_{suffix}"))
                    .addBands(test["forest_count"].rename(f"forest_sample_count_{suffix}"))
                    .addBands(test["nonforest_count"].rename(
                        f"nonforest_sample_count_{suffix}"
                    ))
                )
            name2000 = f"{prefix}_2000_{label}_mean_m"
            name2020 = f"{prefix}_2020_{label}_mean_m"
            elevation2000_1km = aggregate_to_climate_grid(per_year[2000]).rename(name2000)
            elevation2020_1km = aggregate_to_climate_grid(per_year[2020]).rename(name2020)
            shift_prefix = "shift" if group_name == "all" else f"shift_{group_name}"
            shift = elevation2020_1km.subtract(elevation2000_1km).divide(20).rename(
                f"{shift_prefix}_2000_2020_{label}_m_per_year"
            ).toFloat()
            treeline1km = add_band(treeline1km, elevation2000_1km)
            treeline1km = add_band(treeline1km, elevation2020_1km)
            treeline1km = add_band(treeline1km, shift)

    if treeline30m is None or treeline1km is None:
        raise ValueError("no canopy thresholds were resolved")
    metadata: Dict[str, object] = {
        "mountain_id": mountain_id,
        "region_id": str(mountain["region_id"]),
        "high_mountain_area_km2": float(mountain["high_mountain_area_km2"]),
        "analysis_unit": "GMBA_v2_Standard_Basic",
        "analysis_domain": "complete_GMBA_v2_Standard_Basic_geometry",
        "prepared_mountains_asset": args.prepared_mountains_asset,
        "sayre_role": "mountain_selection_only_via_reviewed_manifest",
        "source_bio01": args.chelsa_bio01,
        "source_forest_height_2000": FOREST_HEIGHT_2000,
        "source_forest_height_2020": FOREST_HEIGHT_2020,
        "source_dem": AW3D30,
        "source_landforms": ALOS_LANDFORMS,
        "run_label": args.run_label,
        "configuration_hash": configuration_hash(args),
        "git_commit": current_git_commit() or "unknown",
        "canopy_thresholds_m": ",".join(
            f"{value:g}" for value in normalized_canopy_thresholds(args.canopy_thresholds_m)
        ),
        "edge_method": args.edge_method,
        "workflow": "per-gmba-v4-jrc-mmu",
        "mmu_area_ha": JRC_MMU_HA,
        "mmu_connectivity": JRC_MMU_CONNECTIVITY,
        "mmu_area_measure": "sum_pixelArea_m2_per_connected_component",
        "mmu_operation_order": "remove_small_forest_then_fill_small_nonforest_gaps",
        "connected_component_max_size_pixels": CONNECTED_COMPONENT_MAX_SIZE,
        "connected_component_max_size_role": "compute_protection_only",
        "jrc_alignment": "binary_mmu_postprocessing_only",
        "forest_definition": "GLAD_canopy_height_threshold",
        "window_size_m": args.window_radius_m * 2,
        "t_test_variance": args.t_test_variance,
        "t_test_alternative": args.t_test_alternative,
        "minimum_samples_per_group": args.minimum_samples_per_group,
        "minimum_elevation_difference_m": args.minimum_elevation_difference_m,
        "otsu_scope": args.otsu_scope,
        "otsu_population": "pooled_2000_2020_post_landform_edge_native_bio01_cells",
        "otsu_same_threshold_for_both_years": True,
        "temperature_scale": args.temperature_scale,
        "temperature_offset": args.temperature_offset,
    }
    for label, otsu in otsu_infos.items():
        metadata[f"otsu_valid_{label}"] = otsu.get("valid")
        metadata[f"otsu_threshold_raw_{label}"] = otsu.get("threshold_raw")
        metadata[f"otsu_threshold_c_{label}"] = otsu.get("threshold_c")
        metadata[f"otsu_sample_count_{label}"] = otsu.get("candidate_sample_count")
        metadata[f"otsu_bucket_count_{label}"] = otsu.get("histogram_bucket_count")
    images = {
        "treeline30m": treeline30m.clip(common["mountain_bounds"]).toFloat(),
        "treeline1km": treeline1km.clip(common["mountain_bounds"]).toFloat(),
        "qa30m": qa.updateMask(mountain_mask).clip(common["mountain_bounds"]).toFloat(),
    }
    return {"common": common, "images": images, "metadata": metadata, "otsu": otsu_infos}


def make_asset_export_task(
    args: argparse.Namespace,
    record: Mapping[str, object],
    bundle: Mapping[str, object],
) -> "ee.batch.Task":
    return ee.batch.Export.image.toAsset(
        image=ee.Image(bundle["images"][str(record["product"])]).set(bundle["metadata"]),
        description=str(record["description"]),
        assetId=str(record["destination"]),
        pyramidingPolicy=dict(record["pyramiding_policy"]),
        region=bundle["common"]["mountain_bounds"],
        crs=str(record["crs"]),
        crsTransform=record["crs_transform"],
        maxPixels=1e13,
        overwrite=args.overwrite_assets,
    )


def choose_check_mountain(
    plan: Sequence[Mapping[str, object]], args: argparse.Namespace
) -> Mapping[str, object]:
    if not plan:
        raise ValueError("the selected mountain plan is empty")
    if args.check_mountain_id:
        matches = [item for item in plan if item["mountain_id"] == args.check_mountain_id]
        if not matches:
            raise ValueError(
                f"check mountain is absent from selected plan: {args.check_mountain_id}"
            )
        return matches[0]
    ordered = sorted(plan, key=lambda item: float(item["high_mountain_area_km2"]))
    if args.check_strategy == "smallest":
        return ordered[0]
    if args.check_strategy == "largest":
        return ordered[-1]
    return ordered[len(ordered) // 2]


def image_count(
    image: "ee.Image", region: "ee.Geometry", scale: float, args: argparse.Namespace
) -> Mapping[str, object]:
    return image.reduceRegion(
        ee.Reducer.count(), geometry=region, scale=scale,
        maxPixels=args.otsu_max_pixels, tileScale=args.tile_scale,
    ).getInfo()


def create_check_map(args: argparse.Namespace, bundle: Mapping[str, object]) -> None:
    map_object = geemap.Map()
    common = bundle["common"]
    map_object.centerObject(common["mountain_geometry"], 7)
    map_object.addLayer(common["feature"], {"color": "ffffff"}, "GMBA unit", True)
    map_object.addLayer(
        common["domain"], {"palette": ["f1c40f"]}, "Analysis domain", False
    )
    first_band = expected_product_bands(args)["treeline30m"][0]
    map_object.addLayer(
        bundle["images"]["treeline30m"].select(first_band),
        {"min": 0, "max": 5000, "palette": ["2c7bb6", "ffffbf", "d7191c"]},
        first_band,
        True,
    )
    args.map_html.parent.mkdir(parents=True, exist_ok=True)
    map_object.to_html(filename=str(args.map_html), title="Per-GMBA treeline check")


def run_check(args: argparse.Namespace) -> Dict[str, object]:
    auth = initialize_with_adc(args.project)
    prepared_info = ee.data.getAsset(args.prepared_mountains_asset)
    if prepared_info.get("type") != "TABLE":
        raise ValueError("--prepared-mountains-asset must be a TABLE Asset")
    targets = validate_target_collections(args)
    selected, plan, selection_summary = resolve_mountain_plan(args)
    fixed = load_fixed_mountain_thresholds(args)
    mountain = choose_check_mountain(plan, args)
    bundle = build_mountain_bundle(args, selected, mountain, fixed)
    records = planned_export_records(args, [mountain])
    tasks = [make_asset_export_task(args, record, bundle) for record in records]
    config_sizes = [len(json.dumps(task.config, default=str)) for task in tasks]
    otsu_report: Dict[str, object] = {
        "status": "deferred_to_export_task",
        "execution_feasibility_verified": False,
    }
    if args.deep_check:
        otsu_report = {
            "status": "evaluated",
            "execution_feasibility_verified": True,
            "thresholds": {
                label: ee.Dictionary(info).getInfo()
                for label, info in bundle["otsu"].items()
            },
        }
    pixel_counts: Optional[Mapping[str, object]] = None
    if args.pixel_counts:
        pixel_counts = image_count(
            bundle["images"]["treeline30m"],
            bundle["common"]["mountain_bounds"], 30, args,
        )
    if args.write_map:
        create_check_map(args, bundle)
    report = {
        "status": "per-gmba-plan-and-graph-preflight-passed",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "authentication": auth,
        "resolved_plan": resolved_plan(args),
        "prepared_asset": {
            "id": args.prepared_mountains_asset, "type": prepared_info.get("type"),
        },
        "selection": selection_summary,
        "check_mountain": dict(mountain),
        "check_strategy": args.check_strategy,
        "export_products": [record["product"] for record in records],
        "expected_product_bands": expected_product_bands(args),
        "serialized_task_config_bytes": config_sizes,
        "otsu": otsu_report,
        "pixel_counts": pixel_counts,
        "pixel_counts_status": "computed" if args.pixel_counts else "skipped_by_default",
        "map_html": str(args.map_html) if args.write_map else None,
        "targets": {
            product: {
                "id": summary["id"],
                "existing_child_count": summary["existing_child_count"],
            }
            for product, summary in targets.items()
        },
        "exports_started": False,
    }
    write_json_atomic(args.report_json, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def start_exports(args: argparse.Namespace) -> Path:
    auth = initialize_with_adc(args.project)
    targets = validate_target_collections(args)
    selected, plan, selection_summary = resolve_mountain_plan(args)
    fixed = load_fixed_mountain_thresholds(args)
    records = planned_export_records(args, plan)
    remote_tasks = ee.data.getTaskList()
    apply_resume_guards(
        records, targets, active_tasks_by_description(remote_tasks), args
    )
    queue_projection = enforce_ready_queue_limit(records, remote_tasks, args)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    registry = args.registry_dir / f"{timestamp}-{args.task_prefix}.json"
    payload: Dict[str, object] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "phase": "PREFLIGHT",
        "project": args.project,
        "authentication": auth,
        "resolved_plan": resolved_plan(args),
        "selection": selection_summary,
        "queue_projection_before_preflight": queue_projection,
        "configuration_hash": configuration_hash(args),
        "git_commit": current_git_commit(),
        "tasks": records,
    }
    write_json_atomic(registry, payload)
    records_by_mountain = {
        str(mountain["mountain_id"]): [
            record for record in records
            if record["mountain_id"] == mountain["mountain_id"]
        ]
        for mountain in plan
    }

    # Preflight every graph in the selected batch before starting any task.
    # Mountain offset/limit is safe: each output is keyed by one unique GMBA ID.
    for mountain in plan:
        mountain_records = [
            record for record in records_by_mountain[str(mountain["mountain_id"])]
            if record["state"] == "PLANNED"
        ]
        if not mountain_records:
            continue
        try:
            bundle = build_mountain_bundle(args, selected, mountain, fixed)
            preflight_tasks = [
                make_asset_export_task(args, record, bundle) for record in mountain_records
            ]
            for task, record in zip(preflight_tasks, mountain_records):
                if not task.config:
                    raise ValueError(f"empty export config for {record['description']}")
                record["state"] = "PREFLIGHTED"
            write_json_atomic(registry, payload)
        except Exception as error:
            for record in mountain_records:
                record["state"] = "FAILED_PREFLIGHT"
                record["error"] = f"{type(error).__name__}: {error}"
            payload["phase"] = "PREFLIGHT_FAILED"
            write_json_atomic(registry, payload)
            raise RuntimeError(f"preflight failed; see {registry}") from error

    refreshed_targets = validate_target_collections(args)
    refreshed_tasks = ee.data.getTaskList()
    apply_resume_guards(
        records, refreshed_targets, active_tasks_by_description(refreshed_tasks), args
    )
    payload["queue_projection_before_submit"] = enforce_ready_queue_limit(
        records, refreshed_tasks, args
    )
    payload["phase"] = "SUBMITTING"
    write_json_atomic(registry, payload)
    for mountain in plan:
        mountain_records = [
            record for record in records_by_mountain[str(mountain["mountain_id"])]
            if record["state"] == "PREFLIGHTED"
        ]
        if not mountain_records:
            continue
        try:
            bundle = build_mountain_bundle(args, selected, mountain, fixed)
            for record in mountain_records:
                task = make_asset_export_task(args, record, bundle)
                record["state"] = "CREATED"
                task.start()
                record["task_id"] = task.id
                record["state"] = "SUBMITTED"
            write_json_atomic(registry, payload)
        except Exception as error:
            for record in mountain_records:
                if record["state"] in {"PREFLIGHTED", "CREATED"}:
                    record["state"] = "FAILED_TO_START"
                    record["error"] = f"{type(error).__name__}: {error}"
            payload["phase"] = "SUBMIT_FAILED_RESUMABLE"
            write_json_atomic(registry, payload)
            raise RuntimeError(
                f"submission stopped; rerun with --resume; see {registry}"
            ) from error
    payload["phase"] = "SUBMITTED"
    write_json_atomic(registry, payload)
    print(json.dumps({
        "registry": str(registry),
        "mountain_count": len(plan),
        "task_count": len(records),
        "submitted_count": sum(record["state"] == "SUBMITTED" for record in records),
        "skipped_existing_count": sum(
            record["state"] == "SKIPPED_EXISTING" for record in records
        ),
        "skipped_active_count": sum(
            record["state"] == "SKIPPED_ACTIVE" for record in records
        ),
    }, ensure_ascii=False, indent=2))
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
        if remote is not None:
            task["task_id"] = remote.get("id") or remote.get("task_id")
            recovered += 1
    return recovered


def monitor_once(project: str, registry_path: Path) -> None:
    initialize_with_adc(project)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    recovered = recover_task_ids(registry, ee.data.getTaskList())
    task_ids = [task["task_id"] for task in registry["tasks"] if task.get("task_id")]
    states = ee.data.getTaskStatus(task_ids)
    counts: Dict[str, int] = {}
    details = []
    descriptions = {task["task_id"]: task["description"] for task in registry["tasks"]}
    for state in states:
        status = state.get("state", "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
        details.append({
            "task_id": state.get("id"),
            "description": descriptions.get(state.get("id")),
            "state": status,
            "error_message": state.get("error_message"),
        })
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
    try:
        normalized_canopy_thresholds(args.canopy_thresholds_m)
        sanitize_asset_component(args.run_label)
    except ValueError as error:
        parser.error(str(error))
    if args.max_mountains is not None and args.max_mountains < 1:
        parser.error("--max-mountains must be at least 1")
    if (
        args.export
        and (args.max_mountains is None or args.max_mountains > 100)
        and not args.allow_large_batch_submit
    ):
        parser.error(
            "--export requires --max-mountains between 1 and 100; use "
            "--allow-large-batch-submit only after a successful staged pilot"
        )
    if not 1 <= args.queue_safety_limit <= 3000:
        parser.error("--queue-safety-limit must be in [1,3000]")
    if args.mountain_offset < 0:
        parser.error("--mountain-offset must be non-negative")
    if args.mountain_offset and args.max_mountains is None:
        parser.error("--mountain-offset requires --max-mountains")
    if args.expected_mountain_count < 1 or args.expected_region_count < 1:
        parser.error("expected counts must be positive")
    if args.context_buffer_m <= 0:
        parser.error("--context-buffer-m must be positive")
    minimum_buffer = CONNECTED_COMPONENT_MAX_SIZE * 30 + args.window_radius_m
    if args.context_buffer_m < minimum_buffer:
        parser.error(
            f"--context-buffer-m must be at least {minimum_buffer:g} m for "
            "connected-component boundary protection and neighborhood operations"
        )
    if args.median_radius_pixels < 0:
        parser.error("--median-radius-pixels must be non-negative")
    if args.canny_threshold < 0 or args.canny_sigma < 0:
        parser.error("Canny threshold and sigma must be non-negative")
    if args.window_radius_m <= 0:
        parser.error("--window-radius-m must be positive")
    if args.minimum_samples_per_group < 2:
        parser.error("--minimum-samples-per-group must be at least 2")
    if args.minimum_elevation_difference_m < 0:
        parser.error("--minimum-elevation-difference-m must be non-negative")
    if args.otsu_min_samples < 2:
        parser.error("--otsu-min-samples must be at least 2")
    if not math.isfinite(args.temperature_scale) or args.temperature_scale <= 0:
        parser.error("--temperature-scale must be finite and positive")
    if not math.isfinite(args.temperature_offset):
        parser.error("--temperature-offset must be finite")
    if not 0 < args.aspect_half_width_deg <= 90:
        parser.error("--aspect-half-width-deg must be in (0,90]")
    if args.geometry_max_error_m <= 0:
        parser.error("--geometry-max-error-m must be positive")
    if args.table_max_vertices < 1000:
        parser.error("--table-max-vertices must be at least 1000")
    if args.resume and args.overwrite_assets:
        parser.error("--resume and --overwrite-assets are mutually exclusive")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    if not args.dry_run and RUNTIME_IMPORT_ERROR is not None:
        parser.error(
            "online modes require earthengine-api, geemap, and google-auth; "
            f"first missing import: {RUNTIME_IMPORT_ERROR.name}"
        )
    if args.dry_run:
        print(json.dumps(resolved_plan(args), ensure_ascii=False, indent=2))
        return 0
    missing = missing_requirements(args)
    if missing:
        parser.error("missing requirements: " + "; ".join(missing))
    if args.monitor_once:
        monitor_once(args.project, Path(args.monitor_once))
    elif args.prepare_mountains:
        prepare_mountains(args)
    elif args.check:
        run_check(args)
    elif args.export:
        start_exports(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
