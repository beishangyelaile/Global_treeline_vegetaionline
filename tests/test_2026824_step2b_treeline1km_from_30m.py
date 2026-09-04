"""Offline contracts for materialized Step 2B 1 km aggregation."""

from __future__ import annotations

import copy
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
SCRIPT = (
    ROOT
    / "gee"
    / "runs"
    / "2026824"
    / "code_step2b_treeline1km_from_30m.py"
)


def source_registry_fixture(path: Path) -> None:
    source_hash = "6d6ea51646f2a01127c91d7e785e3d94c6aa4a2f3cc77fd0e35a94c2c8e815c3"
    child = "gmba_11158_step2_gmba_batch100_o005_20260828_6d6ea51646"
    tasks = []
    for product, collection, task_id in (
        ("treeline30m", "Treeline_30m_Collection", "TASK30"),
        ("treeline1km", "Treeline_1km_Collection", "TASK1K"),
        ("qa30m", "Treeline_QA30m_Collection", "TASKQA"),
    ):
        tasks.append(
            {
                "mountain_id": "11158",
                "mountain_key": "gmba_11158",
                "product": product,
                "destination": f"projects/example/assets/{collection}/{child}",
                "configuration_hash": source_hash,
                "state": "SUBMITTED",
                "task_id": task_id,
            }
        )
    path.write_text(
        json.dumps(
            {
                "project": "ee-wsc",
                "configuration_hash": source_hash,
                "tasks": tasks,
                "last_monitor": {
                    "tasks": [
                        {"task_id": "TASK30", "state": "COMPLETED"},
                        {
                            "task_id": "TASK1K",
                            "state": "FAILED",
                            "error_message": "Execution failed; out of memory",
                        },
                        {"task_id": "TASKQA", "state": "COMPLETED"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


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


def load_module():
    spec = importlib.util.spec_from_file_location("step2b_treeline1km", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def valid_source_asset_fixture(module):
    source_hash = "6d6ea51646f2a01127c91d7e785e3d94c6aa4a2f3cc77fd0e35a94c2c8e815c3"
    run_label = "step2_gmba_batch100_o005_20260828"
    child = f"gmba_11158_{run_label}_{source_hash[:10]}"
    destination = (
        "projects/example/assets/Treeline_30m_Collection/" + child
    )
    grid = {
        "crsCode": module.FINE_CRS,
        "affineTransform": {
            "scaleX": module.FINE_TRANSFORM[0],
            "shearX": 0,
            "translateX": 107.13825,
            "shearY": 0,
            "scaleY": module.FINE_TRANSFORM[4],
            "translateY": 55.82825,
        },
    }
    info = {
        "name": destination,
        "type": "IMAGE",
        "sizeBytes": "11685801",
        "bands": [
            {"id": band, "grid": copy.deepcopy(grid)}
            for band in module.SOURCE_BANDS
        ],
        "properties": {
            "mountain_id": "11158",
            "configuration_hash": source_hash,
            "run_label": run_label,
            "git_commit": "949c0558a0b0f72d1a5363f91bfb6f75222cafde",
            "workflow": "step2-per-gmba-sayre-treeline-v1",
        },
    }
    record = {
        "mountain_id": "11158",
        "destination": destination,
        "configuration_hash": source_hash,
        "task_id": "TASK30",
    }
    return info, record, run_label


class Step2BTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_v2_collections_are_the_defaults(self) -> None:
        args = self.module.build_parser().parse_args(["--dry-run"])
        self.assertEqual(
            args.source_treeline30m_collection,
            "projects/ee-alpine-506212/assets/Treeline_30m_Collection_v2",
        )
        self.assertEqual(
            args.source_qa30m_collection,
            "projects/ee-alpine-506212/assets/Treeline_QA30m_Collection_v2",
        )
        self.assertEqual(
            args.target_treeline1km_collection,
            "projects/ee-alpine-506212/assets/Treeline_1km_Collection_v2",
        )

    def test_dry_run_resolves_one_materialized_1km_task_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "source.json"
            source_registry_fixture(registry)
            result = run_cli(
                "--dry-run",
                "--source-registry",
                str(registry),
                "--mountain-ids",
                "11158",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["workflow"],
            "step2b-materialized-treeline30m-to-30arcsec-v2",
        )
        self.assertEqual(payload["mountain_ids"], ["11158"])
        self.assertEqual(payload["expected_task_count"], 1)
        self.assertTrue(payload["eligibility_requires_online_check"])
        self.assertFalse(payload["exports_started"])

    def test_materialized_four_band_image_is_aggregated_once(self) -> None:
        events = []

        class FakeImage:
            def __init__(self, bands=None) -> None:
                self.bands = list(bands or [])

            def select(self, bands):
                selected = [bands] if isinstance(bands, str) else list(bands)
                events.append(("select", tuple(selected)))
                return FakeImage(selected)

            def reduceResolution(self, **kwargs):
                events.append(("reduceResolution", kwargs))
                return FakeImage(self.bands)

            def reproject(self, projection):
                events.append(("reproject", projection))
                return FakeImage(self.bands)

            def rename(self, name):
                events.append(("rename", name))
                return FakeImage([name])

            def subtract(self, other):
                events.append(("subtract", tuple(other.bands)))
                return FakeImage(self.bands)

            def divide(self, value):
                events.append(("divide", value))
                return FakeImage(self.bands)

            def toFloat(self):
                events.append(("toFloat",))
                return self

        class ImageFactory:
            def __call__(self, asset_id):
                events.append(("Image", asset_id))
                return FakeImage()

            @staticmethod
            def cat(images):
                bands = [band for image in images for band in image.bands]
                events.append(("cat", tuple(bands)))
                return FakeImage(bands)

        class Reducer:
            @staticmethod
            def mean():
                return "mean"

        class FakeEE:
            Image = ImageFactory()

            @staticmethod
            def Projection(crs, transform):
                return (crs, tuple(transform))

        FakeEE.Reducer = Reducer

        original_ee = getattr(self.module, "ee", None)
        self.module.ee = FakeEE
        try:
            output = self.module.build_treeline1km_from_30m(
                "projects/example/assets/materialized30m"
            )
        finally:
            if original_ee is None:
                delattr(self.module, "ee")
            else:
                self.module.ee = original_ee

        self.assertEqual(events.count(("Image", "projects/example/assets/materialized30m")), 1)
        reductions = [event for event in events if event[0] == "reduceResolution"]
        self.assertEqual(len(reductions), 1)
        self.assertEqual(
            reductions[0][1],
            {"reducer": "mean", "bestEffort": False, "maxPixels": 2048},
        )
        self.assertEqual(
            [event for event in events if event[0] == "reproject"],
            [
                (
                    "reproject",
                    (
                        "EPSG:4326",
                        (1 / 120, 0, -180, 0, -1 / 120, 90),
                    ),
                )
            ],
        )
        self.assertEqual([event for event in events if event[0] == "divide"], [("divide", 20), ("divide", 20)])
        self.assertEqual(output.bands, self.module.expected_output_bands())

    def test_source_has_no_upstream_full_graph_operations_or_inputs(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        forbidden = (
            "Global_tree",
            "GLAD",
            "CHELSA",
            "AW3D",
            "landform",
            "mosaic",
            "focalMedian",
            "zeroCrossing",
            "Otsu",
            "reduceNeighborhood",
            "Welch",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)
        build_source = inspect.getsource(self.module.build_treeline1km_from_30m)
        self.assertEqual(build_source.count("ee.Image("), 1)
        self.assertEqual(build_source.count("reduceResolution("), 1)

    def test_source_asset_validation_is_strict_but_accepts_clipped_aligned_grid(self) -> None:
        info, record, run_label = valid_source_asset_fixture(self.module)
        provenance = self.module.validate_source_treeline30m_asset(
            info,
            record,
            expected_run_label=run_label,
        )
        self.assertEqual(provenance["source_step2_run_label"], run_label)
        self.assertEqual(
            provenance["source_step2_configuration_hash"],
            record["configuration_hash"],
        )

        invalid_cases = {}

        missing = None
        invalid_cases["missing"] = missing

        empty = copy.deepcopy(info)
        empty["sizeBytes"] = "0"
        invalid_cases["empty"] = empty

        wrong_type = copy.deepcopy(info)
        wrong_type["type"] = "TABLE"
        invalid_cases["type"] = wrong_type

        wrong_bands = copy.deepcopy(info)
        wrong_bands["bands"][0]["id"] = "unexpected"
        invalid_cases["bands"] = wrong_bands

        wrong_crs = copy.deepcopy(info)
        wrong_crs["bands"][0]["grid"]["crsCode"] = "EPSG:3857"
        invalid_cases["crs"] = wrong_crs

        wrong_scale = copy.deepcopy(info)
        wrong_scale["bands"][0]["grid"]["affineTransform"]["scaleX"] = 0.0003
        invalid_cases["scale"] = wrong_scale

        unaligned = copy.deepcopy(info)
        unaligned["bands"][0]["grid"]["affineTransform"]["translateX"] += 0.0001
        invalid_cases["alignment"] = unaligned

        wrong_mountain = copy.deepcopy(info)
        wrong_mountain["properties"]["mountain_id"] = "11157"
        invalid_cases["mountain_id"] = wrong_mountain

        wrong_hash = copy.deepcopy(info)
        wrong_hash["properties"]["configuration_hash"] = "different"
        invalid_cases["configuration_hash"] = wrong_hash

        wrong_run_label = copy.deepcopy(info)
        wrong_run_label["properties"]["run_label"] = "different"
        invalid_cases["run_label"] = wrong_run_label

        for label, candidate in invalid_cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    self.module.validate_source_treeline30m_asset(
                        candidate,
                        record,
                        expected_run_label=run_label,
                    )

    def test_failed_only_requires_latest_1km_oom_and_completed_30m(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "source.json"
            source_registry_fixture(registry_path)
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
            source_hash = payload["configuration_hash"]
            for mountain_id, suffix in (("11159", "B"), ("11160", "C")):
                for product, task_prefix in (
                    ("treeline30m", "TASK30"),
                    ("treeline1km", "TASK1K"),
                    ("qa30m", "TASKQA"),
                ):
                    payload["tasks"].append(
                        {
                            "mountain_id": mountain_id,
                            "mountain_key": f"gmba_{mountain_id}",
                            "product": product,
                            "destination": f"projects/example/assets/{product}/{mountain_id}",
                            "configuration_hash": source_hash,
                            "state": "SUBMITTED",
                            "task_id": task_prefix + suffix,
                        }
                    )
            payload["last_monitor"]["tasks"].extend(
                [
                    {
                        "task_id": "TASK30B",
                        "state": "FAILED",
                        "error_message": "Execution failed; out of memory",
                    },
                    {
                        "task_id": "TASK1KB",
                        "state": "FAILED",
                        "error_message": "Execution failed; out of memory",
                    },
                    {"task_id": "TASKQAB", "state": "COMPLETED"},
                    {"task_id": "TASK30C", "state": "COMPLETED"},
                    {
                        "task_id": "TASK1KC",
                        "state": "FAILED",
                        "error_message": "A non-memory error",
                    },
                    {"task_id": "TASKQAC", "state": "COMPLETED"},
                ]
            )
            registry_path.write_text(json.dumps(payload), encoding="utf-8")
            args = self.module.build_parser().parse_args(
                [
                    "--dry-run",
                    "--source-registry",
                    str(registry_path),
                    "--failed-only",
                ]
            )
            selected = self.module.resolve_offline_mountain_ids(args, payload)

        self.assertEqual(selected, ["11158"])

    def test_failure_classification_uses_five_required_groups(self) -> None:
        classify = self.module.classify_failed_task
        self.assertEqual(
            classify("treeline1km", "FAILED", "out of memory", True),
            "treeline1km_oom_with_completed_treeline30m",
        )
        self.assertEqual(
            classify("treeline1km", "FAILED", "out of memory", False),
            "treeline1km_oom_without_completed_treeline30m",
        )
        self.assertEqual(
            classify("treeline30m", "FAILED", "out of memory", False),
            "treeline30m_oom",
        )
        self.assertEqual(
            classify("qa30m", "FAILED", "out of memory", False),
            "qa30m_oom",
        )
        self.assertEqual(
            classify("treeline1km", "FAILED", "permission denied", True),
            "other_error",
        )
        self.assertIsNone(
            classify("treeline1km", "COMPLETED", "", True)
        )

    def test_new_hash_metadata_and_child_are_isolated_from_direct_product(self) -> None:
        info, source_record, run_label = valid_source_asset_fixture(self.module)
        provenance = self.module.validate_source_treeline30m_asset(
            info,
            source_record,
            expected_run_label=run_label,
        )
        source_hash = source_record["configuration_hash"]
        first_hash = self.module.aggregation_configuration_hash(source_hash)
        second_hash = self.module.aggregation_configuration_hash(source_hash)
        self.assertEqual(first_hash, second_hash)
        self.assertNotEqual(first_hash, source_hash)

        first = self.module.planned_step2b_record(
            mountain_id="11158",
            source_record=source_record,
            provenance=provenance,
            recovery_of_task_id="TASK1K",
            run_label="from30m_A",
            task_prefix="step2b",
            target_collection="projects/example/assets/Treeline_1km_Collection",
        )
        second = self.module.planned_step2b_record(
            mountain_id="11158",
            source_record=source_record,
            provenance=provenance,
            recovery_of_task_id="TASK1K",
            run_label="from30m_B",
            task_prefix="step2b",
            target_collection="projects/example/assets/Treeline_1km_Collection",
        )
        self.assertNotEqual(first["destination"], second["destination"])
        self.assertNotEqual(first["destination"], source_record["destination"])
        self.assertEqual(first["aggregation_configuration_hash"], first_hash)
        metadata = first["metadata"]
        self.assertEqual(metadata["source_step2_configuration_hash"], source_hash)
        self.assertEqual(metadata["aggregation_configuration_hash"], first_hash)
        self.assertEqual(metadata["recovery_of_task_id"], "TASK1K")
        self.assertEqual(
            set(metadata),
            {
                "workflow",
                "mountain_id",
                "source_treeline30m_asset",
                "source_step2_configuration_hash",
                "source_step2_run_label",
                "source_step2_git_commit",
                "aggregation_method",
                "aggregation_input_crs",
                "aggregation_input_transform",
                "aggregation_output_crs",
                "aggregation_output_transform",
                "aggregation_max_pixels",
                "aggregation_best_effort",
                "aggregation_configuration_hash",
                "implementation_sha256",
                "git_commit",
                "recovery_of_task_id",
                "run_label",
            },
        )

        with self.assertRaisesRegex(
            ValueError,
            "Step 2B run label must differ from the source Step 2A run label",
        ):
            self.module.planned_step2b_record(
                mountain_id="11158",
                source_record=source_record,
                provenance=provenance,
                recovery_of_task_id="TASK1K",
                run_label=run_label,
                task_prefix="step2b",
                target_collection="projects/example/assets/Treeline_1km_Collection",
            )

    def test_resume_guard_skips_same_hash_and_rejects_different_hash(self) -> None:
        record = {
            "destination": "projects/example/assets/target/child",
            "aggregation_configuration_hash": "new-hash",
            "state": "PLANNED",
        }
        same = {
            record["destination"]: {
                "properties": {"aggregation_configuration_hash": "new-hash"}
            }
        }
        with self.assertRaises(ValueError):
            self.module.apply_target_asset_guard([copy.deepcopy(record)], same, False)

        resumable = copy.deepcopy(record)
        self.module.apply_target_asset_guard([resumable], same, True)
        self.assertEqual(resumable["state"], "SKIPPED_EXISTING")

        different = {
            record["destination"]: {
                "properties": {"aggregation_configuration_hash": "old-hash"}
            }
        }
        with self.assertRaises(ValueError):
            self.module.apply_target_asset_guard(
                [copy.deepcopy(record)], different, True
            )

    def test_online_selection_requires_completed_and_valid_source_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "source.json"
            source_registry_fixture(registry_path)
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        info, _, _ = valid_source_asset_fixture(self.module)
        source_record = self.module.source_records_by_mountain(registry)["11158"][
            "treeline30m"
        ]
        info["name"] = source_record["destination"]
        info["properties"]["configuration_hash"] = registry["configuration_hash"]
        info["properties"]["mountain_id"] = "11158"
        info["properties"]["run_label"] = "step2_gmba_batch100_o005_20260828"

        args = self.module.build_parser().parse_args(
            [
                "--dry-run",
                "--source-registry",
                str(registry_path),
                "--failed-only",
            ]
        )
        selected, eligibility = self.module.resolve_online_mountain_ids(
            args,
            registry,
            self.module.latest_statuses(registry),
            {"11158": info},
        )
        self.assertEqual(selected, ["11158"])
        self.assertTrue(eligibility["11158"]["eligible_for_materialized_1km"])

        explicit = self.module.build_parser().parse_args(
            [
                "--dry-run",
                "--source-registry",
                str(registry_path),
                "--mountain-ids",
                "11158",
            ]
        )
        with self.assertRaises(ValueError):
            self.module.resolve_online_mountain_ids(
                explicit,
                registry,
                self.module.latest_statuses(registry),
                {"11158": None},
            )

    def test_diagnostic_rows_are_task_grained_and_use_live_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "source.json"
            source_registry_fixture(registry_path)
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        grouped = self.module.source_records_by_mountain(registry)
        info, _, _ = valid_source_asset_fixture(self.module)
        source_record = grouped["11158"]["treeline30m"]
        info["name"] = source_record["destination"]
        assets = {
            source_record["destination"]: info,
            grouped["11158"]["qa30m"]["destination"]: {
                "name": grouped["11158"]["qa30m"]["destination"],
                "type": "IMAGE",
            },
        }
        metrics = {
            "11158": {
                "gmba_area_km2": 100.0,
                "bounds_area_km2": 250.0,
                "bounds_to_gmba_area_ratio": 2.5,
                "required_step1_tile_count": 3,
            }
        }
        rows = self.module.build_diagnostic_rows(
            registry,
            self.module.latest_statuses(registry),
            assets,
            metrics,
        )
        self.assertEqual(len(rows), 3)
        direct = next(row for row in rows if row["product"] == "treeline1km")
        self.assertEqual(direct["task_state"], "FAILED")
        self.assertEqual(
            direct["failure_category"],
            "treeline1km_oom_with_completed_treeline30m",
        )
        self.assertTrue(direct["source_treeline30m_exists"])
        self.assertTrue(direct["source_treeline30m_valid"])
        self.assertTrue(direct["source_qa30m_exists"])
        self.assertTrue(direct["eligible_for_materialized_1km"])
        self.assertEqual(direct["required_step1_tile_count"], 3)
        counts = self.module.failure_category_counts(rows)
        self.assertEqual(
            counts,
            {
                "treeline1km_oom_with_completed_treeline30m": 1,
                "treeline1km_oom_without_completed_treeline30m": 0,
                "treeline30m_oom": 0,
                "qa30m_oom": 0,
                "other_error": 0,
            },
        )

    def test_diagnostic_report_refuses_to_overwrite_source_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text('{"original": true}', encoding="utf-8")
            with self.assertRaises(ValueError):
                self.module.write_new_diagnostic_report(
                    source,
                    {"status": "diagnostic"},
                    source_registry=source,
                )
            self.assertEqual(
                json.loads(source.read_text(encoding="utf-8")),
                {"original": True},
            )

    def test_check_serializes_expression_without_starting_task(self) -> None:
        class Expression:
            def __init__(self) -> None:
                self.calls = []

            def serialize(self, *, pretty, for_cloud_api):
                self.calls.append((pretty, for_cloud_api))
                return '{"type":"Invocation"}'

        class Task:
            def __init__(self, expression) -> None:
                self.config = {
                    "expression": expression,
                    "assetExportOptions": {"earthEngineDestination": {}},
                }

            def start(self):
                raise AssertionError("read-only check must not start a task")

        expression = Expression()
        size = self.module.serialized_export_expression_bytes(Task(expression))
        self.assertEqual(size, len('{"type":"Invocation"}'.encode("utf-8")))
        self.assertEqual(expression.calls, [(False, True)])

    def test_comparison_reports_complete_case_and_per_band_validity(self) -> None:
        source = inspect.getsource(self.module.compare_direct_and_materialized)
        self.assertIn('"complete_case_all_six"', source)
        self.assertIn('"per_band_pairwise"', source)
        self.assertIn('"pairwise_valid_by_band"', source)

    def test_post_export_comparison_receives_materialized_step2b_asset(self) -> None:
        direct_destination = "projects/example/assets/direct/gmba_11158"
        target_destination = "projects/example/assets/materialized/gmba_11158"
        direct_record = {
            "mountain_id": "11158",
            "product": "treeline1km",
            "destination": direct_destination,
            "task_id": "TASK1K",
        }
        source_record = {
            "mountain_id": "11158",
            "product": "treeline30m",
            "destination": "projects/example/assets/source/gmba_11158",
            "task_id": "TASK30",
        }
        qa_record = {
            "mountain_id": "11158",
            "product": "qa30m",
            "destination": "projects/example/assets/qa/gmba_11158",
            "task_id": "TASKQA",
        }
        target_record = {
            "mountain_id": "11158",
            "source_treeline30m_asset": source_record["destination"],
            "destination": target_destination,
        }
        materialized_info = {"name": target_destination, "type": "IMAGE"}
        context = {
            "registry": {
                "tasks": [source_record, direct_record, qa_record],
                "configuration_hash": "source-hash",
            },
            "statuses": {"TASK1K": {"id": "TASK1K", "state": "COMPLETED"}},
            "direct_assets": {
                direct_destination: {"name": direct_destination, "type": "IMAGE"}
            },
            "target_assets": {target_destination: materialized_info},
            "records": [target_record],
            "selected_mountain_ids": ["11158"],
        }
        args = self.module.build_parser().parse_args(
            [
                "--check",
                "--source-registry",
                "source.json",
                "--mountain-ids",
                "11158",
            ]
        )
        captured = {}
        replacements = {
            "exact_analysis_geometry": lambda unused_args, unused_id: "region",
            "compare_direct_and_materialized": (
                lambda record, direct_info, region, target_info=None: captured.update(
                    {
                        "record": record,
                        "direct_info": direct_info,
                        "region": region,
                        "target_info": target_info,
                    }
                )
                or {"status": "compared", "mountain_id": "11158"}
            ),
            "independent_overlap_sample_check": (
                lambda unused_record, unused_region: {
                    "status": "passed",
                    "mountain_id": "11158",
                }
            ),
        }
        originals = {name: getattr(self.module, name) for name in replacements}
        for name, replacement in replacements.items():
            setattr(self.module, name, replacement)
        try:
            comparisons, checks = self.module.run_read_only_comparisons(args, context)
        finally:
            for name, original in originals.items():
                setattr(self.module, name, original)

        self.assertEqual(comparisons[0]["status"], "compared")
        self.assertEqual(checks[0]["status"], "passed")
        self.assertIs(captured["target_info"], materialized_info)

    def test_export_requires_an_explicit_bounded_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "source.json"
            source_registry_fixture(registry)
            result = run_cli("--export", "--source-registry", str(registry))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "--export requires --max-mountains or --mountain-ids",
            result.stderr,
        )

    def test_explicit_check_queries_only_selected_sibling_task_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "source.json"
            source_registry_fixture(registry_path)
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        args = self.module.build_parser().parse_args(
            [
                "--check",
                "--source-registry",
                str(registry_path),
                "--mountain-ids",
                "11158",
            ]
        )
        self.assertEqual(
            self.module.selection_task_ids(args, registry),
            ["TASK30", "TASK1K", "TASKQA"],
        )

    def test_band_contract_and_shift_formula_are_literal_and_fixed(self) -> None:
        self.assertEqual(self.module.FINE_CRS, "EPSG:4326")
        self.assertEqual(
            self.module.FINE_TRANSFORM,
            [0.00025, 0, -180, 0, -0.00025, 90],
        )
        self.assertEqual(self.module.CLIMATE_CRS, "EPSG:4326")
        self.assertEqual(
            self.module.CLIMATE_TRANSFORM,
            [1 / 120, 0, -180, 0, -1 / 120, 90],
        )
        self.assertEqual(
            self.module.SOURCE_BANDS,
            [
                "treeline_2000_h3m_m",
                "treeline_2020_h3m_m",
                "treeline_2000_h5m_m",
                "treeline_2020_h5m_m",
            ],
        )
        self.assertEqual(
            self.module.OUTPUT_BANDS,
            [
                "treeline_2000_h3m_mean_m",
                "treeline_2020_h3m_mean_m",
                "shift_2000_2020_h3m_m_per_year",
                "treeline_2000_h5m_mean_m",
                "treeline_2020_h5m_mean_m",
                "shift_2000_2020_h5m_m_per_year",
            ],
        )
        source = inspect.getsource(self.module.build_treeline1km_from_30m)
        self.assertEqual(source.count(".divide(20)"), 2)
        self.assertNotIn("unmask", source)

    def test_only_submission_function_may_start_tasks(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(source.count(".start()"), 1)
        submit_source = inspect.getsource(self.module.submit_preflighted_tasks)
        self.assertIn(".start()", submit_source)
        self.assertNotIn(".start()", inspect.getsource(self.module.run_check))
        self.assertNotIn(".start()", inspect.getsource(self.module.run_diagnose))
        self.assertNotIn(".start()", inspect.getsource(self.module.monitor_once))

    def test_check_rejects_failed_independent_overlap_validation(self) -> None:
        args = self.module.build_parser().parse_args(
            [
                "--check",
                "--source-registry",
                "source.json",
                "--mountain-ids",
                "11158",
            ]
        )
        replacements = {
            "initialize_with_adc": lambda project: {"project": project},
            "prepare_online_run": lambda unused_args: {
                "registry": {"configuration_hash": "source-hash"},
                "records": [],
                "selected_mountain_ids": ["11158"],
                "target_assets": {},
                "preflight_tasks": [],
            },
            "run_read_only_comparisons": lambda unused_args, unused_context: (
                [],
                [{"status": "failed", "mountain_id": "11158"}],
            ),
        }
        originals = {
            name: getattr(self.module, name) for name in replacements
        }
        for name, replacement in replacements.items():
            setattr(self.module, name, replacement)
        try:
            with self.assertRaisesRegex(
                ValueError,
                "independent overlap validation failed",
            ):
                self.module.run_check(args)
        finally:
            for name, original in originals.items():
                setattr(self.module, name, original)

    def test_monitor_rejects_legacy_registry_before_any_online_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "source.json"
            source_registry_fixture(registry_path)
            original = registry_path.read_bytes()
            args = self.module.build_parser().parse_args(
                ["--monitor-once", str(registry_path)]
            )
            with self.assertRaises(ValueError):
                self.module.monitor_once(args)
            self.assertEqual(registry_path.read_bytes(), original)

    def test_independent_overlap_mean_uses_explicit_pixel_cell_intersection(self) -> None:
        size = abs(self.module.FINE_TRANSFORM[0])
        bounds = [0.0, 0.0, 2 * size, 2 * size]
        fine_features = []
        values = [1.0, 3.0, 5.0, 7.0]
        centers = [
            (size / 2, size / 2),
            (3 * size / 2, size / 2),
            (size / 2, 3 * size / 2),
            (3 * size / 2, 3 * size / 2),
        ]
        for center, value in zip(centers, values):
            fine_features.append(
                {
                    "geometry": {"coordinates": list(center)},
                    "properties": {
                        band: value for band in self.module.SOURCE_BANDS
                    },
                }
            )
        result = self.module.explicit_overlap_weighted_means(
            bounds, fine_features
        )
        self.assertEqual(result["fine_sample_count"], 4)
        self.assertEqual(result["fine_overlap_count"], 4)
        self.assertEqual(
            result["means"],
            {band: 4.0 for band in self.module.SOURCE_BANDS},
        )

    def test_overlap_mean_handles_partial_weights_and_band_masks(self) -> None:
        masked_band = self.module.SOURCE_BANDS[-1]
        first_values = {band: 0.0 for band in self.module.SOURCE_BANDS}
        second_values = {band: 10.0 for band in self.module.SOURCE_BANDS}
        second_values[masked_band] = None
        result = self.module.explicit_overlap_weighted_means(
            [0.0, 0.0, 0.0003, 0.00025],
            [
                {
                    "geometry": {"coordinates": [0.000125, 0.000125]},
                    "properties": first_values,
                },
                {
                    "geometry": {"coordinates": [0.000375, 0.000125]},
                    "properties": second_values,
                },
            ],
        )
        self.assertEqual(result["fine_overlap_count"], 2)
        for band in self.module.SOURCE_BANDS[:-1]:
            self.assertAlmostEqual(result["weight_sums"][band], 1.2)
            self.assertAlmostEqual(result["means"][band], 10.0 / 6.0)
        self.assertAlmostEqual(result["weight_sums"][masked_band], 1.0)
        self.assertEqual(result["means"][masked_band], 0.0)


if __name__ == "__main__":
    unittest.main()
