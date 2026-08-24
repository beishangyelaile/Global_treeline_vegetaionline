#!/usr/bin/env python3
"""Step 1: build globally continuous, JRC-sequence forest tiles.

The offline dry-run never initializes Earth Engine. ``--check`` may inspect
assets and serialize graphs, but only ``--export`` can start tasks.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    import ee
    import google.auth
    from google.auth.transport.requests import Request
except ImportError as error:  # pragma: no cover - exercised only in minimal runtimes
    ee = None  # type: ignore[assignment]
    google = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]
    RUNTIME_IMPORT_ERROR: Optional[ImportError] = error
else:
    RUNTIME_IMPORT_ERROR = None


FOREST_HEIGHT_2000 = "projects/glad/GLCLU2020/Forest_height_2000"
FOREST_HEIGHT_2020 = "projects/glad/GLCLU2020/Forest_height_2020"
GMBA_ASSET = "projects/ee-remote/assets/Alpine/GMBA_v2"
CURRENT_MANIFEST_ASSET = (
    "projects/ee-wsc/assets/Alpine/GMBA_8regions_Sayre31_32_manifest"
)
GLOBAL_TREE_3M = "projects/ee-alpine-506212/assets/Global_tree_3m"
GLOBAL_TREE_5M = "projects/ee-alpine-506212/assets/Global_tree_5m"

MMU_AREA_M2 = 5000
MMU_MAX_SIZE = 50
MMU_CONNECTIVITY = 8
CANOPY_THRESHOLDS: Tuple[int, int] = (3, 5)
TILE_SIZE_DEGREES = 10
FINE_CRS = "EPSG:4326"
FINE_TRANSFORM = [0.00025, 0, -180, 0, -0.00025, 90]
WORKFLOW = "step1-global-forest-jrc-sequence-modified-ms50-v1"
WORKLOAD_TAG = "globaltreeline-step1"
ADC_SCOPES = tuple(ee.oauth.SCOPES) if ee is not None else ()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build globally continuous 10-degree binary forest tiles."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print an offline plan")
    mode.add_argument("--check", action="store_true", help="read-only GEE preflight")
    mode.add_argument("--export", action="store_true", help="start bounded exports")
    mode.add_argument(
        "--monitor-once", type=Path, metavar="REGISTRY", help="query one registry"
    )

    parser.add_argument("--project", default=os.environ.get("EE_PROJECT", "ee-wsc"))
    parser.add_argument("--gmba-asset", default=GMBA_ASSET)
    parser.add_argument("--current-manifest-asset", default=CURRENT_MANIFEST_ASSET)
    parser.add_argument("--tree3m-collection", default=GLOBAL_TREE_3M)
    parser.add_argument("--tree5m-collection", default=GLOBAL_TREE_5M)
    parser.add_argument("--latitude-min", type=int, default=-60)
    parser.add_argument("--latitude-max", type=int, default=80)
    parser.add_argument("--tile-manifest", type=Path)
    parser.add_argument(
        "--write-tile-manifest",
        type=Path,
        default=Path(__file__).with_name("step1_tile_manifest.json"),
    )
    parser.add_argument(
        "--report-json",
        type=Path,
        default=Path(__file__).with_name("step1_check_report.json"),
    )
    parser.add_argument("--max-tiles", type=int)
    parser.add_argument("--tile-offset", type=int, default=0)
    parser.add_argument("--queue-safety-limit", type=int, default=100)
    parser.add_argument(
        "--registry-dir",
        type=Path,
        default=Path(os.environ.get("GLOBALTREELINE_ARTIFACTS", "outputs/tasks")),
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-label", default="step1_ms50")
    parser.add_argument("--task-prefix", default="global_forest_tile")
    return parser


def source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


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
        "source_height_2000": FOREST_HEIGHT_2000,
        "source_height_2020": FOREST_HEIGHT_2020,
        "canopy_thresholds_m": list(CANOPY_THRESHOLDS),
        "threshold_comparison": "strict_gt",
        "mmu_max_size": MMU_MAX_SIZE,
        "mmu_area_m2": MMU_AREA_M2,
        "mmu_connectivity": MMU_CONNECTIVITY,
        "mmu_method": "JRC_sequence_modified_maxSize50",
        "mmu_operation_order": "fill_small_nonforest_then_remove_small_forest",
        "area_measure": "connected_pixel_count_times_pixelArea",
        "source_mask_policy": "preserve_original_valid_mask",
        "grid_crs": FINE_CRS,
        "grid_transform": list(FINE_TRANSFORM),
        "tile_size_degrees": TILE_SIZE_DEGREES,
        "source_sha256": source_sha256(),
    }


def configuration_hash(args: argparse.Namespace) -> str:
    encoded = json.dumps(
        scientific_configuration(args), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def latitude_range_is_global(latitude_min: int, latitude_max: int) -> bool:
    return latitude_min == -90 and latitude_max == 90


def coordinate_label(value: int, positive: str, negative: str, width: int) -> str:
    prefix = positive if value >= 0 else negative
    return f"{prefix}{abs(value):0{width}d}"


def tile_id(min_lon: int, min_lat: int) -> str:
    return (
        f"{coordinate_label(min_lat, 'N', 'S', 2)}_"
        f"{coordinate_label(min_lon, 'E', 'W', 3)}"
    )


def generate_regular_tiles(latitude_min: int, latitude_max: int) -> List[Dict[str, object]]:
    if latitude_min < -90 or latitude_max > 90 or latitude_min >= latitude_max:
        raise ValueError("latitude range must satisfy -90 <= min < max <= 90")
    if latitude_min % TILE_SIZE_DEGREES or latitude_max % TILE_SIZE_DEGREES:
        raise ValueError("latitude bounds must align to the 10-degree grid")
    tiles: List[Dict[str, object]] = []
    for min_lat in range(latitude_min, latitude_max, TILE_SIZE_DEGREES):
        for min_lon in range(-180, 180, TILE_SIZE_DEGREES):
            max_lon = min_lon + TILE_SIZE_DEGREES
            max_lat = min_lat + TILE_SIZE_DEGREES
            tiles.append(
                {
                    "tile_id": tile_id(min_lon, min_lat),
                    "bbox": [float(min_lon), float(min_lat), float(max_lon), float(max_lat)],
                    "min_lon": float(min_lon),
                    "max_lon": float(max_lon),
                    "min_lat": float(min_lat),
                    "max_lat": float(max_lat),
                }
            )
    return tiles


def load_tile_manifest(path: Optional[Path]) -> Optional[Dict[str, object]]:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    tiles = payload.get("tiles")
    if not isinstance(tiles, list):
        raise ValueError("tile manifest must contain a tiles list")
    identifiers = [str(tile.get("tile_id")) for tile in tiles]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("tile manifest contains duplicate tile IDs")
    for tile in tiles:
        if not isinstance(tile.get("bbox"), list) or len(tile["bbox"]) != 4:
            raise ValueError(f"tile has invalid bbox: {tile}")
    return payload


def select_tiles(
    tiles: Sequence[Mapping[str, object]], args: argparse.Namespace
) -> List[Dict[str, object]]:
    end = None if args.max_tiles is None else args.tile_offset + args.max_tiles
    return [dict(tile) for tile in tiles[args.tile_offset:end]]


def missing_requirements(args: argparse.Namespace) -> List[str]:
    missing: List[str] = []
    if args.tile_manifest is None:
        missing.append("tile_manifest")
    elif not args.tile_manifest.exists():
        missing.append("tile_manifest_not_found")
    return missing


def resolved_plan(args: argparse.Namespace) -> Dict[str, object]:
    manifest = load_tile_manifest(args.tile_manifest)
    available_tiles = [] if manifest is None else manifest["tiles"]
    selected = select_tiles(available_tiles, args)
    missing = missing_requirements(args)
    diagnostics = None if manifest is None else manifest.get("latitude_diagnostics")
    effective_latitude_min = (
        args.latitude_min if manifest is None else int(manifest.get("latitude_min", args.latitude_min))
    )
    effective_latitude_max = (
        args.latitude_max if manifest is None else int(manifest.get("latitude_max", args.latitude_max))
    )
    return {
        "status": "offline-step1-plan",
        "ready": not missing,
        "missing_requirements": missing,
        "project": args.project,
        "products": ["h3m", "h5m"],
        "outputs": {
            "h3m": args.tree3m_collection,
            "h5m": args.tree5m_collection,
        },
        "selection": {
            "tile_manifest": str(args.tile_manifest) if args.tile_manifest else None,
            "available_tile_count": len(available_tiles),
            "selected_tile_count": len(selected),
            "selected_tile_ids": [tile["tile_id"] for tile in selected],
            "tile_offset": args.tile_offset,
            "max_tiles": args.max_tiles,
            "latitude_min": effective_latitude_min,
            "latitude_max": effective_latitude_max,
            "complete_global_latitude_coverage": latitude_range_is_global(
                effective_latitude_min, effective_latitude_max
            ),
        },
        "latitude_diagnostics": diagnostics,
        "expected_task_count": len(selected) * 2,
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


def build_forest_year(canopy_asset: str, canopy_threshold_m: float) -> "ee.Image":
    canopy_height = ee.Image(canopy_asset).select([0])
    forest_raw = canopy_height.gt(canopy_threshold_m)
    return apply_jrc_mmu(forest_raw)


def apply_jrc_mmu(forest_raw: "ee.Image") -> "ee.Image":
    """Apply the fixed JRC operation sequence with modified maxSize=50."""
    proj = forest_raw.projection()
    valid_mask = forest_raw.mask()
    pixel_area = ee.Image.pixelArea().reproject(crs=proj)

    count1 = forest_raw.connectedPixelCount(
        maxSize=MMU_MAX_SIZE,
        eightConnected=True,
    ).reproject(crs=proj)
    area1 = count1.multiply(pixel_area)
    small_component = area1.lte(MMU_AREA_M2)

    forest_after_fill = forest_raw.add(small_component).gte(1)
    forest_after_fill_masked = forest_after_fill.selfMask()

    count2 = forest_after_fill_masked.connectedPixelCount(
        maxSize=MMU_MAX_SIZE,
        eightConnected=True,
    ).reproject(crs=proj)
    area2 = count2.multiply(pixel_area)
    forest_keep = area2.gte(MMU_AREA_M2)
    forest_clean_masked = forest_keep.updateMask(forest_keep)

    return forest_clean_masked.unmask(0).updateMask(valid_mask).toByte()


def build_threshold_product(canopy_threshold_m: int) -> "ee.Image":
    forest_2000 = build_forest_year(FOREST_HEIGHT_2000, canopy_threshold_m)
    forest_2020 = build_forest_year(FOREST_HEIGHT_2020, canopy_threshold_m)
    return ee.Image.cat(
        [forest_2000.rename("tree_2000"), forest_2020.rename("tree_2020")]
    ).toByte()


def tile_feature_collection(tiles: Sequence[Mapping[str, object]]) -> "ee.FeatureCollection":
    return ee.FeatureCollection(
        [
            ee.Feature(
                ee.Geometry.Rectangle(tile["bbox"], proj=FINE_CRS, geodesic=False),
                {key: value for key, value in tile.items() if key != "bbox"},
            ).set("bbox", json.dumps(tile["bbox"], separators=(",", ":")))
            for tile in tiles
        ]
    )


def add_gmba_join_id(feature: "ee.Feature") -> "ee.Feature":
    feature = ee.Feature(feature)
    return feature.set(
        "gmba_join_id", ee.Number(feature.get("GMBA_V2_ID")).format("%.0f")
    )


def resolve_valid_tiles_and_latitude_diagnostics(
    args: argparse.Namespace,
) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    candidates = generate_regular_tiles(args.latitude_min, args.latitude_max)
    gmba_basic = ee.FeatureCollection(args.gmba_asset).filter(
        ee.Filter.eq("MapUnit", "Basic")
    )
    def mark_valid_tile(feature: "ee.Feature") -> "ee.Feature":
        feature = ee.Feature(feature)
        intersects = gmba_basic.filterBounds(feature.geometry()).size().gt(0)
        return feature.set("intersects_gmba_basic", intersects)

    valid_info = (
        tile_feature_collection(candidates)
        .map(mark_valid_tile)
        .filter(ee.Filter.eq("intersects_gmba_basic", 1))
        .getInfo()
    )
    valid_ids = {str(feature["properties"]["tile_id"]) for feature in valid_info["features"]}
    valid_tiles = [tile for tile in candidates if str(tile["tile_id"]) in valid_ids]

    outside_regions = []
    if args.latitude_min > -90:
        outside_regions.append(
            ee.Geometry.Rectangle([-180, -90, 180, args.latitude_min], geodesic=False)
        )
    if args.latitude_max < 90:
        outside_regions.append(
            ee.Geometry.Rectangle([-180, args.latitude_max, 180, 90], geodesic=False)
        )
    if outside_regions:
        outside_geometry = outside_regions[0]
        for geometry in outside_regions[1:]:
            outside_geometry = outside_geometry.union(geometry, maxError=1000)
        outside_gmba = gmba_basic.filterBounds(outside_geometry).map(add_gmba_join_id)
        outside_ids = ee.List(outside_gmba.aggregate_array("gmba_join_id")).distinct()
        manifest_available = asset_exists(args.current_manifest_asset)
        outside_manifest = (
            ee.FeatureCollection(args.current_manifest_asset)
            .map(add_gmba_join_id)
            .filter(ee.Filter.inList("gmba_join_id", outside_ids))
            if manifest_available
            else None
        )
        valid_forest = ee.ImageCollection(
            [ee.Image(FOREST_HEIGHT_2000), ee.Image(FOREST_HEIGHT_2020)]
        ).max().gt(3)

        def mark_valid_forest(feature: "ee.Feature") -> "ee.Feature":
            present = valid_forest.reduceRegion(
                ee.Reducer.max(),
                feature.geometry(),
                scale=1000,
                bestEffort=True,
                maxPixels=1e8,
            ).values().get(0)
            present_or_zero = ee.Number(
                ee.Algorithms.If(ee.Algorithms.IsEqual(present, None), 0, present)
            )
            return ee.Feature(feature).set(
                "has_valid_forest", present_or_zero.gt(0)
            )

        outside_with_forest = outside_gmba.map(mark_valid_forest).filter(
            ee.Filter.eq("has_valid_forest", 1)
        )
        diagnostics = {
            "outside_range_gmba_count": int(outside_gmba.size().getInfo()),
            "outside_range_manifest_count": (
                int(outside_manifest.size().getInfo())
                if outside_manifest is not None
                else None
            ),
            "manifest_diagnostic_status": (
                "evaluated" if manifest_available else "asset_not_found"
            ),
            "outside_range_with_valid_forest_count": int(
                outside_with_forest.size().getInfo()
            ),
        }
    else:
        diagnostics = {
            "outside_range_gmba_count": 0,
            "outside_range_manifest_count": 0,
            "outside_range_with_valid_forest_count": 0,
            "manifest_diagnostic_status": "not_needed_for_complete_latitude_range",
        }
    diagnostics["range_requires_extension"] = bool(
        diagnostics["outside_range_manifest_count"]
        or diagnostics["outside_range_with_valid_forest_count"]
        or diagnostics["manifest_diagnostic_status"] == "asset_not_found"
    )
    return valid_tiles, diagnostics


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


def asset_exists(asset_id: str) -> bool:
    try:
        ee.data.getAsset(asset_id)
        return True
    except ee.EEException as error:
        message = str(error).lower()
        if "not found" in message or "does not exist" in message:
            return False
        raise


def validate_target_collections(args: argparse.Namespace) -> Dict[str, object]:
    summaries: Dict[str, object] = {}
    for product, asset_id in (
        ("h3m", args.tree3m_collection),
        ("h5m", args.tree5m_collection),
    ):
        info = ee.data.getAsset(asset_id)
        if info.get("type") != "IMAGE_COLLECTION":
            raise ValueError(f"{product} target must be IMAGE_COLLECTION")
        children = list_child_assets(asset_id)
        summaries[product] = {
            "id": asset_id,
            "type": info.get("type"),
            "children": {str(child.get("id")): child for child in children},
            "existing_child_count": len(children),
        }
    return summaries


def planned_export_records(
    args: argparse.Namespace, tiles: Sequence[Mapping[str, object]]
) -> List[Dict[str, object]]:
    config_hash = configuration_hash(args)
    records: List[Dict[str, object]] = []
    for tile in tiles:
        for threshold, product, collection in (
            (3, "h3m", args.tree3m_collection),
            (5, "h5m", args.tree5m_collection),
        ):
            identifier = str(tile["tile_id"])
            records.append(
                {
                    **dict(tile),
                    "product": product,
                    "canopy_threshold_m": threshold,
                    "description": f"GFC_{product}_ms50_{identifier}",
                    "destination": (
                        f"{collection.rstrip('/')}/GFC_2000_2020_{identifier}"
                    ),
                    "pyramiding_policy": {
                        ".default": "mode",
                        "tree_2000": "mode",
                        "tree_2020": "mode",
                    },
                    "configuration_hash": config_hash,
                    "state": "PLANNED",
                    "task_id": None,
                }
            )
    return records


def asset_metadata(args: argparse.Namespace, record: Mapping[str, object]) -> Dict[str, object]:
    return {
        "canopy_threshold_m": record["canopy_threshold_m"],
        "mmu_max_size": MMU_MAX_SIZE,
        "mmu_area_m2": MMU_AREA_M2,
        "mmu_connectivity": MMU_CONNECTIVITY,
        "mmu_method": "JRC_sequence_modified_maxSize50",
        "source_height_2000": FOREST_HEIGHT_2000,
        "source_height_2020": FOREST_HEIGHT_2020,
        "source_years": "2000,2020",
        "tile_id": record["tile_id"],
        "bbox": json.dumps(record["bbox"], separators=(",", ":")),
        "grid_crs": FINE_CRS,
        "grid_transform": json.dumps(FINE_TRANSFORM, separators=(",", ":")),
        "configuration_hash": configuration_hash(args),
        "implementation_sha256": source_sha256(),
        "run_label": args.run_label,
        "git_commit": current_git_commit() or "unknown",
    }


def make_asset_export_task(
    args: argparse.Namespace,
    record: Mapping[str, object],
    product: "ee.Image",
) -> "ee.batch.Task":
    region_geom = ee.Geometry.Rectangle(record["bbox"], proj=FINE_CRS, geodesic=False)
    return ee.batch.Export.image.toAsset(
        image=product.set(asset_metadata(args, record)),
        description=str(record["description"]),
        assetId=str(record["destination"]),
        region=region_geom,
        crs=FINE_CRS,
        crsTransform=FINE_TRANSFORM,
        maxPixels=1e13,
        pyramidingPolicy={
            ".default": "mode",
            "tree_2000": "mode",
            "tree_2020": "mode",
        },
    )


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def run_check(args: argparse.Namespace) -> Dict[str, object]:
    auth = initialize_with_adc(args.project)
    targets = validate_target_collections(args)
    gmba_info = ee.data.getAsset(args.gmba_asset)
    if gmba_info.get("type") != "TABLE":
        raise ValueError("--gmba-asset must be a TABLE")
    valid_tiles, requested_diagnostics = resolve_valid_tiles_and_latitude_diagnostics(args)
    effective_args = args
    auto_expanded = bool(
        requested_diagnostics["range_requires_extension"]
        and not latitude_range_is_global(args.latitude_min, args.latitude_max)
    )
    if auto_expanded:
        effective_args = argparse.Namespace(**vars(args))
        effective_args.latitude_min = -90
        effective_args.latitude_max = 90
        valid_tiles, effective_diagnostics = resolve_valid_tiles_and_latitude_diagnostics(
            effective_args
        )
    else:
        effective_diagnostics = requested_diagnostics
    diagnostics = {
        "requested_range": {
            "latitude_min": args.latitude_min,
            "latitude_max": args.latitude_max,
            **requested_diagnostics,
        },
        "auto_expanded_to_minus90_plus90": auto_expanded,
        "effective_range": {
            "latitude_min": effective_args.latitude_min,
            "latitude_max": effective_args.latitude_max,
            **effective_diagnostics,
        },
    }
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": args.project,
        "gmba_asset": args.gmba_asset,
        "latitude_min": effective_args.latitude_min,
        "latitude_max": effective_args.latitude_max,
        "latitude_diagnostics": diagnostics,
        "configuration_hash": configuration_hash(effective_args),
        "tiles": valid_tiles,
    }
    write_json_atomic(args.write_tile_manifest, manifest)

    sample = valid_tiles[0] if valid_tiles else None
    serialized: List[int] = []
    if sample is not None:
        for record in planned_export_records(args, [sample]):
            product = build_threshold_product(int(record["canopy_threshold_m"]))
            serialized.append(len(json.dumps(make_asset_export_task(args, record, product).config, default=str)))
    report = {
        "status": "step1-read-only-graph-preflight-passed",
        "exports_started": False,
        "authentication": auth,
        "gmba_asset": {"id": args.gmba_asset, "type": gmba_info.get("type")},
        "candidate_tile_count": len(
            generate_regular_tiles(effective_args.latitude_min, effective_args.latitude_max)
        ),
        "valid_tile_count": len(valid_tiles),
        "latitude_diagnostics": diagnostics,
        "range_accepted": not effective_diagnostics["range_requires_extension"],
        "tile_manifest": str(args.write_tile_manifest),
        "serialized_task_config_bytes": serialized,
        "configuration_hash": configuration_hash(effective_args),
        "targets": {
            product: {
                "id": summary["id"],
                "type": summary["type"],
                "existing_child_count": summary["existing_child_count"],
            }
            for product, summary in targets.items()
        },
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
                raise ValueError("existing Asset found; use --resume after verifying configuration")
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
    remote_tasks: Sequence[Mapping[str, object]],
    limit: int,
) -> Dict[str, int]:
    ready = sum(task.get("state") == "READY" for task in remote_tasks)
    planned = sum(record.get("state") == "PREFLIGHTED" for record in records)
    if ready + planned > limit:
        raise ValueError(
            f"queue safety limit exceeded: READY {ready} + new {planned} > {limit}"
        )
    return {"existing_ready": ready, "new_tasks": planned, "projected_ready": ready + planned}


def start_exports(args: argparse.Namespace) -> Path:
    initialize_with_adc(args.project)
    manifest = load_tile_manifest(args.tile_manifest)
    assert manifest is not None
    selected = select_tiles(manifest["tiles"], args)
    if not selected:
        raise ValueError("selected tile plan is empty")
    targets = validate_target_collections(args)
    remote_tasks = ee.data.getTaskList()
    records = planned_export_records(args, selected)
    apply_resume_guards(records, targets, active_tasks_by_description(remote_tasks), args)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    registry = args.registry_dir / f"{timestamp}-{args.task_prefix}.json"
    payload: Dict[str, object] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "phase": "PREFLIGHT",
        "project": args.project,
        "configuration_hash": configuration_hash(args),
        "tile_manifest": str(args.tile_manifest),
        "tasks": records,
    }
    write_json_atomic(registry, payload)

    products = {threshold: build_threshold_product(threshold) for threshold in CANOPY_THRESHOLDS}
    tasks: List[Tuple[Dict[str, object], "ee.batch.Task"]] = []
    for record in records:
        if record["state"] != "PLANNED":
            continue
        task = make_asset_export_task(
            args, record, products[int(record["canopy_threshold_m"])]
        )
        if not task.config:
            raise ValueError(f"empty export configuration: {record['description']}")
        record["state"] = "PREFLIGHTED"
        tasks.append((record, task))
    payload["queue_projection"] = enforce_queue_limit(
        records, remote_tasks, args.queue_safety_limit
    )
    write_json_atomic(registry, payload)

    payload["phase"] = "SUBMITTING"
    write_json_atomic(registry, payload)
    for record, task in tasks:
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
    details: List[Dict[str, object]] = []
    records = {record.get("task_id"): record for record in registry["tasks"]}
    for state in states:
        status = str(state.get("state", "UNKNOWN"))
        counts[status] = counts.get(status, 0) + 1
        record = records.get(state.get("id"), {})
        details.append(
            {
                "task_id": state.get("id"),
                "tile_id": record.get("tile_id"),
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
    if args.latitude_min < -90 or args.latitude_max > 90 or args.latitude_min >= args.latitude_max:
        parser.error("latitude range must satisfy -90 <= min < max <= 90")
    if args.latitude_min % 10 or args.latitude_max % 10:
        parser.error("latitude bounds must align to the 10-degree grid")
    if args.tile_offset < 0:
        parser.error("--tile-offset must be non-negative")
    if args.max_tiles is not None and args.max_tiles < 1:
        parser.error("--max-tiles must be at least 1")
    if args.tile_offset and args.max_tiles is None:
        parser.error("--tile-offset requires --max-tiles")
    if not 1 <= args.queue_safety_limit <= 3000:
        parser.error("--queue-safety-limit must be in [1,3000]")
    if args.export:
        errors = []
        if args.tile_manifest is None:
            errors.append("--tile-manifest")
        if args.max_tiles is None:
            errors.append("--max-tiles")
        if errors:
            parser.error("--export requires " + " and ".join(errors))


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
