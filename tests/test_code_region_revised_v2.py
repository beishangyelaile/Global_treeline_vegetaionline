"""Offline regression tests for the sole per-GMBA Earth Engine entry point."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "gee" / "runs" / "2026821" / "code_region_revised_v2.py"

ASSET_ARGUMENTS = [
    "--project",
    "ee-wsc",
    "--prepared-mountains-asset",
    "projects/example/assets/prepared_mountains",
    "--chelsa-bio01",
    "projects/example/assets/chelsa_bio01",
    "--treeline30m-collection",
    "projects/example/assets/treeline30m",
    "--treeline1km-collection",
    "projects/example/assets/treeline1km",
    "--qa30m-collection",
    "projects/example/assets/qa30m",
]

MOUNTAINS = [
    {
        "mountain_id": "10011",
        "mountain_key": "gmba_10011",
        "region_id": "R3_EUROPEAN_ALPS",
        "region_name": "European Alps",
        "high_mountain_area_km2": 100.0,
    },
    {
        "mountain_id": "10012",
        "mountain_key": "gmba_10012",
        "region_id": "R3_EUROPEAN_ALPS",
        "region_name": "European Alps",
        "high_mountain_area_km2": 200.0,
    },
]


def load_module():
    spec = importlib.util.spec_from_file_location("treeline_gmba_v2", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parser_args(module, mode: str = "--dry-run", *extra: str):
    return module.build_parser().parse_args([mode, *ASSET_ARGUMENTS, *extra])


def run_cli(mode: str, *extra: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(SCRIPT), mode, *ASSET_ARGUMENTS, *extra],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


class V2CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_dry_run_is_offline_ready_and_exactly_scoped(self) -> None:
        result = run_cli(
            "--dry-run", "--max-mountains", "10", "--mountain-offset", "20"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["selection"]["max_mountains"], 10)
        self.assertEqual(payload["selection"]["mountain_offset"], 20)
        self.assertEqual(payload["expected_task_count"], 30)
        self.assertEqual(
            payload["products"], ["treeline30m", "treeline1km", "qa30m"]
        )

    def test_export_requires_a_bounded_batch_before_initialization(self) -> None:
        result = run_cli("--export")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--max-mountains between 1 and 100", result.stderr)

    def test_jrc_mmu_defaults_are_fixed_and_not_cli_adjustable(self) -> None:
        args = parser_args(self.module)
        self.assertEqual(self.module.JRC_MMU_HA, 0.5)
        self.assertEqual(self.module.JRC_MMU_CONNECTIVITY, 8)
        self.assertEqual(self.module.CONNECTED_COMPONENT_MAX_SIZE, 512)
        self.assertFalse(hasattr(args, "minimum_forest_patch_ha"))
        self.assertFalse(hasattr(args, "hole_max_dimension_pixels"))
        self.assertFalse(hasattr(args, "hole_border_width_m"))
        self.assertFalse(hasattr(args, "accept_hole_filling_assumption"))
        self.assertFalse(hasattr(args, "patch_count_cap"))

    def test_jrc_mmu_order_connectivity_and_area_are_fixed(self) -> None:
        component_source = inspect.getsource(self.module.small_component_mask)
        self.assertIn("ee.Kernel.square(1)", component_source)
        self.assertIn("ee.Image.pixelArea()", component_source)
        self.assertIn("reduceConnectedComponents", component_source)
        self.assertNotIn("ee.Kernel.plus(1)", component_source)
        self.assertNotIn("connectedPixelCount", component_source)

        clean_source = inspect.getsource(self.module.clean_forest)
        remove_index = clean_source.index('small_component_mask(raw, "lte")')
        retained_index = clean_source.index("retained = raw.And")
        background_index = clean_source.index("nonforest = retained.Not()")
        fill_index = clean_source.index('small_component_mask(nonforest, "lt")')
        self.assertLess(remove_index, retained_index)
        self.assertLess(retained_index, background_index)
        self.assertLess(background_index, fill_index)
        self.assertNotIn("difference(", clean_source)

    def test_jrc_mmu_threshold_boundaries_match_the_fixed_method(self) -> None:
        component_source = inspect.getsource(self.module.small_component_mask)
        self.assertIn("component_area.lte(JRC_MMU_M2)", component_source)
        self.assertIn("component_area.lt(JRC_MMU_M2)", component_source)
        clean_source = inspect.getsource(self.module.clean_forest)
        self.assertIn('small_component_mask(raw, "lte")', clean_source)
        self.assertIn('small_component_mask(nonforest, "lt")', clean_source)

    def test_removed_mmu_options_are_rejected(self) -> None:
        for option in (
            "--minimum-forest-patch-ha",
            "--hole-max-dimension-pixels",
            "--hole-border-width-m",
            "--accept-hole-filling-assumption",
            "--patch-count-cap",
        ):
            result = run_cli("--dry-run", option)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unrecognized arguments", result.stderr)

    def test_safe_defaults_remain_opt_in(self) -> None:
        args = parser_args(self.module)
        self.assertFalse(args.overwrite_assets)
        self.assertFalse(args.allow_large_batch_submit)
        self.assertFalse(args.resume)
        self.assertFalse(args.deep_check)
        self.assertFalse(args.pixel_counts)
        self.assertEqual(args.check_strategy, "median")
        self.assertEqual(args.temperature_scale, 0.1)
        self.assertEqual(args.temperature_offset, -273.15)
        self.assertEqual(args.run_label, "mountain_v4_jrc_mmu")

    def test_canopy_thresholds_are_positive_sorted_and_unique(self) -> None:
        self.assertEqual(
            self.module.normalized_canopy_thresholds([5, 3, 5.0]), [3.0, 5.0]
        )
        with self.assertRaisesRegex(ValueError, "finite positive"):
            self.module.normalized_canopy_thresholds([3, 0])

    def test_otsu_is_deterministic_and_rejects_degenerate_histograms(self) -> None:
        threshold = self.module.otsu_threshold_from_histogram(
            {"histogram": [10, 1, 1, 10], "bucketMeans": [0, 1, 2, 3]}
        )
        self.assertEqual(threshold, 1)
        with self.assertRaisesRegex(ValueError, "empty, too small, or degenerate"):
            self.module.otsu_threshold_from_histogram({}, minimum_samples=2)

    def test_each_mountain_has_three_unique_asset_destinations(self) -> None:
        args = parser_args(self.module)
        records = self.module.planned_export_records(args, MOUNTAINS)
        self.assertEqual(len(records), 6)
        self.assertEqual(len({record["destination"] for record in records}), 6)
        self.assertEqual(
            {record["product"] for record in records},
            {"treeline30m", "treeline1km", "qa30m"},
        )
        for record in records:
            collection = getattr(args, f"{record['product']}_collection")
            self.assertTrue(record["destination"].startswith(collection + "/"))

    def test_static_product_schema_matches_default_outputs(self) -> None:
        bands = self.module.expected_product_bands(parser_args(self.module))
        self.assertEqual(
            bands["treeline30m"],
            [
                "treeline_2000_h3m_m",
                "treeline_2020_h3m_m",
                "treeline_2000_h5m_m",
                "treeline_2020_h5m_m",
            ],
        )
        self.assertEqual(len(bands["treeline1km"]), 6)
        self.assertIn("analysis_domain", bands["qa30m"])
        self.assertIn("dem_elevation_m", bands["qa30m"])
        self.assertIn("dem_stk", bands["qa30m"])
        self.assertIn("otsu_valid_h3m", bands["qa30m"])
        self.assertIn("forest_small_patch_removed_2000_h3m", bands["qa30m"])
        self.assertIn("forest_small_patch_removed_2020_h5m", bands["qa30m"])
        self.assertIn("nonforest_small_gap_filled_2000_h3m", bands["qa30m"])
        self.assertIn("nonforest_small_gap_filled_2020_h5m", bands["qa30m"])

    def test_qa_pyramiding_policy_matches_band_semantics(self) -> None:
        records = self.module.planned_export_records(
            parser_args(self.module), MOUNTAINS[:1]
        )
        qa = next(record for record in records if record["product"] == "qa30m")
        self.assertEqual(qa["pyramiding_policy"][".default"], "mode")
        self.assertEqual(qa["pyramiding_policy"]["dem_elevation_m"], "mean")
        self.assertEqual(qa["pyramiding_policy"]["dem_stk"], "mean")
        self.assertEqual(
            qa["pyramiding_policy"]["t_statistic_2000_h3m"], "mean"
        )

        aspect_records = self.module.planned_export_records(
            parser_args(self.module, "--dry-run", "--aspect-mode", "polar-equator"),
            MOUNTAINS[:1],
        )
        aspect_qa = next(
            record for record in aspect_records if record["product"] == "qa30m"
        )
        self.assertEqual(aspect_qa["pyramiding_policy"]["aspect_deg"], "sample")
        self.assertEqual(aspect_qa["pyramiding_policy"]["slope_deg"], "mean")

    def test_configuration_hash_tracks_science_not_batch_selection(self) -> None:
        first = parser_args(self.module, "--dry-run", "--max-mountains", "10")
        second = parser_args(
            self.module,
            "--dry-run",
            "--max-mountains",
            "30",
            "--mountain-offset",
            "10",
        )
        changed = parser_args(self.module, "--dry-run", "--edge-method", "canny")
        self.assertEqual(
            self.module.configuration_hash(first),
            self.module.configuration_hash(second),
        )
        self.assertNotEqual(
            self.module.configuration_hash(first),
            self.module.configuration_hash(changed),
        )

    def test_configuration_identifies_v4_jrc_mmu_contract(self) -> None:
        configuration = self.module.scientific_configuration(parser_args(self.module))
        self.assertEqual(configuration["workflow"], "per-gmba-v4-jrc-mmu")
        self.assertEqual(configuration["mmu_area_ha"], 0.5)
        self.assertEqual(configuration["mmu_connectivity"], 8)
        self.assertEqual(
            configuration["mmu_area_measure"],
            "sum_pixelArea_m2_per_connected_component",
        )
        self.assertEqual(
            configuration["mmu_operation_order"],
            "remove_small_forest_then_fill_small_nonforest_gaps",
        )
        self.assertEqual(configuration["connected_component_max_size_pixels"], 512)
        self.assertEqual(
            configuration["connected_component_max_size_role"],
            "compute_protection_only",
        )
        self.assertEqual(configuration["jrc_alignment"], "binary_mmu_postprocessing_only")
        self.assertNotIn("minimum_forest_patch_ha", configuration)
        self.assertNotIn("hole_max_dimension_pixels", configuration)

    def test_active_task_conflict_requires_resume(self) -> None:
        args = parser_args(self.module)
        records = self.module.planned_export_records(args, MOUNTAINS[:1])
        active = {
            records[0]["description"]: {
                "id": "task-id",
                "state": "READY",
                "description": records[0]["description"],
            }
        }
        empty_targets = {
            product: {"children": {}}
            for product in ("treeline30m", "treeline1km", "qa30m")
        }
        with self.assertRaisesRegex(ValueError, "READY/RUNNING"):
            self.module.apply_resume_guards(records, empty_targets, active, args)

    def test_resume_only_skips_assets_with_matching_configuration(self) -> None:
        args = parser_args(self.module)
        args.resume = True
        records = self.module.planned_export_records(args, MOUNTAINS[:1])
        target = records[0]
        targets = {
            "treeline30m": {
                "children": {
                    target["destination"]: {
                        "properties": {
                            "configuration_hash": target["configuration_hash"]
                        }
                    }
                }
            },
            "treeline1km": {"children": {}},
            "qa30m": {"children": {}},
        }
        self.module.apply_resume_guards(records, targets, {}, args)
        self.assertEqual(records[0]["state"], "SKIPPED_EXISTING")

    def test_queue_limit_counts_only_tasks_that_would_be_submitted(self) -> None:
        args = parser_args(self.module)
        args.queue_safety_limit = 4
        records = [
            {"state": "PLANNED"},
            {"state": "PREFLIGHTED"},
            {"state": "SKIPPED_EXISTING"},
        ]
        result = self.module.enforce_ready_queue_limit(
            records, [{"state": "READY"}, {"state": "RUNNING"}], args
        )
        self.assertEqual(result["projected_ready"], 3)

    def test_registry_write_is_atomic_and_task_ids_can_be_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            payload = {"tasks": [{"description": "task-a", "task_id": None}]}
            self.module.write_json_atomic(path, payload)
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            loaded = json.loads(path.read_text(encoding="utf-8"))
            recovered = self.module.recover_task_ids(
                loaded,
                [{"description": "task-a", "id": "id-a", "state": "RUNNING"}],
            )
            self.assertEqual(recovered, 1)
            self.assertEqual(loaded["tasks"][0]["task_id"], "id-a")

    def test_asset_exporter_has_no_drive_path_and_starts_before_recording_id(self) -> None:
        exporter = inspect.getsource(self.module.make_asset_export_task)
        start_exports = inspect.getsource(self.module.start_exports)
        self.assertIn("Export.image.toAsset", exporter)
        self.assertNotIn("toDrive", exporter)
        self.assertLess(
            start_exports.index("task.start()"),
            start_exports.index('record["task_id"] = task.id'),
        )
        self.assertLess(
            start_exports.index("# Preflight every graph"),
            start_exports.index("task.start()"),
        )


if __name__ == "__main__":
    unittest.main()
