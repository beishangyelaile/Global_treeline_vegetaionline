"""Offline regression tests for the revised GMBA workflow."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "gee" / "runs" / "2026821" / "code_region_revised.py"
SHARD = {
    "region_id": "R3_EUROPEAN_ALPS",
    "tile_x": 5,
    "tile_y": 23,
    "shard_id": "R3_EUROPEAN_ALPS_xp005_yp023",
    "quarter_cell_count": 48,
    "bounds": [10.0, 46.0, 12.0, 48.0],
}


def load_module():
    spec = importlib.util.spec_from_file_location("treeline_2026821_region_revised", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parser_args(module, *extra: str):
    return module.build_parser().parse_args(
        [
            "--dry-run",
            "--project", "ee-wsc",
            "--gmba-asset", "projects/example/assets/gmba",
            "--manifest-asset", "projects/example/assets/manifest",
            "--chelsa-bio01", "projects/example/assets/bio01",
            "--treeline30m-collection", "projects/output/assets/treeline30m",
            "--treeline1km-collection", "projects/output/assets/treeline1km",
            "--qa30m-collection", "projects/output/assets/qa30m",
            *extra,
        ]
    )


class RevisedRegionTests(unittest.TestCase):
    def test_task_ids_are_recorded_after_start_and_can_be_recovered(self) -> None:
        module = load_module()
        source = inspect.getsource(module.start_exports)
        start_index = source.index("task.start()")
        id_index = source.index('record["task_id"] = task.id', start_index)
        self.assertLess(start_index, id_index)

        registry = {"tasks": [{"description": "task-a", "task_id": None}]}
        recovered = module.recover_task_ids(
            registry,
            [{"description": "task-a", "id": "id-a", "state": "RUNNING"}],
        )
        self.assertEqual(recovered, 1)
        self.assertEqual(registry["tasks"][0]["task_id"], "id-a")

    def test_mountain_limit_is_applied_before_shard_planning(self) -> None:
        module = load_module()
        args = parser_args(
            module, "--mountain-offset", "20", "--max-mountains", "30"
        )
        plan = module.resolved_plan(args)
        self.assertEqual(plan["max_mountains"], 30)
        self.assertEqual(plan["mountain_offset"], 20)
        self.assertEqual(plan["expected_shard_count"], "resolved online")
        self.assertEqual(plan["expected_export_task_count"], "resolved online")

        selection_source = inspect.getsource(module.select_analysis_mountains)
        shard_source = inspect.getsource(module.resolve_shard_plan)
        self.assertIn('ordered = scoped.sort("GMBA_V2_ID")', selection_source)
        self.assertIn("ordered.toList(args.max_mountains, args.mountain_offset)", selection_source)
        self.assertIn("args.max_mountains is None", shard_source)

    def test_export_task_limit_is_deterministic_and_exact(self) -> None:
        module = load_module()
        args = parser_args(module, "--max-export-tasks", "2")
        records = module.planned_export_records(args, [SHARD])
        self.assertEqual(len(records), 2)
        self.assertEqual(
            [record["product"] for record in records],
            ["treeline30m", "treeline1km"],
        )
        self.assertEqual(module.resolved_plan(args)["expected_export_task_count"], 2)

    def test_default_preflight_is_shallow_and_pixel_counts_are_optional(self) -> None:
        module = load_module()
        defaults = parser_args(module)
        self.assertFalse(defaults.deep_check)
        self.assertTrue(defaults.pixel_counts)
        self.assertFalse(parser_args(module, "--no-pixel-counts").pixel_counts)

        source = inspect.getsource(module.run_formal_shard_preflight)
        self.assertIn('"analysis_domain_count_1km": None', source)
        self.assertIn('"skipped_in_default_preflight"', source)
        self.assertLess(source.index("if args.deep_check"), source.index(".reduceRegion("))
        self.assertIn('if args.otsu_scope == "shard-dynamic"', source)
        self.assertIn('"status": "deferred_to_export_task"', source)
        self.assertIn("product_bands = expected_product_bands(args)", source)

    def test_static_product_schema_matches_default_outputs(self) -> None:
        module = load_module()
        bands = module.expected_product_bands(parser_args(module))
        self.assertEqual(
            bands["treeline30m"],
            ["treeline_all_2000_m", "treeline_all_2020_m"],
        )
        self.assertIn("dem_stk", bands["qa30m"])

    def test_qa_pyramiding_policy_matches_band_semantics(self) -> None:
        module = load_module()
        default_records = module.planned_export_records(parser_args(module), [SHARD])
        default_qa = next(record for record in default_records if record["product"] == "qa30m")
        self.assertEqual(default_qa["pyramiding_policy"], {
            ".default": "mode",
            "dem_stk": "mean",
        })

        aspect_records = module.planned_export_records(
            parser_args(module, "--aspect-mode", "polar-equator"), [SHARD]
        )
        aspect_qa = next(record for record in aspect_records if record["product"] == "qa30m")
        self.assertEqual(aspect_qa["pyramiding_policy"]["aspect_deg"], "sample")
        self.assertEqual(aspect_qa["pyramiding_policy"]["slope_deg"], "mean")
        self.assertEqual(aspect_qa["pyramiding_policy"]["dem_stk"], "mean")

        exporter_source = inspect.getsource(module.make_asset_export_task)
        self.assertIn('pyramidingPolicy=dict(record["pyramiding_policy"])', exporter_source)

    def test_expensive_intermediates_use_buffered_gmba_support(self) -> None:
        module = load_module()
        support_source = inspect.getsource(module.build_processing_support)
        common_source = inspect.getsource(module.build_common)
        bundle_source = inspect.getsource(module.build_shard_export_bundle)

        self.assertIn("selected_gmba.geometry", support_source)
        self.assertIn(".buffer(", support_source)
        self.assertIn(".intersection(processing_rectangle", support_source)
        self.assertIn("quarter_degree_screen(processing_rectangle", common_source)
        self.assertIn("build_aw3d(processing_support", common_source)
        self.assertIn("clean_forest(FOREST_HEIGHT_2020, processing_support", common_source)
        self.assertIn("build_products(\n        args, common, processing_support", bundle_source)
        self.assertIn(".clip(batch_region)", bundle_source)


if __name__ == "__main__":
    unittest.main()
