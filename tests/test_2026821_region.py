"""Offline tests for the GMBA quarter-grid Asset export workflow."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "gee" / "runs" / "2026821" / "code_region.py"
SHARDS = [
    {
        "region_id": "R3_EUROPEAN_ALPS",
        "tile_x": 5,
        "tile_y": 23,
        "shard_id": "R3_EUROPEAN_ALPS_xp005_yp023",
        "quarter_cell_count": 48,
        "bounds": [10.0, 46.0, 12.0, 48.0],
    },
    {
        "region_id": "R7_ANDES",
        "tile_x": -36,
        "tile_y": -17,
        "shard_id": "R7_ANDES_xm036_ym017",
        "quarter_cell_count": 12,
        "bounds": [-72.0, -34.0, -70.0, -32.0],
    },
]


def load_module():
    spec = importlib.util.spec_from_file_location("treeline_2026821_region", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parser_args(module, mode: str = "--dry-run"):
    return module.build_parser().parse_args(
        [
            mode,
            "--project",
            "ee-wsc",
            "--gmba-asset",
            "projects/example/assets/gmba",
            "--manifest-asset",
            "projects/example/assets/manifest",
            "--chelsa-bio01",
            "projects/example/assets/bio01",
            "--treeline30m-collection",
            "projects/output/assets/Treeline_30m_Collection",
            "--treeline1km-collection",
            "projects/output/assets/Treeline_1km_Collection",
            "--qa30m-collection",
            "projects/output/assets/Treeline_QA30m_Collection",
        ]
    )


class RegionAssetExportTests(unittest.TestCase):
    def test_two_shards_create_six_unique_asset_destinations(self) -> None:
        module = load_module()
        records = module.planned_export_records(parser_args(module), SHARDS)
        self.assertEqual(len(records), 6)
        self.assertEqual(len({record["destination"] for record in records}), 6)

    def test_products_are_routed_to_requested_collections(self) -> None:
        module = load_module()
        args = parser_args(module)
        records = module.planned_export_records(args, SHARDS[:1])
        destinations = {record["product"]: record["destination"] for record in records}
        self.assertTrue(destinations["treeline30m"].startswith(args.treeline30m_collection + "/"))
        self.assertTrue(destinations["treeline1km"].startswith(args.treeline1km_collection + "/"))
        self.assertTrue(destinations["qa30m"].startswith(args.qa30m_collection + "/"))

    def test_asset_exporter_has_no_drive_export_path(self) -> None:
        module = load_module()
        task_source = inspect.getsource(module.make_asset_export_task)
        bundle_source = inspect.getsource(module.build_shard_export_bundle)
        self.assertIn("Export.image.toAsset", task_source)
        self.assertNotIn("toDrive", task_source + bundle_source)
        self.assertIn("otsu_threshold_ee", bundle_source)
        self.assertIn("updateMask(otsu_valid_mask)", bundle_source)
        self.assertIn("toInt16()", bundle_source)

    def test_overwrite_is_opt_in(self) -> None:
        module = load_module()
        self.assertFalse(parser_args(module).overwrite_assets)

    def test_missing_child_asset_is_not_treated_as_an_error(self) -> None:
        module = load_module()
        with mock.patch.object(
            module.ee.data,
            "getAsset",
            side_effect=module.ee.EEException("Asset does not exist or doesn't allow this operation"),
        ):
            self.assertFalse(module.asset_exists("projects/output/assets/collection/child"))

    def test_export_does_not_require_fixed_celsius_threshold_or_sayre(self) -> None:
        module = load_module()
        args = parser_args(module, "--export")
        self.assertEqual(module.missing_requirements(args, export=True), [])
        self.assertNotIn("sayre_asset", vars(args))
        source = inspect.getsource(module.build_shard_export_bundle)
        self.assertNotIn("sayre_asset", source)

    def test_shard_bounds_are_global_two_degree_cells(self) -> None:
        module = load_module()
        self.assertEqual(module.shard_bounds(5, 23, 2.0), (10.0, 46.0, 12.0, 48.0))
        self.assertEqual(module.shard_bounds(-36, -17, 2.0), (-72.0, -34.0, -70.0, -32.0))

    def test_negative_grid_indices_have_stable_unambiguous_asset_ids(self) -> None:
        module = load_module()
        asset_id = module.child_asset_id(
            "projects/output/assets/collection", "R7_ANDES", -36, -17, "none"
        )
        self.assertTrue(asset_id.endswith("/R7_ANDES_xm036_ym017_none"))

    def test_default_plan_reports_427_shards_and_1281_tasks(self) -> None:
        module = load_module()
        plan = module.resolved_plan(parser_args(module))
        self.assertEqual(plan["expected_shard_count"], 427)
        self.assertEqual(plan["expected_export_task_count"], 1281)

    def test_export_preflights_all_configs_before_submit_phase(self) -> None:
        module = load_module()
        source = inspect.getsource(module.start_exports)
        preflight = source.index("# Phase 1")
        submit = source.index("# Phase 2")
        first_start = source.index("task.start()")
        self.assertLess(preflight, submit)
        self.assertLess(submit, first_start)
        self.assertIn('record["state"] = "PREFLIGHTED"', source)

    def test_active_task_conflict_requires_resume(self) -> None:
        module = load_module()
        records = [{"destination": "asset/a", "description": "task_a", "state": "PLANNED"}]
        active = {"task_a": {"id": "id-a", "state": "READY"}}
        with self.assertRaisesRegex(ValueError, "READY/RUNNING"):
            module.apply_resume_guards(records, [], active, False, False)

    def test_resume_skips_existing_and_active_work(self) -> None:
        module = load_module()
        records = [
            {"destination": "asset/a", "description": "task_a", "state": "PLANNED"},
            {"destination": "asset/b", "description": "task_b", "state": "PLANNED"},
        ]
        module.apply_resume_guards(
            records, ["asset/a"], {"task_b": {"id": "id-b", "state": "RUNNING"}}, True, False
        )
        self.assertEqual(records[0]["state"], "SKIPPED_EXISTING")
        self.assertEqual(records[1]["state"], "SKIPPED_ACTIVE")
        self.assertEqual(records[1]["task_id"], "id-b")

    def test_qa_is_masked_to_analysis_domain_and_buffer_covers_hole_scale(self) -> None:
        module = load_module()
        source = inspect.getsource(module.build_products)
        self.assertGreaterEqual(source.count("toFloat().updateMask(domain)"), 2)
        args = parser_args(module)
        self.assertGreaterEqual(
            args.context_buffer_m,
            args.hole_max_size_pixels * 30 + args.window_radius_m,
        )


if __name__ == "__main__":
    unittest.main()
