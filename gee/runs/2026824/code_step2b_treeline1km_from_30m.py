#!/usr/bin/env python3
"""Step 2B: aggregate validated materialized treeline30m Assets to 30 arc-seconds."""

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


WORKFLOW = "step2b-materialized-treeline30m-to-30arcsec-v2"
WORKLOAD_TAG = "globaltreeline-step2b"
ADC_SCOPES = tuple(ee.oauth.SCOPES) if ee is not None else ()
SOURCE_TREELINE30M_COLLECTION = (
    "projects/ee-alpine-506212/assets/Treeline_30m_Collection_v2"
)
SOURCE_QA30M_COLLECTION = (
    "projects/ee-alpine-506212/assets/Treeline_QA30m_Collection_v2"
)
TARGET_TREELINE1KM_COLLECTION = (
    "projects/ee-alpine-506212/assets/Treeline_1km_Collection_v2"
)
ANALYSIS_MOUNTAINS_ASSET = "projects/ee-wsc/assets/Alpine/GMBA_Sayre"
FINE_CRS = "EPSG:4326"
FINE_TRANSFORM = [0.00025, 0, -180, 0, -0.00025, 90]
CLIMATE_CRS = "EPSG:4326"
CLIMATE_TRANSFORM = [1 / 120, 0, -180, 0, -1 / 120, 90]
AGGREGATION_MAX_PIXELS = 2048
AGGREGATION_BEST_EFFORT = False
AGGREGATION_METHOD = "mean_of_materialized_treeline30m_valid_pixels"
SOURCE_BANDS = [
    "treeline_2000_h3m_m",
    "treeline_2020_h3m_m",
    "treeline_2000_h5m_m",
    "treeline_2020_h5m_m",
]
OUTPUT_BANDS = [
    "treeline_2000_h3m_mean_m",
    "treeline_2020_h3m_mean_m",
    "shift_2000_2020_h3m_m_per_year",
    "treeline_2000_h5m_mean_m",
    "treeline_2020_h5m_mean_m",
    "shift_2000_2020_h5m_m_per_year",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build Step 2B treeline1km from completed treeline30m Assets."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--diagnose", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--export", action="store_true")
    mode.add_argument("--monitor-once", type=Path, metavar="REGISTRY")

    parser.add_argument("--project", default=os.environ.get("EE_PROJECT", "ee-wsc"))
    parser.add_argument("--source-registry", type=Path)
    parser.add_argument(
        "--analysis-mountains-asset",
        default=os.environ.get("GMBA_SAYRE_ASSET", ANALYSIS_MOUNTAINS_ASSET),
    )
    parser.add_argument(
        "--source-treeline30m-collection",
        default=SOURCE_TREELINE30M_COLLECTION,
    )
    parser.add_argument("--source-qa30m-collection", default=SOURCE_QA30M_COLLECTION)
    parser.add_argument(
        "--target-treeline1km-collection",
        default=TARGET_TREELINE1KM_COLLECTION,
    )

    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--failed-only", action="store_true")
    selector.add_argument("--all-eligible", action="store_true")
    selector.add_argument("--mountain-ids", nargs="+")
    parser.add_argument("--max-mountains", type=int)
    parser.add_argument("--mountain-offset", type=int, default=0)
    parser.add_argument("--queue-safety-limit", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--run-label", default="step2_1km_from30m_v2_20260831"
    )
    parser.add_argument("--task-prefix", default="step2b_treeline1km")
    parser.add_argument(
        "--registry-dir",
        type=Path,
        default=Path(os.environ.get("GLOBALTREELINE_ARTIFACTS", "outputs/tasks")),
    )
    parser.add_argument("--report-json", type=Path)
    return parser


def implementation_sha256() -> str:
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


def sanitize_asset_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")
    if not component:
        raise ValueError(f"empty Asset component: {value!r}")
    return component


def load_source_registry(path: Path) -> Dict[str, object]:
    if not path.is_file():
        raise ValueError(f"source registry not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks")
    source_hash = str(payload.get("configuration_hash") or "")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("source registry has no tasks")
    if not source_hash:
        raise ValueError("source registry lacks configuration_hash")
    task_hashes = {str(task.get("configuration_hash") or "") for task in tasks}
    if task_hashes != {source_hash}:
        raise ValueError("source registry contains inconsistent configuration hashes")
    return payload


def source_records_by_mountain(
    registry: Mapping[str, object],
) -> Dict[str, Dict[str, Mapping[str, object]]]:
    grouped: Dict[str, Dict[str, Mapping[str, object]]] = {}
    for task in registry["tasks"]:  # type: ignore[index]
        mountain_id = str(task.get("mountain_id") or "")
        product = str(task.get("product") or "")
        if not mountain_id or not product:
            raise ValueError("source registry task lacks mountain_id or product")
        products = grouped.setdefault(mountain_id, {})
        if product in products:
            raise ValueError(f"duplicate source task for {mountain_id}/{product}")
        products[product] = task
    return grouped


def latest_statuses(registry: Mapping[str, object]) -> Dict[str, Mapping[str, object]]:
    monitor = registry.get("last_monitor") or {}
    return {
        str(item.get("task_id") or item.get("id") or ""): item
        for item in monitor.get("tasks", [])
        if item.get("task_id") or item.get("id")
    }


def normalized_task_status(
    record: Mapping[str, object],
    statuses: Mapping[str, Mapping[str, object]],
) -> Dict[str, object]:
    task_id = str(record.get("task_id") or "")
    remote = statuses.get(task_id, record)
    attempt = remote.get("attempt_count")
    if attempt is None:
        attempt = remote.get("attempt")
    if attempt is None:
        attempt = record.get("attempt_count")
    if attempt is None:
        attempt = record.get("attempt")
    try:
        attempt_count = int(attempt) if attempt is not None else None
    except (TypeError, ValueError):
        attempt_count = None
    return {
        "task_id": task_id,
        "task_state": str(remote.get("state") or "UNKNOWN"),
        "attempt_count": attempt_count,
        "error_message": str(
            remote.get("error_message")
            or remote.get("error")
            or record.get("error_message")
            or record.get("error")
            or ""
        ),
    }


def is_out_of_memory_error(error_message: object) -> bool:
    message = str(error_message or "").lower()
    return "out of memory" in message or "memory limit" in message


def classify_failed_task(
    product: str,
    task_state: str,
    error_message: object,
    source_treeline30m_completed: bool,
) -> Optional[str]:
    if task_state != "FAILED":
        return None
    oom = is_out_of_memory_error(error_message)
    if product == "treeline1km" and oom:
        if source_treeline30m_completed:
            return "treeline1km_oom_with_completed_treeline30m"
        return "treeline1km_oom_without_completed_treeline30m"
    if product == "treeline30m" and oom:
        return "treeline30m_oom"
    if product == "qa30m" and oom:
        return "qa30m_oom"
    return "other_error"


def _numeric_mountain_key(value: str) -> tuple[int, str]:
    try:
        return (int(value), value)
    except ValueError:
        return (sys.maxsize, value)


def resolve_offline_mountain_ids(
    args: argparse.Namespace, registry: Mapping[str, object]
) -> List[str]:
    grouped = source_records_by_mountain(registry)
    if args.mountain_ids:
        requested = list(dict.fromkeys(map(str, args.mountain_ids)))
        missing = sorted(set(requested) - set(grouped), key=_numeric_mountain_key)
        if missing:
            raise ValueError(f"mountain IDs absent from source registry: {missing}")
        selected = requested
    elif args.failed_only:
        statuses = latest_statuses(registry)
        selected = []
        for mountain_id, products in grouped.items():
            direct = products.get("treeline1km")
            source = products.get("treeline30m")
            if direct is None or source is None:
                continue
            direct_status = normalized_task_status(direct, statuses)
            source_status = normalized_task_status(source, statuses)
            if (
                direct_status["task_state"] == "FAILED"
                and is_out_of_memory_error(direct_status["error_message"])
                and source_status["task_state"] == "COMPLETED"
            ):
                selected.append(mountain_id)
    else:
        selected = [
            mountain_id
            for mountain_id, products in grouped.items()
            if "treeline30m" in products
        ]
    selected = sorted(selected, key=_numeric_mountain_key)
    start = args.mountain_offset
    stop = None if args.max_mountains is None else start + args.max_mountains
    return selected[start:stop]


def expected_output_bands() -> List[str]:
    return list(OUTPUT_BANDS)


def _aligned_grid_signature(grid: Mapping[str, object]) -> tuple[object, ...]:
    affine = grid.get("affineTransform")
    if not isinstance(affine, Mapping):
        raise ValueError("source treeline30m band lacks affineTransform")
    try:
        scale_x = float(affine["scaleX"])
        shear_x = float(affine.get("shearX", 0))
        translate_x = float(affine["translateX"])
        shear_y = float(affine.get("shearY", 0))
        scale_y = float(affine["scaleY"])
        translate_y = float(affine["translateY"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("source treeline30m band has an invalid affineTransform") from exc

    if grid.get("crsCode") != FINE_CRS:
        raise ValueError("source treeline30m CRS does not match the fixed fine grid")
    expected = FINE_TRANSFORM
    if not math.isclose(scale_x, expected[0], rel_tol=0, abs_tol=1e-12):
        raise ValueError("source treeline30m x scale does not match the fixed fine grid")
    if not math.isclose(scale_y, expected[4], rel_tol=0, abs_tol=1e-12):
        raise ValueError("source treeline30m y scale does not match the fixed fine grid")
    if not math.isclose(shear_x, expected[1], rel_tol=0, abs_tol=1e-12):
        raise ValueError("source treeline30m x shear is not zero")
    if not math.isclose(shear_y, expected[3], rel_tol=0, abs_tol=1e-12):
        raise ValueError("source treeline30m y shear is not zero")

    x_index = (translate_x - expected[2]) / expected[0]
    y_index = (translate_y - expected[5]) / expected[4]
    if not math.isclose(x_index, round(x_index), rel_tol=0, abs_tol=1e-6):
        raise ValueError("source treeline30m x origin is not aligned to the fine grid")
    if not math.isclose(y_index, round(y_index), rel_tol=0, abs_tol=1e-6):
        raise ValueError("source treeline30m y origin is not aligned to the fine grid")
    return (
        grid.get("crsCode"),
        scale_x,
        shear_x,
        translate_x,
        shear_y,
        scale_y,
        translate_y,
    )


def validate_source_treeline30m_asset(
    asset_info: Optional[Mapping[str, object]],
    source_record: Mapping[str, object],
    *,
    expected_run_label: Optional[str] = None,
) -> Dict[str, object]:
    if not asset_info:
        raise ValueError("source treeline30m Asset does not exist")
    if asset_info.get("type") != "IMAGE":
        raise ValueError("source treeline30m Asset must be an IMAGE")
    try:
        size_bytes = int(asset_info.get("sizeBytes") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("source treeline30m Asset has invalid sizeBytes") from exc
    if size_bytes <= 0:
        raise ValueError("source treeline30m Asset is empty")

    destination = str(source_record.get("destination") or "")
    asset_id = str(asset_info.get("name") or asset_info.get("id") or "")
    if not destination or asset_id != destination:
        raise ValueError("source treeline30m Asset identity does not match the registry")

    bands = asset_info.get("bands")
    if not isinstance(bands, list):
        raise ValueError("source treeline30m Asset lacks band metadata")
    band_ids = [str(band.get("id") or "") for band in bands]
    if band_ids != SOURCE_BANDS:
        raise ValueError("source treeline30m bands or band order do not match")
    grid_signatures = [
        _aligned_grid_signature(band.get("grid") or {}) for band in bands
    ]
    if len(set(grid_signatures)) != 1:
        raise ValueError("source treeline30m bands do not share one aligned grid")

    properties = asset_info.get("properties")
    if not isinstance(properties, Mapping):
        raise ValueError("source treeline30m Asset lacks provenance properties")
    mountain_id = str(source_record.get("mountain_id") or "")
    source_hash = str(source_record.get("configuration_hash") or "")
    source_run_label = str(properties.get("run_label") or "")
    source_git_commit = str(properties.get("git_commit") or "")
    source_workflow = str(properties.get("workflow") or "")
    if not mountain_id or str(properties.get("mountain_id") or "") != mountain_id:
        raise ValueError("source treeline30m mountain_id does not match the registry")
    if not source_hash or str(properties.get("configuration_hash") or "") != source_hash:
        raise ValueError(
            "source treeline30m configuration_hash does not match the registry"
        )
    if not source_run_label:
        raise ValueError("source treeline30m run_label is missing")
    if expected_run_label is not None and source_run_label != expected_run_label:
        raise ValueError("source treeline30m run_label does not match")
    if not source_git_commit:
        raise ValueError("source treeline30m git_commit is missing")
    if not source_workflow:
        raise ValueError("source treeline30m workflow is missing")

    expected_child = "_".join(
        (
            f"gmba_{sanitize_asset_component(mountain_id)}",
            sanitize_asset_component(source_run_label),
            source_hash[:10],
        )
    )
    if destination.rsplit("/", 1)[-1] != expected_child:
        raise ValueError("source treeline30m child name does not match its provenance")

    return {
        "source_treeline30m_asset": destination,
        "source_step2_configuration_hash": source_hash,
        "source_step2_run_label": source_run_label,
        "source_step2_git_commit": source_git_commit,
        "source_step2_workflow": source_workflow,
        "source_size_bytes": size_bytes,
        "source_band_ids": band_ids,
        "source_grid": list(grid_signatures[0]),
    }


def resolve_online_mountain_ids(
    args: argparse.Namespace,
    registry: Mapping[str, object],
    statuses: Mapping[str, Mapping[str, object]],
    source_asset_infos: Mapping[str, Optional[Mapping[str, object]]],
) -> Tuple[List[str], Dict[str, Dict[str, object]]]:
    grouped = source_records_by_mountain(registry)
    eligibility: Dict[str, Dict[str, object]] = {}
    for mountain_id, products in grouped.items():
        source_record = products.get("treeline30m")
        direct_record = products.get("treeline1km")
        source_status = (
            normalized_task_status(source_record, statuses)
            if source_record is not None
            else {
                "task_id": "",
                "task_state": "MISSING",
                "attempt_count": None,
                "error_message": "source registry has no treeline30m record",
            }
        )
        direct_status = (
            normalized_task_status(direct_record, statuses)
            if direct_record is not None
            else {
                "task_id": "",
                "task_state": "MISSING",
                "attempt_count": None,
                "error_message": "",
            }
        )
        provenance: Optional[Dict[str, object]] = None
        validation_error = ""
        if source_record is None:
            validation_error = "source registry has no treeline30m record"
        else:
            try:
                provenance = validate_source_treeline30m_asset(
                    source_asset_infos.get(mountain_id), source_record
                )
            except ValueError as error:
                validation_error = str(error)
        source_valid = provenance is not None
        eligible = (
            source_status["task_state"] == "COMPLETED" and source_valid
        )
        direct_oom = (
            direct_status["task_state"] == "FAILED"
            and is_out_of_memory_error(direct_status["error_message"])
        )
        eligibility[mountain_id] = {
            "mountain_id": mountain_id,
            "source_treeline30m_task_state": source_status["task_state"],
            "source_treeline30m_valid": source_valid,
            "source_treeline30m_validation_error": validation_error,
            "eligible_for_materialized_1km": eligible,
            "direct_treeline1km_task_state": direct_status["task_state"],
            "direct_treeline1km_error_message": direct_status["error_message"],
            "direct_treeline1km_oom": direct_oom,
            "provenance": provenance,
        }

    if args.mountain_ids:
        requested = list(dict.fromkeys(map(str, args.mountain_ids)))
        absent = sorted(set(requested) - set(grouped), key=_numeric_mountain_key)
        if absent:
            raise ValueError(f"mountain IDs absent from source registry: {absent}")
        blocked = [
            mountain_id
            for mountain_id in requested
            if not eligibility[mountain_id]["eligible_for_materialized_1km"]
        ]
        if blocked:
            reasons = {
                mountain_id: {
                    "task_state": eligibility[mountain_id][
                        "source_treeline30m_task_state"
                    ],
                    "validation_error": eligibility[mountain_id][
                        "source_treeline30m_validation_error"
                    ],
                }
                for mountain_id in blocked
            }
            raise ValueError(f"requested mountains are not Step 2B eligible: {reasons}")
        selected = requested
    elif args.failed_only:
        selected = [
            mountain_id
            for mountain_id, item in eligibility.items()
            if item["eligible_for_materialized_1km"]
            and item["direct_treeline1km_oom"]
        ]
    else:
        selected = [
            mountain_id
            for mountain_id, item in eligibility.items()
            if item["eligible_for_materialized_1km"]
        ]
    selected = sorted(selected, key=_numeric_mountain_key)
    start = args.mountain_offset
    stop = None if args.max_mountains is None else start + args.max_mountains
    return selected[start:stop], eligibility


def build_treeline1km_from_30m(source_treeline30m_asset: str):
    source = ee.Image(source_treeline30m_asset).select(SOURCE_BANDS)
    means = source.reduceResolution(
        reducer=ee.Reducer.mean(),
        bestEffort=AGGREGATION_BEST_EFFORT,
        maxPixels=AGGREGATION_MAX_PIXELS,
    ).reproject(ee.Projection(CLIMATE_CRS, CLIMATE_TRANSFORM))

    mean_2000_h3m = means.select("treeline_2000_h3m_m").rename(
        "treeline_2000_h3m_mean_m"
    )
    mean_2020_h3m = means.select("treeline_2020_h3m_m").rename(
        "treeline_2020_h3m_mean_m"
    )
    shift_h3m = mean_2020_h3m.subtract(mean_2000_h3m).divide(20).rename(
        "shift_2000_2020_h3m_m_per_year"
    )
    mean_2000_h5m = means.select("treeline_2000_h5m_m").rename(
        "treeline_2000_h5m_mean_m"
    )
    mean_2020_h5m = means.select("treeline_2020_h5m_m").rename(
        "treeline_2020_h5m_mean_m"
    )
    shift_h5m = mean_2020_h5m.subtract(mean_2000_h5m).divide(20).rename(
        "shift_2000_2020_h5m_m_per_year"
    )
    return ee.Image.cat(
        [
            mean_2000_h3m,
            mean_2020_h3m,
            shift_h3m,
            mean_2000_h5m,
            mean_2020_h5m,
            shift_h5m,
        ]
    ).toFloat()


def serialized_export_expression_bytes(task: object) -> int:
    config = getattr(task, "config", None)
    if not isinstance(config, Mapping):
        raise ValueError("export task lacks a configuration")
    expression = config.get("expression")
    export_options = config.get("assetExportOptions")
    if expression is None or not export_options:
        raise ValueError("export task configuration is incomplete")
    encoded = expression.serialize(pretty=False, for_cloud_api=True)
    return len(encoded.encode("utf-8"))


def make_step2b_export_task(record: Mapping[str, object]):
    source_asset = str(record["source_treeline30m_asset"])
    image = build_treeline1km_from_30m(source_asset).set(record["metadata"])
    return ee.batch.Export.image.toAsset(
        image=image,
        description=str(record["description"]),
        assetId=str(record["destination"]),
        pyramidingPolicy=dict(record["pyramiding_policy"]),
        region=ee.Image(source_asset).geometry(),
        crs=str(record["crs"]),
        crsTransform=record["crs_transform"],
        maxPixels=1e13,
    )


def explicit_overlap_weighted_means(
    target_bounds: Sequence[float],
    fine_features: Sequence[Mapping[str, object]],
) -> Dict[str, object]:
    if len(target_bounds) != 4:
        raise ValueError("target cell bounds must contain four coordinates")
    left, bottom, right, top = map(float, target_bounds)
    fine_width = abs(float(FINE_TRANSFORM[0]))
    fine_height = abs(float(FINE_TRANSFORM[4]))
    weighted_sums = {band: 0.0 for band in SOURCE_BANDS}
    weight_sums = {band: 0.0 for band in SOURCE_BANDS}
    overlap_count = 0
    for feature in fine_features:
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if len(coordinates) < 2:
            raise ValueError("fine sample feature lacks point coordinates")
        center_x = float(coordinates[0])
        center_y = float(coordinates[1])
        overlap_x = max(
            0.0,
            min(center_x + fine_width / 2, right)
            - max(center_x - fine_width / 2, left),
        )
        overlap_y = max(
            0.0,
            min(center_y + fine_height / 2, top)
            - max(center_y - fine_height / 2, bottom),
        )
        weight = min(
            1.0,
            max(0.0, (overlap_x * overlap_y) / (fine_width * fine_height)),
        )
        if weight <= 0:
            continue
        overlap_count += 1
        properties = feature.get("properties") or {}
        for band in SOURCE_BANDS:
            value = properties.get(band)
            if value is None:
                continue
            weighted_sums[band] += float(value) * weight
            weight_sums[band] += weight
    missing = [band for band, weight in weight_sums.items() if weight <= 0]
    if missing:
        raise ValueError(f"independent sample has no valid weight for bands: {missing}")
    return {
        "fine_sample_count": len(fine_features),
        "fine_overlap_count": overlap_count,
        "weight_sums": weight_sums,
        "means": {
            band: weighted_sums[band] / weight_sums[band]
            for band in SOURCE_BANDS
        },
    }


def validated_comparison_asset(
    asset_info: Mapping[str, object], label: str
) -> str:
    asset_id = _asset_id(asset_info)
    if asset_info.get("type") != "IMAGE":
        raise ValueError(f"{label} comparison Asset is not an IMAGE: {asset_id}")
    try:
        size_bytes = int(asset_info.get("sizeBytes") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} comparison Asset has invalid sizeBytes") from exc
    if size_bytes <= 0:
        raise ValueError(f"{label} comparison Asset is empty: {asset_id}")
    bands = [str(band.get("id") or "") for band in asset_info.get("bands", [])]
    if bands != OUTPUT_BANDS:
        raise ValueError(f"{label} comparison Asset bands do not match Step 2B output")
    return asset_id


def _band_dictionary(
    raw: Mapping[str, object], *, integer: bool = False
) -> Dict[str, object]:
    if integer:
        return {band: int(raw.get(band) or 0) for band in OUTPUT_BANDS}
    return {band: raw.get(band) for band in OUTPUT_BANDS}


def compare_direct_and_materialized(
    record: Mapping[str, object],
    direct_asset_info: Mapping[str, object],
    region: object,
    materialized_asset_info: Optional[Mapping[str, object]] = None,
) -> Dict[str, object]:
    direct_asset = validated_comparison_asset(direct_asset_info, "direct")

    source_asset = str(record["source_treeline30m_asset"])
    old = ee.Image(direct_asset).select(OUTPUT_BANDS)
    materialized_asset = None
    if materialized_asset_info is None:
        new = build_treeline1km_from_30m(source_asset).select(OUTPUT_BANDS)
    else:
        materialized_asset = validated_comparison_asset(
            materialized_asset_info, "Step 2B materialized"
        )
        if materialized_asset != str(record["destination"]):
            raise ValueError("Step 2B comparison Asset does not match planned destination")
        new = ee.Image(materialized_asset).select(OUTPUT_BANDS)
    old_band_mask = old.mask().unmask(0, sameFootprint=False).gt(0)
    new_band_mask = new.mask().unmask(0, sameFootprint=False).gt(0)
    pairwise_mask = old_band_mask.And(new_band_mask)
    pairwise_difference = new.subtract(old).updateMask(pairwise_mask)
    old_joint = old_band_mask.reduce(ee.Reducer.min()).gt(0)
    new_joint = new_band_mask.reduce(ee.Reducer.min()).gt(0)
    joint_mask = old_joint.And(new_joint)
    complete_case_difference = new.subtract(old).updateMask(joint_mask)
    count_image = ee.Image.cat(
        [
            old_joint.selfMask().rename("direct_valid"),
            new_joint.selfMask().rename("from30m_valid"),
            old_joint.neq(new_joint).selfMask().rename("mask_mismatch"),
        ]
    )
    reduce_kwargs = {
        "geometry": region,
        "crs": CLIMATE_CRS,
        "crsTransform": CLIMATE_TRANSFORM,
        "bestEffort": False,
        "maxPixels": 500_000,
        "tileScale": 2,
    }
    raw_values = ee.Dictionary(
        {
            "complete_case_all_six": ee.Dictionary(
                {
                    "counts": count_image.reduceRegion(
                        reducer=ee.Reducer.count(), **reduce_kwargs
                    ),
                    "mean_difference_new_minus_direct_by_band": (
                        complete_case_difference.reduceRegion(
                            reducer=ee.Reducer.mean(), **reduce_kwargs
                        )
                    ),
                    "maximum_absolute_difference_by_band": (
                        complete_case_difference.abs().reduceRegion(
                            reducer=ee.Reducer.max(), **reduce_kwargs
                        )
                    ),
                }
            ),
            "per_band_pairwise": ee.Dictionary(
                {
                    "direct_valid_by_band": old_band_mask.selfMask().reduceRegion(
                        reducer=ee.Reducer.count(), **reduce_kwargs
                    ),
                    "from30m_valid_by_band": new_band_mask.selfMask().reduceRegion(
                        reducer=ee.Reducer.count(), **reduce_kwargs
                    ),
                    "pairwise_valid_by_band": pairwise_mask.selfMask().reduceRegion(
                        reducer=ee.Reducer.count(), **reduce_kwargs
                    ),
                    "mask_mismatch_by_band": (
                        old_band_mask.neq(new_band_mask).selfMask().reduceRegion(
                            reducer=ee.Reducer.count(), **reduce_kwargs
                        )
                    ),
                    "mean_difference_new_minus_direct_by_band": (
                        pairwise_difference.reduceRegion(
                            reducer=ee.Reducer.mean(), **reduce_kwargs
                        )
                    ),
                    "maximum_absolute_difference_by_band": (
                        pairwise_difference.abs().reduceRegion(
                            reducer=ee.Reducer.max(), **reduce_kwargs
                        )
                    ),
                }
            ),
        }
    ).getInfo()
    complete_case = raw_values.get("complete_case_all_six") or {}
    counts = complete_case.get("counts") or {}
    complete_case["counts"] = {
        "direct_valid": int(counts.get("direct_valid") or 0),
        "from30m_valid": int(counts.get("from30m_valid") or 0),
        "mask_mismatch": int(counts.get("mask_mismatch") or 0),
    }
    for key in (
        "mean_difference_new_minus_direct_by_band",
        "maximum_absolute_difference_by_band",
    ):
        complete_case[key] = _band_dictionary(complete_case.get(key) or {})
    complete_case["validity_definition"] = "all_six_bands"

    pairwise = raw_values.get("per_band_pairwise") or {}
    for key in (
        "direct_valid_by_band",
        "from30m_valid_by_band",
        "pairwise_valid_by_band",
        "mask_mismatch_by_band",
    ):
        pairwise[key] = _band_dictionary(pairwise.get(key) or {}, integer=True)
    for key in (
        "mean_difference_new_minus_direct_by_band",
        "maximum_absolute_difference_by_band",
    ):
        pairwise[key] = _band_dictionary(pairwise.get(key) or {})
    pairwise["validity_definition"] = "same_band_pairwise_overlap"

    return {
        "status": "compared",
        "mountain_id": record["mountain_id"],
        "old_direct_asset": direct_asset,
        "new_from30m_asset": materialized_asset,
        "new_from30m_virtual_graph": materialized_asset is None,
        "validity_definition": "all_six_bands",
        "bands": list(OUTPUT_BANDS),
        "grid": {"crs": CLIMATE_CRS, "transform": list(CLIMATE_TRANSFORM)},
        "limits": {"max_pixels": 500_000, "tile_scale": 2},
        "counts": complete_case["counts"],
        "mean_difference_new_minus_direct_by_band": complete_case[
            "mean_difference_new_minus_direct_by_band"
        ],
        "maximum_absolute_difference_by_band": complete_case[
            "maximum_absolute_difference_by_band"
        ],
        "complete_case_all_six": complete_case,
        "per_band_pairwise": pairwise,
    }


def independent_overlap_sample_check(
    record: Mapping[str, object],
    region: object,
    *,
    seed: int = 20260831,
    sample_count: int = 3,
    max_fine_features: int = 4000,
    mean_tolerance_m: float = 1e-3,
    shift_tolerance_m_per_year: float = 1e-4,
) -> Dict[str, object]:
    if not 1 <= sample_count <= 3:
        raise ValueError("independent sample_count must be in [1,3]")
    source_asset = str(record["source_treeline30m_asset"])
    projection = ee.Projection(CLIMATE_CRS, CLIMATE_TRANSFORM)
    virtual = build_treeline1km_from_30m(source_asset).select(OUTPUT_BANDS)
    valid = virtual.mask().reduce(ee.Reducer.min()).gt(0)
    candidates = (
        ee.Image.pixelCoordinates(projection)
        .addBands(virtual)
        .addBands(ee.Image.constant(1).toInt().rename("class"))
        .updateMask(valid)
    )
    targets = candidates.stratifiedSample(
        numPoints=sample_count,
        classBand="class",
        region=region,
        projection=projection,
        seed=seed,
        geometries=True,
        tileScale=2,
    )
    target_features = targets.getInfo().get("features", [])
    if not target_features:
        raise ValueError("independent check found no valid 30 arc-second cells")

    climate_size = abs(float(CLIMATE_TRANSFORM[0]))
    fine_width = abs(float(FINE_TRANSFORM[0]))
    fine_height = abs(float(FINE_TRANSFORM[4]))
    source = ee.Image(source_asset).select(SOURCE_BANDS)
    merged = ee.FeatureCollection([])
    cells: Dict[str, Dict[str, object]] = {}

    def tag_cell(cell_id: str):
        def tag(feature):
            return ee.Feature(feature).set("independent_cell_id", cell_id)

        return tag

    for index, feature in enumerate(target_features):
        cell_id = str(index)
        center = feature["geometry"]["coordinates"]
        center_x, center_y = map(float, center[:2])
        bounds = [
            center_x - climate_size / 2,
            center_y - climate_size / 2,
            center_x + climate_size / 2,
            center_y + climate_size / 2,
        ]
        expanded = ee.Geometry.Rectangle(
            [
                bounds[0] - fine_width / 2,
                bounds[1] - fine_height / 2,
                bounds[2] + fine_width / 2,
                bounds[3] + fine_height / 2,
            ],
            proj=FINE_CRS,
            geodesic=False,
        )
        fine = source.sample(
            region=expanded,
            projection=source.projection(),
            dropNulls=False,
            geometries=True,
            tileScale=2,
        )
        merged = merged.merge(fine.map(tag_cell(cell_id)))
        cells[cell_id] = {
            "bounds": bounds,
            "center": [center_x, center_y],
            "target_properties": feature.get("properties") or {},
        }

    fine_features = merged.getInfo().get("features", [])
    if len(fine_features) > max_fine_features:
        raise ValueError(
            f"independent check exceeded fine feature limit: {len(fine_features)}"
        )
    fine_by_cell: Dict[str, List[Mapping[str, object]]] = {
        cell_id: [] for cell_id in cells
    }
    for feature in fine_features:
        properties = feature.get("properties") or {}
        cell_id = str(properties.get("independent_cell_id") or "")
        if cell_id in fine_by_cell:
            fine_by_cell[cell_id].append(feature)

    source_to_output = {
        "treeline_2000_h3m_m": "treeline_2000_h3m_mean_m",
        "treeline_2020_h3m_m": "treeline_2020_h3m_mean_m",
        "treeline_2000_h5m_m": "treeline_2000_h5m_mean_m",
        "treeline_2020_h5m_m": "treeline_2020_h5m_mean_m",
    }
    max_errors = {band: 0.0 for band in OUTPUT_BANDS}
    passed_by_band = {band: True for band in OUTPUT_BANDS}
    cell_reports = []
    for cell_id, cell in cells.items():
        overlap = explicit_overlap_weighted_means(
            cell["bounds"], fine_by_cell[cell_id]
        )
        source_means = overlap["means"]
        independent = {
            output: float(source_means[source_band])
            for source_band, output in source_to_output.items()
        }
        independent["shift_2000_2020_h3m_m_per_year"] = (
            independent["treeline_2020_h3m_mean_m"]
            - independent["treeline_2000_h3m_mean_m"]
        ) / 20
        independent["shift_2000_2020_h5m_m_per_year"] = (
            independent["treeline_2020_h5m_mean_m"]
            - independent["treeline_2000_h5m_mean_m"]
        ) / 20
        target_properties = cell["target_properties"]
        step2b = {
            band: float(target_properties[band]) for band in OUTPUT_BANDS
        }
        absolute_error = {
            band: abs(step2b[band] - independent[band])
            for band in OUTPUT_BANDS
        }
        cell_passed = True
        for band, error in absolute_error.items():
            tolerance = (
                shift_tolerance_m_per_year
                if band.startswith("shift_")
                else mean_tolerance_m
            )
            max_errors[band] = max(max_errors[band], error)
            within = error <= tolerance
            passed_by_band[band] = passed_by_band[band] and within
            cell_passed = cell_passed and within
        cell_reports.append(
            {
                "cell_id": cell_id,
                "x": target_properties.get("x"),
                "y": target_properties.get("y"),
                "center_lonlat": cell["center"],
                "bounds": cell["bounds"],
                "fine_sample_count": overlap["fine_sample_count"],
                "fine_overlap_count": overlap["fine_overlap_count"],
                "step2b": step2b,
                "independent": independent,
                "absolute_error": absolute_error,
                "passed": cell_passed,
            }
        )
    passed = all(passed_by_band.values())
    return {
        "status": "passed" if passed else "failed",
        "method": "explicit_source_pixel_target_cell_overlap_weighted_mean",
        "mountain_id": record["mountain_id"],
        "seed": seed,
        "requested_sample_count": sample_count,
        "actual_sample_count": len(cell_reports),
        "tolerances": {
            "mean_m": mean_tolerance_m,
            "shift_m_per_year": shift_tolerance_m_per_year,
        },
        "limits": {
            "max_cells_per_mountain": 3,
            "max_fine_features": max_fine_features,
        },
        "max_abs_error_by_band": max_errors,
        "passed_by_band": passed_by_band,
        "cells": cell_reports,
    }


def aggregation_configuration(source_step2_configuration_hash: str) -> Dict[str, object]:
    return {
        "workflow": WORKFLOW,
        "implementation_sha256": implementation_sha256(),
        "source_step2_configuration_hash": source_step2_configuration_hash,
        "aggregation_method": AGGREGATION_METHOD,
        "aggregation_input_crs": FINE_CRS,
        "aggregation_input_transform": list(FINE_TRANSFORM),
        "aggregation_output_crs": CLIMATE_CRS,
        "aggregation_output_transform": list(CLIMATE_TRANSFORM),
        "aggregation_max_pixels": AGGREGATION_MAX_PIXELS,
        "aggregation_best_effort": AGGREGATION_BEST_EFFORT,
        "source_bands": list(SOURCE_BANDS),
        "output_bands": expected_output_bands(),
    }


def aggregation_configuration_hash(source_step2_configuration_hash: str) -> str:
    encoded = json.dumps(
        aggregation_configuration(source_step2_configuration_hash),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def planned_step2b_record(
    *,
    mountain_id: str,
    source_record: Mapping[str, object],
    provenance: Mapping[str, object],
    recovery_of_task_id: Optional[str],
    run_label: str,
    task_prefix: str,
    target_collection: str,
) -> Dict[str, object]:
    source_hash = str(source_record.get("configuration_hash") or "")
    if source_hash != provenance.get("source_step2_configuration_hash"):
        raise ValueError("validated source provenance and registry hash differ")
    source_run_label = str(provenance.get("source_step2_run_label") or "")
    if run_label == source_run_label:
        raise ValueError(
            "Step 2B run label must differ from the source Step 2A run label"
        )
    aggregation_hash = aggregation_configuration_hash(source_hash)
    mountain_key = sanitize_asset_component(f"gmba_{mountain_id}")
    child = sanitize_asset_component(
        f"{mountain_key}_{run_label}_{aggregation_hash[:10]}"
    )
    description = sanitize_asset_component(
        f"{task_prefix}_{run_label}_{mountain_key}_{aggregation_hash[:10]}"
    )
    implementation_hash = implementation_sha256()
    metadata = {
        "workflow": WORKFLOW,
        "mountain_id": str(mountain_id),
        "source_treeline30m_asset": provenance["source_treeline30m_asset"],
        "source_step2_configuration_hash": source_hash,
        "source_step2_run_label": provenance["source_step2_run_label"],
        "source_step2_git_commit": provenance["source_step2_git_commit"],
        "aggregation_method": AGGREGATION_METHOD,
        "aggregation_input_crs": FINE_CRS,
        "aggregation_input_transform": list(FINE_TRANSFORM),
        "aggregation_output_crs": CLIMATE_CRS,
        "aggregation_output_transform": list(CLIMATE_TRANSFORM),
        "aggregation_max_pixels": AGGREGATION_MAX_PIXELS,
        "aggregation_best_effort": AGGREGATION_BEST_EFFORT,
        "aggregation_configuration_hash": aggregation_hash,
        "implementation_sha256": implementation_hash,
        "git_commit": current_git_commit() or "unknown",
        "recovery_of_task_id": str(recovery_of_task_id or "none"),
        "run_label": run_label,
    }
    return {
        "mountain_id": str(mountain_id),
        "mountain_key": mountain_key,
        "product": "treeline1km",
        "source_treeline30m_asset": provenance["source_treeline30m_asset"],
        "source_step2_configuration_hash": source_hash,
        "aggregation_configuration_hash": aggregation_hash,
        "description": description,
        "destination": f"{target_collection.rstrip('/')}/{child}",
        "crs": CLIMATE_CRS,
        "crs_transform": list(CLIMATE_TRANSFORM),
        "pyramiding_policy": {".default": "mean"},
        "metadata": metadata,
        "state": "PLANNED",
        "task_id": None,
    }


def apply_target_asset_guard(
    records: Sequence[Dict[str, object]],
    target_assets: Mapping[str, Mapping[str, object]],
    resume: bool,
) -> None:
    for record in records:
        destination = str(record["destination"])
        remote = target_assets.get(destination)
        if remote is None:
            continue
        properties = remote.get("properties") or {}
        remote_hash = properties.get("aggregation_configuration_hash")
        expected_hash = record["aggregation_configuration_hash"]
        if remote_hash != expected_hash:
            raise ValueError(
                f"target Asset exists with a different aggregation hash: {destination}"
            )
        if not resume:
            raise ValueError(
                f"target Asset already exists; use --resume to skip it: {destination}"
            )
        record["state"] = "SKIPPED_EXISTING"


def active_tasks_by_description(
    tasks: Iterable[Mapping[str, object]],
) -> Dict[str, Mapping[str, object]]:
    return {
        str(task["description"]): task
        for task in tasks
        if task.get("description")
        and task.get("state") in {"READY", "RUNNING"}
    }


def apply_active_task_guard(
    records: Sequence[Dict[str, object]],
    active_tasks: Mapping[str, Mapping[str, object]],
    resume: bool,
) -> None:
    for record in records:
        if record["state"] != "PLANNED":
            continue
        active = active_tasks.get(str(record["description"]))
        if active is None:
            continue
        if not resume:
            raise ValueError("matching READY/RUNNING task found; use --resume")
        record["state"] = "SKIPPED_ACTIVE"
        record["task_id"] = active.get("id") or active.get("task_id")


def fetch_target_asset_inventory(
    collection: str, records: Sequence[Mapping[str, object]]
) -> Dict[str, Mapping[str, object]]:
    return fetch_existing_asset_details(
        collection, [str(record["destination"]) for record in records]
    )


def fetch_existing_asset_details(
    collection: str, destinations: Sequence[str]
) -> Dict[str, Mapping[str, object]]:
    validate_image_collection(collection)
    listed = {
        _asset_id(info): info
        for info in list_child_assets(collection)
        if _asset_id(info)
    }
    return {
        destination: ee.data.getAsset(destination)
        for destination in set(destinations)
        if destination in listed
    }


def preflight_export_tasks(
    records: Sequence[Dict[str, object]],
) -> Dict[str, object]:
    tasks: Dict[str, object] = {}
    for record in records:
        if record["state"] != "PLANNED":
            continue
        task = make_step2b_export_task(record)
        size = serialized_export_expression_bytes(task)
        record["serialized_expression_bytes"] = size
        record["state"] = "PREFLIGHTED"
        tasks[str(record["destination"])] = task
    return tasks


def enforce_queue_limit(
    records: Sequence[Mapping[str, object]],
    remote_tasks: Sequence[Mapping[str, object]],
    limit: int,
) -> Dict[str, int]:
    ready = sum(task.get("state") == "READY" for task in remote_tasks)
    new = sum(record.get("state") == "PREFLIGHTED" for record in records)
    if ready + new > limit:
        raise ValueError(
            f"queue safety limit exceeded: READY {ready} + new {new} > {limit}"
        )
    return {
        "existing_ready": ready,
        "new_tasks": new,
        "projected_ready": ready + new,
    }


def build_diagnostic_rows(
    registry: Mapping[str, object],
    statuses: Mapping[str, Mapping[str, object]],
    assets_by_id: Mapping[str, Optional[Mapping[str, object]]],
    geometry_metrics: Mapping[str, Mapping[str, object]],
) -> List[Dict[str, object]]:
    grouped = source_records_by_mountain(registry)
    rows: List[Dict[str, object]] = []
    for task in registry["tasks"]:  # type: ignore[index]
        mountain_id = str(task.get("mountain_id") or "")
        product = str(task.get("product") or "")
        products = grouped[mountain_id]
        source_record = products.get("treeline30m")
        qa_record = products.get("qa30m")
        source_asset_id = (
            str(source_record.get("destination") or "")
            if source_record is not None
            else ""
        )
        source_info = assets_by_id.get(source_asset_id)
        source_status = (
            normalized_task_status(source_record, statuses)
            if source_record is not None
            else {
                "task_state": "MISSING",
                "attempt_count": None,
                "error_message": "source registry has no treeline30m record",
            }
        )
        source_validation_error = ""
        source_valid = False
        if source_record is not None:
            try:
                validate_source_treeline30m_asset(source_info, source_record)
                source_valid = True
            except ValueError as error:
                source_validation_error = str(error)
        status = normalized_task_status(task, statuses)
        source_completed = source_status["task_state"] == "COMPLETED"
        failure_category = classify_failed_task(
            product,
            str(status["task_state"]),
            status["error_message"],
            source_completed,
        )
        destination = str(task.get("destination") or "")
        qa_asset_id = (
            str(qa_record.get("destination") or "") if qa_record is not None else ""
        )
        metric = geometry_metrics.get(mountain_id, {})
        rows.append(
            {
                "mountain_id": mountain_id,
                "product": product,
                "task_id": status["task_id"],
                "task_state": status["task_state"],
                "attempt_count": status["attempt_count"],
                "error_message": status["error_message"],
                "destination": destination,
                "destination_exists": assets_by_id.get(destination) is not None,
                "source_treeline30m_asset": source_asset_id,
                "source_treeline30m_exists": source_info is not None,
                "source_treeline30m_task_state": source_status["task_state"],
                "source_treeline30m_valid": source_valid,
                "source_treeline30m_validation_error": source_validation_error,
                "source_qa30m_exists": assets_by_id.get(qa_asset_id) is not None,
                "gmba_area_km2": metric.get("gmba_area_km2"),
                "bounds_area_km2": metric.get("bounds_area_km2"),
                "bounds_to_gmba_area_ratio": metric.get(
                    "bounds_to_gmba_area_ratio"
                ),
                "required_step1_tile_count": metric.get(
                    "required_step1_tile_count"
                ),
                "eligible_for_materialized_1km": source_completed and source_valid,
                "failure_category": failure_category,
            }
        )
    return rows


FAILURE_CATEGORIES = (
    "treeline1km_oom_with_completed_treeline30m",
    "treeline1km_oom_without_completed_treeline30m",
    "treeline30m_oom",
    "qa30m_oom",
    "other_error",
)


def failure_category_counts(
    rows: Iterable[Mapping[str, object]],
) -> Dict[str, int]:
    counts = {category: 0 for category in FAILURE_CATEGORIES}
    for row in rows:
        category = row.get("failure_category")
        if category in counts:
            counts[str(category)] += 1
    return counts


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def write_new_diagnostic_report(
    path: Path,
    payload: Mapping[str, object],
    *,
    source_registry: Path,
) -> None:
    if path.resolve() == source_registry.resolve():
        raise ValueError("diagnostic report must not overwrite the source registry")
    if path.exists():
        raise ValueError(f"diagnostic report already exists: {path}")
    write_json_atomic(path, payload)


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


def fetch_latest_task_statuses(
    registry: Mapping[str, object],
    *,
    task_ids: Optional[Sequence[str]] = None,
    chunk_size: int = 50,
) -> Dict[str, Mapping[str, object]]:
    identifiers = (
        list(task_ids)
        if task_ids is not None
        else [
            str(task.get("task_id"))
            for task in registry["tasks"]  # type: ignore[index]
            if task.get("task_id")
        ]
    )
    statuses: Dict[str, Mapping[str, object]] = {}
    for start in range(0, len(identifiers), chunk_size):
        for item in ee.data.getTaskStatus(
            identifiers[start : start + chunk_size]
        ):
            task_id = str(item.get("id") or item.get("task_id") or "")
            if task_id:
                statuses[task_id] = item
    fallback = latest_statuses(registry)
    for task_id in identifiers:
        if task_id not in statuses and task_id in fallback:
            statuses[task_id] = fallback[task_id]
    return statuses


def selection_task_ids(
    args: argparse.Namespace, registry: Mapping[str, object]
) -> List[str]:
    grouped = source_records_by_mountain(registry)
    identifiers: List[str] = []
    if args.mountain_ids:
        selected = list(dict.fromkeys(map(str, args.mountain_ids)))
        products = ("treeline30m", "treeline1km", "qa30m")
    elif args.failed_only:
        selected = list(grouped)
        products = ("treeline30m", "treeline1km")
    else:
        selected = list(grouped)
        products = ("treeline30m",)
    for mountain_id in selected:
        for product in products:
            record = grouped.get(mountain_id, {}).get(product)
            if record is not None and record.get("task_id"):
                identifiers.append(str(record["task_id"]))
    return identifiers


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


def _asset_id(info: Mapping[str, object]) -> str:
    return str(info.get("id") or info.get("name") or "")


def validate_image_collection(asset_id: str) -> Mapping[str, object]:
    info = ee.data.getAsset(asset_id)
    if info.get("type") != "IMAGE_COLLECTION":
        raise ValueError(f"expected IMAGE_COLLECTION: {asset_id}")
    return info


def fetch_registry_asset_inventory(
    args: argparse.Namespace, registry: Mapping[str, object]
) -> Dict[str, Optional[Mapping[str, object]]]:
    collections = {
        "treeline30m": args.source_treeline30m_collection,
        "treeline1km": args.target_treeline1km_collection,
        "qa30m": args.source_qa30m_collection,
    }
    grouped_destinations: Dict[str, List[str]] = {
        product: [] for product in collections
    }
    for task in registry["tasks"]:  # type: ignore[index]
        product = str(task.get("product") or "")
        if product not in collections:
            continue
        destination = str(task.get("destination") or "")
        parent = collections[product].rstrip("/") + "/"
        if not destination.startswith(parent):
            raise ValueError(
                f"source registry destination is outside configured {product} collection"
            )
        grouped_destinations[product].append(destination)

    inventory: Dict[str, Optional[Mapping[str, object]]] = {}
    for product, collection in collections.items():
        validate_image_collection(collection)
        listed = {
            _asset_id(info): info
            for info in list_child_assets(collection)
            if _asset_id(info)
        }
        for destination in grouped_destinations[product]:
            summary = listed.get(destination)
            if summary is None:
                inventory[destination] = None
            elif product == "treeline30m":
                inventory[destination] = ee.data.getAsset(destination)
            else:
                inventory[destination] = summary
    return inventory


def source_infos_by_mountain(
    registry: Mapping[str, object],
    assets_by_id: Mapping[str, Optional[Mapping[str, object]]],
) -> Dict[str, Optional[Mapping[str, object]]]:
    grouped = source_records_by_mountain(registry)
    return {
        mountain_id: assets_by_id.get(
            str(products.get("treeline30m", {}).get("destination") or "")
        )
        for mountain_id, products in grouped.items()
    }


def preselect_online_candidate_ids(
    args: argparse.Namespace,
    registry: Mapping[str, object],
    statuses: Mapping[str, Mapping[str, object]],
) -> List[str]:
    grouped = source_records_by_mountain(registry)
    if args.mountain_ids:
        requested = list(dict.fromkeys(map(str, args.mountain_ids)))
        absent = sorted(set(requested) - set(grouped), key=_numeric_mountain_key)
        if absent:
            raise ValueError(f"mountain IDs absent from source registry: {absent}")
        return requested
    if args.failed_only:
        candidates = []
        for mountain_id, products in grouped.items():
            source = products.get("treeline30m")
            direct = products.get("treeline1km")
            if source is None or direct is None:
                continue
            source_status = normalized_task_status(source, statuses)
            direct_status = normalized_task_status(direct, statuses)
            if (
                source_status["task_state"] == "COMPLETED"
                and direct_status["task_state"] == "FAILED"
                and is_out_of_memory_error(direct_status["error_message"])
            ):
                candidates.append(mountain_id)
        return sorted(candidates, key=_numeric_mountain_key)
    return sorted(
        (
            mountain_id
            for mountain_id, products in grouped.items()
            if "treeline30m" in products
        ),
        key=_numeric_mountain_key,
    )


def fetch_selected_source_asset_infos(
    args: argparse.Namespace,
    registry: Mapping[str, object],
    mountain_ids: Sequence[str],
) -> Dict[str, Optional[Mapping[str, object]]]:
    validate_image_collection(args.source_treeline30m_collection)
    listed = {
        _asset_id(info): info
        for info in list_child_assets(args.source_treeline30m_collection)
        if _asset_id(info)
    }
    grouped = source_records_by_mountain(registry)
    result: Dict[str, Optional[Mapping[str, object]]] = {}
    parent = args.source_treeline30m_collection.rstrip("/") + "/"
    for mountain_id in mountain_ids:
        source = grouped[mountain_id].get("treeline30m")
        if source is None:
            result[mountain_id] = None
            continue
        destination = str(source.get("destination") or "")
        if not destination.startswith(parent):
            raise ValueError(
                "source registry destination is outside the configured treeline30m collection"
            )
        result[mountain_id] = (
            ee.data.getAsset(destination) if destination in listed else None
        )
    return result


def load_step1_manifest(path: Path) -> Mapping[str, object]:
    if not path.is_file():
        raise ValueError(f"Step 1 manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    tiles = payload.get("tiles")
    if not isinstance(tiles, list) or not tiles:
        raise ValueError("Step 1 manifest has no tiles")
    tile_ids = []
    for tile in tiles:
        tile_id = str(tile.get("tile_id") or "")
        bbox = tile.get("bbox")
        if not tile_id or not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("Step 1 manifest contains an invalid tile")
        tile_ids.append(tile_id)
    if len(tile_ids) != len(set(tile_ids)):
        raise ValueError("Step 1 manifest contains duplicate tile_id values")
    return payload


def fetch_geometry_metrics(
    args: argparse.Namespace, registry: Mapping[str, object]
) -> Dict[str, Dict[str, object]]:
    manifest_path = Path(str(registry.get("step1_manifest") or ""))
    manifest = load_step1_manifest(manifest_path)
    tile_features = [
        ee.Feature(
            ee.Geometry.Rectangle(
                tile["bbox"], proj=FINE_CRS, geodesic=False
            ),
            {"tile_id": tile["tile_id"]},
        )
        for tile in manifest["tiles"]  # type: ignore[index]
    ]
    tile_collection = ee.FeatureCollection(tile_features)
    mountain_ids = sorted(
        source_records_by_mountain(registry), key=_numeric_mountain_key
    )
    numeric_ids = [int(value) for value in mountain_ids]
    mountains = ee.FeatureCollection(args.analysis_mountains_asset).filter(
        ee.Filter.inList("GMBA_V2_ID", numeric_ids)
    )
    projection = ee.Projection(FINE_CRS)

    def add_metrics(feature):
        feature = ee.Feature(feature)
        geometry = feature.geometry()
        bounds = geometry.bounds(maxError=100, proj=projection)
        gmba_area = ee.Number(feature.get("gmba_area_km2"))
        bounds_area = bounds.area(maxError=100).divide(1e6)
        return ee.Feature(
            None,
            {
                "mountain_id": ee.Number(feature.get("GMBA_V2_ID")).format(
                    "%.0f"
                ),
                "gmba_area_km2": gmba_area,
                "bounds_area_km2": bounds_area,
                "bounds_to_gmba_area_ratio": ee.Algorithms.If(
                    gmba_area.gt(0), bounds_area.divide(gmba_area), None
                ),
                "required_step1_tile_count": tile_collection.filterBounds(
                    geometry
                ).size(),
            },
        )

    payload = ee.FeatureCollection(mountains.map(add_metrics)).getInfo()
    metrics = {
        str(feature["properties"]["mountain_id"]): dict(feature["properties"])
        for feature in payload.get("features", [])
    }
    missing = sorted(set(mountain_ids) - set(metrics), key=_numeric_mountain_key)
    if missing:
        raise ValueError(f"analysis table is missing registry mountains: {missing}")
    return metrics


def _count_values(values: Iterable[object]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def run_diagnose(args: argparse.Namespace) -> Path:
    auth = initialize_with_adc(args.project)
    registry = load_source_registry(args.source_registry)
    statuses = fetch_latest_task_statuses(registry)
    assets = fetch_registry_asset_inventory(args, registry)
    metrics = fetch_geometry_metrics(args, registry)
    rows = build_diagnostic_rows(registry, statuses, assets, metrics)
    timestamp = datetime.now(timezone.utc)
    report = {
        "status": "step2b-read-only-diagnosis-complete",
        "diagnosed_at": timestamp.isoformat(),
        "exports_started": False,
        "authentication": auth,
        "project": args.project,
        "source_registry": str(args.source_registry),
        "source_registry_sha256": hashlib.sha256(
            args.source_registry.read_bytes()
        ).hexdigest(),
        "source_step2_configuration_hash": registry["configuration_hash"],
        "task_state_counts": _count_values(
            row["task_state"] for row in rows
        ),
        "failure_category_counts": failure_category_counts(rows),
        "mountain_11158": [
            row for row in rows if row["mountain_id"] == "11158"
        ],
        "tasks": rows,
    }
    report_path = args.report_json or (
        args.registry_dir
        / f"{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}-step2b-diagnosis.json"
    )
    write_new_diagnostic_report(
        report_path, report, source_registry=args.source_registry
    )
    print(
        json.dumps(
            {
                "report_json": str(report_path),
                "task_state_counts": report["task_state_counts"],
                "failure_category_counts": report["failure_category_counts"],
                "exports_started": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return report_path


def prepare_online_run(args: argparse.Namespace) -> Dict[str, object]:
    registry = load_source_registry(args.source_registry)
    statuses = fetch_latest_task_statuses(
        registry, task_ids=selection_task_ids(args, registry)
    )
    candidates = preselect_online_candidate_ids(args, registry, statuses)
    source_infos = fetch_selected_source_asset_infos(
        args, registry, candidates
    )
    selected, eligibility = resolve_online_mountain_ids(
        args,
        registry,
        statuses,
        source_infos,
    )
    if not selected:
        raise ValueError("selection resolves no eligible Step 2B mountains")
    grouped = source_records_by_mountain(registry)
    records: List[Dict[str, object]] = []
    for mountain_id in selected:
        products = grouped[mountain_id]
        source_record = products["treeline30m"]
        provenance = eligibility[mountain_id]["provenance"]
        if not isinstance(provenance, Mapping):
            raise ValueError(f"source provenance was not validated: {mountain_id}")
        direct = products.get("treeline1km")
        records.append(
            planned_step2b_record(
                mountain_id=mountain_id,
                source_record=source_record,
                provenance=provenance,
                recovery_of_task_id=(
                    str(direct.get("task_id"))
                    if direct is not None and direct.get("task_id")
                    else None
                ),
                run_label=args.run_label,
                task_prefix=args.task_prefix,
                target_collection=args.target_treeline1km_collection,
            )
        )
    direct_destinations = [
        str(grouped[mountain_id]["treeline1km"]["destination"])
        for mountain_id in selected
        if "treeline1km" in grouped[mountain_id]
    ]
    planned_destinations = [str(record["destination"]) for record in records]
    existing_assets = fetch_existing_asset_details(
        args.target_treeline1km_collection,
        planned_destinations + direct_destinations,
    )
    target_assets = {
        destination: existing_assets[destination]
        for destination in planned_destinations
        if destination in existing_assets
    }
    direct_assets = {
        destination: existing_assets[destination]
        for destination in direct_destinations
        if destination in existing_assets
    }
    apply_target_asset_guard(records, target_assets, args.resume)
    remote_tasks = ee.data.getTaskList()
    apply_active_task_guard(
        records, active_tasks_by_description(remote_tasks), args.resume
    )
    preflight_tasks = preflight_export_tasks(records)
    return {
        "registry": registry,
        "statuses": statuses,
        "source_asset_infos": source_infos,
        "selected_mountain_ids": selected,
        "eligibility": eligibility,
        "records": records,
        "target_assets": target_assets,
        "direct_assets": direct_assets,
        "remote_tasks": remote_tasks,
        "preflight_tasks": preflight_tasks,
    }


def exact_analysis_geometry(args: argparse.Namespace, mountain_id: str):
    matches = ee.FeatureCollection(args.analysis_mountains_asset).filter(
        ee.Filter.eq("GMBA_V2_ID", int(mountain_id))
    )
    return ee.Feature(matches.first()).geometry()


def run_read_only_comparisons(
    args: argparse.Namespace, context: Mapping[str, object]
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    comparisons: List[Dict[str, object]] = []
    independent_checks: List[Dict[str, object]] = []
    if not args.mountain_ids:
        return comparisons, independent_checks
    registry = context["registry"]
    grouped = source_records_by_mountain(registry)
    statuses = context["statuses"]
    direct_assets = context["direct_assets"]
    target_assets = context["target_assets"]
    records_by_mountain = {
        str(record["mountain_id"]): record for record in context["records"]
    }
    compared = 0
    for mountain_id in context["selected_mountain_ids"]:
        direct = grouped[mountain_id].get("treeline1km")
        if direct is None:
            comparisons.append(
                {
                    "status": "not_available",
                    "mountain_id": mountain_id,
                    "reason": "source_registry_has_no_direct_record",
                }
            )
            continue
        direct_status = normalized_task_status(direct, statuses)
        direct_asset_id = str(direct.get("destination") or "")
        direct_info = direct_assets.get(direct_asset_id)
        if direct_status["task_state"] != "COMPLETED":
            comparisons.append(
                {
                    "status": "not_available",
                    "mountain_id": mountain_id,
                    "reason": "direct_task_not_completed",
                    "direct_task_state": direct_status["task_state"],
                    "direct_task_id": direct_status["task_id"],
                    "direct_asset_exists": direct_info is not None,
                }
            )
            continue
        if direct_info is None:
            comparisons.append(
                {
                    "status": "not_available",
                    "mountain_id": mountain_id,
                    "reason": "direct_asset_missing",
                    "direct_task_state": direct_status["task_state"],
                    "direct_task_id": direct_status["task_id"],
                }
            )
            continue
        if compared >= 3:
            comparisons.append(
                {
                    "status": "skipped",
                    "mountain_id": mountain_id,
                    "reason": "three_comparison_mountain_limit",
                }
            )
            continue
        record = records_by_mountain[mountain_id]
        geometry = exact_analysis_geometry(args, mountain_id)
        comparison = compare_direct_and_materialized(
            record,
            direct_info,
            geometry,
            target_assets.get(str(record["destination"])),
        )
        comparison["direct_task_id"] = direct_status["task_id"]
        comparisons.append(comparison)
        independent_checks.append(
            independent_overlap_sample_check(record, geometry)
        )
        compared += 1
    return comparisons, independent_checks


def run_check(args: argparse.Namespace) -> Dict[str, object]:
    auth = initialize_with_adc(args.project)
    context = prepare_online_run(args)
    registry = context["registry"]
    records = context["records"]
    comparisons, independent_checks = run_read_only_comparisons(args, context)
    failed_independent_checks = [
        check
        for check in independent_checks
        if check.get("status") != "passed"
    ]
    if failed_independent_checks:
        failed_mountains = ", ".join(
            str(check.get("mountain_id") or "unknown")
            for check in failed_independent_checks
        )
        raise ValueError(
            "independent overlap validation failed for mountains: "
            f"{failed_mountains}"
        )
    report = {
        "status": "step2b-source-and-graph-preflight-passed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "exports_started": False,
        "authentication": auth,
        "project": args.project,
        "source_registry": str(args.source_registry),
        "source_step2_configuration_hash": registry["configuration_hash"],
        "aggregation_configuration_hash": aggregation_configuration_hash(
            str(registry["configuration_hash"])
        ),
        "selected_mountain_ids": context["selected_mountain_ids"],
        "records": records,
        "target_conflict_count": len(context["target_assets"]),
        "preflighted_task_count": len(context["preflight_tasks"]),
        "direct_v1_comparisons": comparisons,
        "independent_overlap_checks": independent_checks,
    }
    if args.report_json is not None:
        if args.report_json.resolve() == args.source_registry.resolve():
            raise ValueError("check report must not overwrite the source registry")
        write_json_atomic(args.report_json, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def submit_preflighted_tasks(
    records: Sequence[Dict[str, object]],
    tasks_by_destination: Mapping[str, object],
    registry_payload: Dict[str, object],
    registry_path: Path,
) -> None:
    registry_payload["phase"] = "SUBMITTING"
    write_json_atomic(registry_path, registry_payload)
    for record in records:
        if record["state"] != "PREFLIGHTED":
            continue
        task = tasks_by_destination[str(record["destination"])]
        try:
            task.start()
            record["task_id"] = task.id
            record["state"] = "SUBMITTED"
            write_json_atomic(registry_path, registry_payload)
        except Exception as error:
            record["state"] = "FAILED_TO_START"
            record["error"] = f"{type(error).__name__}: {error}"
            registry_payload["phase"] = "SUBMIT_FAILED_RESUMABLE"
            write_json_atomic(registry_path, registry_payload)
            raise RuntimeError(
                f"submission stopped; rerun with --resume; see {registry_path}"
            ) from error
    registry_payload["phase"] = "SUBMITTED"
    write_json_atomic(registry_path, registry_payload)


def start_exports(args: argparse.Namespace) -> Path:
    auth = initialize_with_adc(args.project)
    context = prepare_online_run(args)
    registry = context["registry"]
    records = context["records"]
    timestamp = datetime.now(timezone.utc)
    registry_path = args.registry_dir / (
        f"{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}-{args.task_prefix}.json"
    )
    aggregation_hash = aggregation_configuration_hash(
        str(registry["configuration_hash"])
    )
    payload: Dict[str, object] = {
        "created_at": timestamp.isoformat(),
        "phase": "PREFLIGHTED",
        "workflow": WORKFLOW,
        "project": args.project,
        "authentication": auth,
        "source_registry": str(args.source_registry),
        "source_registry_sha256": hashlib.sha256(
            args.source_registry.read_bytes()
        ).hexdigest(),
        "source_step2_configuration_hash": registry["configuration_hash"],
        "aggregation_configuration_hash": aggregation_hash,
        "aggregation_configuration": aggregation_configuration(
            str(registry["configuration_hash"])
        ),
        "run_label": args.run_label,
        "products": ["treeline1km"],
        "selection": {
            "failed_only": args.failed_only,
            "all_eligible": args.all_eligible,
            "mountain_ids": args.mountain_ids,
            "mountain_offset": args.mountain_offset,
            "max_mountains": args.max_mountains,
        },
        "target_treeline1km_collection": args.target_treeline1km_collection,
        "tasks": records,
    }
    write_json_atomic(registry_path, payload)
    payload["queue_projection"] = enforce_queue_limit(
        records, context["remote_tasks"], args.queue_safety_limit
    )
    write_json_atomic(registry_path, payload)
    if context["preflight_tasks"]:
        submit_preflighted_tasks(
            records,
            context["preflight_tasks"],
            payload,
            registry_path,
        )
    else:
        payload["phase"] = "RESUMED_NO_NEW_TASKS"
        write_json_atomic(registry_path, payload)
    print(
        json.dumps(
            {
                "registry": str(registry_path),
                "selected_mountain_count": len(
                    context["selected_mountain_ids"]
                ),
                "submitted_task_count": sum(
                    record["state"] == "SUBMITTED" for record in records
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return registry_path


def recover_task_ids(
    registry: Dict[str, object], tasks: Sequence[Mapping[str, object]]
) -> int:
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


def monitor_once(args: argparse.Namespace) -> Dict[str, object]:
    registry_path = args.monitor_once
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if registry.get("workflow") != WORKFLOW:
        raise ValueError("monitor registry is not a Step 2B registry")
    project = str(registry.get("project") or args.project)
    initialize_with_adc(project)
    recovered = recover_task_ids(registry, ee.data.getTaskList())
    statuses = fetch_latest_task_statuses(registry)
    details = []
    for record in registry["tasks"]:
        status = normalized_task_status(record, statuses)
        details.append(
            {
                "task_id": status["task_id"],
                "mountain_id": record.get("mountain_id"),
                "product": record.get("product"),
                "state": status["task_state"],
                "attempt_count": status["attempt_count"],
                "error_message": status["error_message"],
            }
        )
    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "exports_started": False,
        "recovered_task_ids": recovered,
        "counts": _count_values(item["state"] for item in details),
        "tasks": details,
    }
    registry["last_monitor"] = result
    write_json_atomic(registry_path, registry)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def resolved_dry_run(args: argparse.Namespace) -> Dict[str, object]:
    registry = load_source_registry(args.source_registry)
    mountain_ids = resolve_offline_mountain_ids(args, registry)
    source_hash = str(registry["configuration_hash"])
    return {
        "status": "offline-step2b-plan",
        "workflow": WORKFLOW,
        "project": args.project,
        "source_registry": str(args.source_registry),
        "source_step2_configuration_hash": source_hash,
        "aggregation_configuration_hash": aggregation_configuration_hash(source_hash),
        "mountain_ids": mountain_ids,
        "expected_task_count": len(mountain_ids),
        "eligibility_requires_online_check": True,
        "exports_started": False,
    }


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.max_mountains is not None and args.max_mountains < 1:
        parser.error("--max-mountains must be at least 1")
    if args.mountain_offset < 0:
        parser.error("--mountain-offset must be non-negative")
    if args.mountain_offset and args.max_mountains is None:
        parser.error("--mountain-offset requires --max-mountains")
    if not 1 <= args.queue_safety_limit <= 3000:
        parser.error("--queue-safety-limit must be in [1,3000]")
    if args.monitor_once is None and args.source_registry is None:
        parser.error("this mode requires --source-registry")
    if args.export and args.max_mountains is None and not args.mountain_ids:
        parser.error("--export requires --max-mountains or --mountain-ids")
    try:
        sanitize_asset_component(args.run_label)
        sanitize_asset_component(args.task_prefix)
    except ValueError as error:
        parser.error(str(error))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    if args.dry_run:
        print(json.dumps(resolved_dry_run(args), ensure_ascii=False, indent=2))
        return 0
    if RUNTIME_IMPORT_ERROR is not None:
        parser.error(f"Earth Engine runtime unavailable: {RUNTIME_IMPORT_ERROR}")
    if args.diagnose:
        run_diagnose(args)
    elif args.check:
        run_check(args)
    elif args.export:
        start_exports(args)
    else:
        monitor_once(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
