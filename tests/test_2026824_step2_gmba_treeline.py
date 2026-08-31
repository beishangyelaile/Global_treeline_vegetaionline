"""Offline contracts for the Step 2 per-mountain treeline workflow."""

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
SCRIPT = ROOT / "gee" / "runs" / "2026824" / "code_step2_gmba_treeline.py"


def load_module():
    spec = importlib.util.spec_from_file_location("step2_gmba_treeline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


class Step2TreelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_source_has_no_raw_canopy_or_mmu_implementation(self) -> None:
        self.assertNotIn("projects/glad/", self.source)
        self.assertNotIn("connectedPixelCount", self.source)
        self.assertNotIn("MMU_AREA_M2", self.source)

    def test_step1_collections_are_the_only_forest_inputs(self) -> None:
        self.assertEqual(
            self.module.GLOBAL_TREE_3M,
            "projects/ee-alpine-506212/assets/Global_tree_3m",
        )
        self.assertEqual(
            self.module.GLOBAL_TREE_5M,
            "projects/ee-alpine-506212/assets/Global_tree_5m",
        )
        source = inspect.getsource(self.module.build_global_forest_inputs)
        self.assertEqual(source.count(".mosaic()"), 2)
        self.assertIn('select(["tree_2000", "tree_2020"])', source)
        self.assertEqual(source.count(".setDefaultProjection(projection)"), 2)
        self.assertNotIn(".reproject(", source)

    def test_forest_mosaics_set_the_fine_default_projection_before_edges(self) -> None:
        events = []

        class FakeImage:
            def __init__(self, asset_id: str) -> None:
                self.asset_id = asset_id

            def mosaic(self):
                events.append((self.asset_id, "mosaic"))
                return self

            def select(self, bands):
                events.append((self.asset_id, "select", tuple(bands)))
                return self

            def setDefaultProjection(self, projection):
                events.append((self.asset_id, "setDefaultProjection", projection))
                return self

        class FakeEE:
            @staticmethod
            def Projection(crs, transform=None):
                projection = (crs, tuple(transform))
                events.append(("Projection", projection))
                return projection

            @staticmethod
            def ImageCollection(asset_id):
                events.append((asset_id, "ImageCollection"))
                return FakeImage(asset_id)

        original_ee = self.module.ee
        self.module.ee = FakeEE
        try:
            args = self.module.build_parser().parse_args(["--dry-run"])
            forests = self.module.build_global_forest_inputs(args)
        finally:
            self.module.ee = original_ee

        projection = (self.module.FINE_CRS, tuple(self.module.FINE_TRANSFORM))
        self.assertEqual(
            events,
            [
                ("Projection", projection),
                (self.module.GLOBAL_TREE_3M, "ImageCollection"),
                (self.module.GLOBAL_TREE_3M, "mosaic"),
                (
                    self.module.GLOBAL_TREE_3M,
                    "select",
                    ("tree_2000", "tree_2020"),
                ),
                (self.module.GLOBAL_TREE_3M, "setDefaultProjection", projection),
                (self.module.GLOBAL_TREE_5M, "ImageCollection"),
                (self.module.GLOBAL_TREE_5M, "mosaic"),
                (
                    self.module.GLOBAL_TREE_5M,
                    "select",
                    ("tree_2000", "tree_2020"),
                ),
                (self.module.GLOBAL_TREE_5M, "setDefaultProjection", projection),
            ],
        )
        self.assertEqual(forests["h3m"].asset_id, self.module.GLOBAL_TREE_3M)
        self.assertEqual(forests["h5m"].asset_id, self.module.GLOBAL_TREE_5M)

    def test_projection_change_has_new_method_identity_and_provenance(self) -> None:
        args = self.module.build_parser().parse_args(["--dry-run"])
        configuration = self.module.scientific_configuration(args)
        self.assertEqual(
            self.module.WORKFLOW,
            "step2-per-gmba-sayre-treeline-v2",
        )
        self.assertEqual(args.run_label, "gmba_sayre_step2_v2")
        self.assertEqual(
            configuration["forest_mosaic_default_projection"],
            {
                "crs": self.module.FINE_CRS,
                "transform": self.module.FINE_TRANSFORM,
                "placement": "after_mosaic_select_before_pixel_neighborhood",
            },
        )
        self.assertIn("set_fine_default_projection", configuration["edge_order"])

    def test_analysis_asset_and_selection_thresholds_are_fixed(self) -> None:
        self.assertEqual(
            self.module.ANALYSIS_MOUNTAINS_ASSET,
            "projects/ee-wsc/assets/Alpine/GMBA_Sayre",
        )
        self.assertEqual(self.module.MIN_HM_FRACTION, 0.50)
        self.assertEqual(self.module.MAX_TREE_FRACTION, 0.90)
        self.assertEqual(self.module.WORLDCOVER_2021, "ESA/WorldCover/v200")
        args = self.module.build_parser().parse_args(["--dry-run"])
        self.assertEqual(args.analysis_mountains_asset, self.module.ANALYSIS_MOUNTAINS_ASSET)

        selection_source = inspect.getsource(self.module.analysis_mountains)
        self.assertIn('ee.Filter.gte("hm_fraction", MIN_HM_FRACTION)', selection_source)
        self.assertIn('ee.Filter.lte("tree_fraction", MAX_TREE_FRACTION)', selection_source)
        self.assertIn("add_analysis_keys", selection_source)

    def test_analysis_keys_are_derived_from_gmba_v2_id(self) -> None:
        source = inspect.getsource(self.module.add_analysis_keys)
        self.assertIn('feature.get("GMBA_V2_ID")', source)
        self.assertIn('"gmba_id_text"', source)
        self.assertIn('"gmba_sort_key"', source)

    def test_edges_are_built_before_the_analysis_domain_mask(self) -> None:
        edge_source = inspect.getsource(self.module.forest_edges_global)
        self.assertIn("focalMedian", edge_source)
        self.assertIn("zeroCrossing", edge_source)
        self.assertNotIn("domain", edge_source)
        bundle_source = inspect.getsource(self.module.build_mountain_bundle)
        self.assertLess(
            bundle_source.index("forest_edges_global"),
            bundle_source.index("updateMask(domain)"),
        )

    def test_analysis_geometry_is_direct_and_has_no_context_buffer(self) -> None:
        source = inspect.getsource(self.module.build_mountain_context)
        self.assertIn("analysis_mountains(args)", source)
        self.assertIn('ee.Filter.eq("gmba_id_text"', source)
        self.assertNotIn("buffer(", source)

    def test_otsu_contract_is_pooled_and_has_no_global_fallback(self) -> None:
        configuration = self.module.scientific_configuration(
            self.module.build_parser().parse_args(["--dry-run"])
        )
        self.assertEqual(configuration["otsu_scope"], "per_gmba_threshold")
        self.assertEqual(configuration["otsu_year_pooling"], "2000_2020")
        self.assertTrue(configuration["otsu_native_cells_counted_once"])
        self.assertTrue(configuration["otsu_same_threshold_both_years"])
        self.assertEqual(configuration["otsu_invalid_policy"], "flag_no_fallback")
        self.assertEqual(
            configuration["analysis_domain"],
            "complete_filtered_GMBA_v2_Standard_Basic_geometry",
        )
        self.assertEqual(configuration["minimum_high_mountain_fraction"], 0.50)
        self.assertEqual(configuration["maximum_tree_cover_fraction"], 0.90)

    def test_step1_inventory_rejects_missing_duplicates_and_mismatch(self) -> None:
        valid = {
            "h3m": [self.module.inventory_fixture("N00_E000", 3)],
            "h5m": [self.module.inventory_fixture("N00_E000", 5)],
        }
        self.assertEqual(valid["h3m"][0]["properties"]["mmu_max_size"], 500)
        passed = self.module.validate_step1_inventory(["N00_E000"], valid)
        self.assertTrue(passed["ready"])

        missing = self.module.validate_step1_inventory(
            ["N00_E000", "N00_E010"], valid
        )
        self.assertFalse(missing["ready"])
        self.assertTrue(any("missing" in error for error in missing["errors"]))

        duplicate = {"h3m": valid["h3m"] * 2, "h5m": valid["h5m"]}
        failed = self.module.validate_step1_inventory(["N00_E000"], duplicate)
        self.assertFalse(failed["ready"])
        self.assertTrue(any("duplicate" in error for error in failed["errors"]))

        old_max_size = {
            "h3m": [
                {
                    **valid["h3m"][0],
                    "properties": {
                        **valid["h3m"][0]["properties"],
                        "mmu_max_size": 50,
                    },
                }
            ],
            "h5m": valid["h5m"],
        }
        failed = self.module.validate_step1_inventory(["N00_E000"], old_max_size)
        self.assertFalse(failed["ready"])
        self.assertTrue(any("invalid max size" in error for error in failed["errors"]))

    def test_expected_outputs_include_traceable_qa(self) -> None:
        bands = self.module.expected_product_bands()
        self.assertEqual(
            bands["treeline30m"],
            [
                "treeline_2000_h3m_m",
                "treeline_2020_h3m_m",
                "treeline_2000_h5m_m",
                "treeline_2020_h5m_m",
            ],
        )
        self.assertEqual(
            bands["qa30m"][:9],
            [
                "analysis_domain",
                "sayre_high",
                "gmba_mask",
                "hm_fraction",
                "tree_fraction",
                "non_valley",
                "dem_elevation_m",
                "dem_msk",
                "dem_stk",
            ],
        )
        for name in (
            "analysis_domain",
            "hm_fraction",
            "tree_fraction",
            "candidate_edge_2000_h3m",
            "candidate_edge_2020_h5m",
            "otsu_valid_h3m",
            "otsu_valid_h5m",
            "forest_sample_count_2000_h3m",
            "nonforest_sample_count_2020_h5m",
        ):
            self.assertIn(name, bands["qa30m"])

    def test_pyramiding_distinguishes_categorical_and_continuous_bands(self) -> None:
        policies = self.module.product_pyramiding_policies()
        self.assertEqual(policies["qa30m"][".default"], "mode")
        self.assertEqual(policies["qa30m"]["dem_elevation_m"], "mean")
        self.assertEqual(policies["qa30m"]["hm_fraction"], "mean")
        self.assertEqual(policies["qa30m"]["tree_fraction"], "mean")
        self.assertEqual(
            policies["qa30m"]["forest_sample_count_2000_h3m"], "mode"
        )
        self.assertEqual(policies["treeline30m"][".default"], "mean")

    def test_check_uses_compact_expression_serialization_without_starting_task(self) -> None:
        class Expression:
            def __init__(self) -> None:
                self.calls = []

            def serialize(self, *, pretty, for_cloud_api):
                self.calls.append((pretty, for_cloud_api))
                return '{"type":"Invocation"}'

            def __str__(self):
                raise AssertionError("check must not expand the expression with str()")

        class Task:
            def __init__(self, expression) -> None:
                self.config = {
                    "expression": expression,
                    "assetExportOptions": {"earthEngineDestination": {}},
                }

            def start(self):
                raise AssertionError("check must not start an export task")

        expression = Expression()
        size = self.module.serialized_export_expression_bytes(
            Task(expression), "treeline30m"
        )
        self.assertEqual(size, len('{"type":"Invocation"}'.encode("utf-8")))
        self.assertEqual(expression.calls, [(False, True)])
        check_source = inspect.getsource(self.module.run_check)
        self.assertIn("serialized_export_expression_bytes", check_source)
        self.assertNotIn(".start()", check_source)

    def test_dry_run_uses_default_analysis_asset_without_network(self) -> None:
        result = run_cli("--dry-run", "--max-mountains", "4")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ready"])
        self.assertEqual(
            payload["analysis_mountains_asset"], self.module.ANALYSIS_MOUNTAINS_ASSET
        )
        self.assertNotIn("analysis_mountains_asset", payload["missing_requirements"])
        self.assertIn("step1_manifest", payload["missing_requirements"])
        self.assertEqual(payload["products"], ["treeline30m", "qa30m"])
        self.assertEqual(payload["expected_task_count"], 8)
        self.assertFalse(payload["legacy_direct_1km"])

    def test_direct_1km_is_only_planned_when_explicitly_selected(self) -> None:
        result = run_cli(
            "--dry-run",
            "--max-mountains",
            "2",
            "--export-products",
            "treeline1km",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["products"], ["treeline1km"])
        self.assertEqual(payload["expected_task_count"], 2)
        self.assertTrue(payload["legacy_direct_1km"])

    def test_legacy_direct_1km_cannot_share_a_batch_with_step2a_products(self) -> None:
        result = run_cli(
            "--dry-run",
            "--max-mountains",
            "1",
            "--export-products",
            "treeline30m",
            "treeline1km",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "legacy treeline1km must be selected alone",
            result.stderr,
        )

    def test_dry_run_becomes_ready_with_explicit_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "step1.json"
            manifest.write_text(
                json.dumps({"configuration_hash": "abc", "tiles": []}),
                encoding="utf-8",
            )
            result = run_cli(
                "--dry-run",
                "--analysis-mountains-asset",
                "projects/example/assets/filtered_gmba_basic",
                "--step1-manifest",
                str(manifest),
                "--max-mountains",
                "4",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ready"])

    def test_dry_run_can_select_two_export_products(self) -> None:
        result = run_cli(
            "--dry-run",
            "--max-mountains",
            "200",
            "--export-products",
            "treeline30m",
            "qa30m",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["products"], ["treeline30m", "qa30m"])
        self.assertEqual(payload["expected_task_count"], 400)
        self.assertEqual(
            payload["scientific_configuration"]["export_products"],
            ["treeline30m", "qa30m"],
        )

    def test_planned_records_exclude_unselected_product(self) -> None:
        args = self.module.build_parser().parse_args(
            [
                "--dry-run",
                "--export-products",
                "treeline30m",
                "qa30m",
            ]
        )
        records = self.module.planned_export_records(
            args,
            [{"mountain_id": "11106", "mountain_key": "gmba_11106"}],
        )
        self.assertEqual(
            [record["product"] for record in records],
            ["treeline30m", "qa30m"],
        )
        self.assertNotIn(
            "Treeline_1km_Collection",
            " ".join(str(record["destination"]) for record in records),
        )

    def test_export_requires_a_bounded_batch_and_step1_manifest(self) -> None:
        result = run_cli("--export")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--step1-manifest", result.stderr)
        self.assertIn("--max-mountains", result.stderr)


if __name__ == "__main__":
    unittest.main()
