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


def validated_upstream_fixture(module, directory: str):
    root = Path(directory)
    manifest = root / "step1.json"
    receipt = root / "validated-upstream.json"
    manifest.write_text(
        json.dumps(
            {
                "project": "ee-wsc",
                "gmba_asset": module.ANALYSIS_MOUNTAINS_ASSET,
                "configuration_hash": "test-step1-hash",
                "tiles": [
                    {
                        "tile_id": "N00_E000",
                        "bbox": [0, 0, 10, 10],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    args = module.build_parser().parse_args(
        [
            "--dry-run",
            "--project",
            "ee-wsc",
            "--step1-manifest",
            str(manifest),
            "--validated-upstream-receipt",
            str(receipt),
        ]
    )
    identity = module.validated_upstream_identity(args)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": module.VALIDATED_UPSTREAM_SCHEMA_VERSION,
                "validation_id": "test-validated-upstream",
                "validated_at_utc": "2026-08-31T08:25:54+00:00",
                "upstream_identity": identity,
                "analysis_table": {
                    "id": module.ANALYSIS_MOUNTAINS_ASSET,
                    "type": "TABLE",
                    "source_feature_count": 2,
                    "complete_property_count": 2,
                    "selected_feature_count": 2,
                    "distinct_gmba_id_count": 2,
                    "mapunit_histogram": {"Basic": 2},
                    "below_minimum_hm_fraction_count": 0,
                    "above_maximum_tree_fraction_count": 0,
                },
                "step1_integrity": {
                    "ready": True,
                    "errors": [],
                    "expected_tile_count": 1,
                    "h3m_tile_count": 1,
                    "h5m_tile_count": 1,
                    "configuration_hashes": ["test-step1-hash"],
                    "all_analysis_mountain_count": 2,
                    "all_analysis_required_tile_count": 1,
                    "all_analysis_required_tile_ids_sha256": "test-tiles-hash",
                    "missing_required_tile_ids": [],
                },
                "deep_check": {
                    "status": "evaluated",
                    "execution_feasibility_verified": True,
                    "scope": "representative_mountain",
                    "mountain_id": "10067",
                    "thresholds": {
                        "h3m": {"valid": 1},
                        "h5m": {"valid": 1},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return args, manifest, receipt


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

    def test_v2_output_collections_are_the_defaults(self) -> None:
        args = self.module.build_parser().parse_args(["--dry-run"])
        self.assertEqual(
            args.treeline30m_collection,
            "projects/ee-alpine-506212/assets/Treeline_30m_Collection_v2",
        )
        self.assertEqual(
            args.treeline1km_collection,
            "projects/ee-alpine-506212/assets/Treeline_1km_Collection_v2",
        )
        self.assertEqual(
            args.qa30m_collection,
            "projects/ee-alpine-506212/assets/Treeline_QA30m_Collection_v2",
        )

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

    def test_legacy_1km_aggregation_matches_step2b_limits(self) -> None:
        events = []

        class FakeImage:
            def reduceResolution(self, **kwargs):
                events.append(("reduceResolution", kwargs))
                return self

            def reproject(self, projection):
                events.append(("reproject", projection))
                return self

        class FakeReducer:
            @staticmethod
            def mean():
                return "mean"

        class FakeEE:
            Reducer = FakeReducer

            @staticmethod
            def Projection(crs, transform):
                return (crs, tuple(transform))

        original_ee = self.module.ee
        self.module.ee = FakeEE
        try:
            output = self.module.aggregate_to_climate_grid(FakeImage())
        finally:
            self.module.ee = original_ee

        self.assertIsInstance(output, FakeImage)
        self.assertEqual(
            events,
            [
                (
                    "reduceResolution",
                    {"reducer": "mean", "bestEffort": False, "maxPixels": 2048},
                ),
                (
                    "reproject",
                    (
                        self.module.CLIMATE_CRS,
                        tuple(self.module.CLIMATE_TRANSFORM),
                    ),
                ),
            ],
        )

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

    def test_repository_validated_upstream_receipt_matches_science_source(self) -> None:
        receipt_path = self.module.VALIDATED_UPSTREAM_RECEIPT
        self.assertTrue(receipt_path.is_file())
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["schema_version"],
            self.module.VALIDATED_UPSTREAM_SCHEMA_VERSION,
        )
        self.assertEqual(
            payload["upstream_identity"]["science_source_sha256"],
            self.module.validated_science_source_sha256(),
        )
        self.assertEqual(payload["analysis_table"]["selected_feature_count"], 3115)
        self.assertEqual(
            payload["step1_integrity"]["all_analysis_mountain_count"],
            3115,
        )
        self.assertEqual(payload["step1_integrity"]["h3m_tile_count"], 304)
        self.assertEqual(payload["step1_integrity"]["h5m_tile_count"], 304)
        self.assertEqual(
            payload["step1_integrity"]["all_analysis_required_tile_count"],
            182,
        )
        self.assertTrue(
            payload["deep_check"]["execution_feasibility_verified"]
        )

    def test_trusted_upstream_fast_path_is_local_and_auditable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args, _, _ = validated_upstream_fixture(self.module, directory)
            originals = {
                "getAsset": self.module.ee.data.getAsset,
                "listAssets": self.module.ee.data.listAssets,
                "FeatureCollection": self.module.ee.FeatureCollection,
            }

            def unexpected_remote_call(*unused_args, **unused_kwargs):
                raise AssertionError("trusted receipt must skip remote upstream checks")

            self.module.ee.data.getAsset = unexpected_remote_call
            self.module.ee.data.listAssets = unexpected_remote_call
            self.module.ee.FeatureCollection = unexpected_remote_call
            try:
                result = self.module.resolve_upstream_validation(
                    args,
                    [{"mountain_id": "10067", "mountain_key": "gmba_10067"}],
                )
            finally:
                self.module.ee.data.getAsset = originals["getAsset"]
                self.module.ee.data.listAssets = originals["listAssets"]
                self.module.ee.FeatureCollection = originals["FeatureCollection"]

        self.assertEqual(result["mode"], "trusted_receipt")
        self.assertEqual(result["validation_id"], "test-validated-upstream")
        self.assertTrue(result["step1_integrity"]["ready"])
        self.assertEqual(
            result["deep_check"]["scope"], "representative_mountain"
        )

    def test_trusted_upstream_rejects_changed_manifest_or_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args, manifest, _ = validated_upstream_fixture(self.module, directory)
            manifest.write_text(
                json.dumps(
                    {
                        "configuration_hash": "changed",
                        "tiles": [{"tile_id": "N00_E000"}],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "trusted upstream receipt mismatch"):
                self.module.validate_trusted_upstream_receipt(args)

        with tempfile.TemporaryDirectory() as directory:
            args, _, receipt = validated_upstream_fixture(self.module, directory)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            payload["analysis_table"]["complete_property_count"] = 1
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "analysis_table.complete_property_count",
            ):
                self.module.validate_trusted_upstream_receipt(args)

        with tempfile.TemporaryDirectory() as directory:
            args, _, _ = validated_upstream_fixture(self.module, directory)
            args.global_tree_3m = "projects/example/assets/changed"
            with self.assertRaisesRegex(ValueError, "trusted upstream receipt mismatch"):
                self.module.validate_trusted_upstream_receipt(args)

    def test_explicit_upstream_revalidation_keeps_full_remote_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args, _, _ = validated_upstream_fixture(self.module, directory)
            args.revalidate_upstream = True
            calls = []
            replacements = {
                "validate_analysis_table": lambda unused_args: calls.append("table")
                or {"id": self.module.ANALYSIS_MOUNTAINS_ASSET},
                "resolve_required_tile_ids": (
                    lambda unused_args, unused_plan: calls.append("coverage")
                    or ["N00_E000"]
                ),
                "run_step1_integrity_check": (
                    lambda unused_args, required: calls.append(
                        ("step1", tuple(required))
                    )
                    or {"ready": True}
                ),
            }
            originals = {
                name: getattr(self.module, name) for name in replacements
            }
            for name, replacement in replacements.items():
                setattr(self.module, name, replacement)
            try:
                result = self.module.resolve_upstream_validation(
                    args,
                    [{"mountain_id": "10067", "mountain_key": "gmba_10067"}],
                )
            finally:
                for name, original in originals.items():
                    setattr(self.module, name, original)

        self.assertEqual(result["mode"], "live_revalidation")
        self.assertEqual(calls, ["table", "coverage", ("step1", ("N00_E000",))])

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

    def test_single_mountain_ab_bundle_can_explicitly_select_all_products(self) -> None:
        result = run_cli(
            "--dry-run",
            "--max-mountains",
            "1",
            "--allow-direct-1km-ab",
            "--export-products",
            "treeline30m",
            "treeline1km",
            "qa30m",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["products"], ["treeline30m", "treeline1km", "qa30m"]
        )
        self.assertEqual(payload["expected_task_count"], 3)
        self.assertTrue(payload["direct_1km_ab_bundle"])

    def test_direct_1km_ab_bundle_is_restricted_to_one_mountain(self) -> None:
        result = run_cli(
            "--dry-run",
            "--max-mountains",
            "2",
            "--allow-direct-1km-ab",
            "--export-products",
            "treeline30m",
            "treeline1km",
            "qa30m",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "--allow-direct-1km-ab requires --max-mountains 1",
            result.stderr,
        )

    def test_direct_1km_ab_bundle_requires_exactly_all_three_products(self) -> None:
        result = run_cli(
            "--dry-run",
            "--max-mountains",
            "1",
            "--allow-direct-1km-ab",
            "--export-products",
            "treeline1km",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "--allow-direct-1km-ab requires exactly treeline30m treeline1km qa30m",
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
                "--revalidate-upstream",
                "--analysis-mountains-asset",
                "projects/example/assets/filtered_gmba_basic",
                "--step1-manifest",
                str(manifest),
                "--max-mountains",
                "4",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ready"])
        self.assertEqual(
            payload["upstream_validation_mode"],
            "live_revalidation",
        )

    def test_dry_run_accepts_matching_trusted_upstream_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args, manifest, receipt = validated_upstream_fixture(
                self.module,
                directory,
            )
            result = run_cli(
                "--dry-run",
                "--project",
                args.project,
                "--step1-manifest",
                str(manifest),
                "--validated-upstream-receipt",
                str(receipt),
                "--max-mountains",
                "4",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ready"])
        self.assertEqual(
            payload["upstream_validation_mode"],
            "trusted_receipt",
        )
        self.assertEqual(
            payload["validated_upstream_id"],
            "test-validated-upstream",
        )

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
