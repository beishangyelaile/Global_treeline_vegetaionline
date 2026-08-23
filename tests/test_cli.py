"""CLI guardrail tests that do not call Earth Engine."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "gee" / "runs" / "20260818-1126-global-treeline" / "code.py"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def load_run_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("global_treeline_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CliGuardrailTests(unittest.TestCase):
    def test_dry_run_reports_missing_temperature_without_network(self) -> None:
        result = run_cli(
            "--dry-run",
            "--project",
            "test-project",
            "--mountain-asset",
            "projects/example/assets/high_mountain",
            "--bbox",
            "6",
            "45",
            "12",
            "48",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ready_for_export"])
        self.assertTrue(any("CHELSA" in item for item in payload["missing_requirements"]))

    def test_dry_run_is_ready_when_temperature_semantics_are_explicit(self) -> None:
        result = run_cli(
            "--dry-run",
            "--project",
            "test-project",
            "--mountain-asset",
            "projects/example/assets/high_mountain",
            "--temperature-asset",
            "projects/example/assets/chelsa_bio1",
            "--temperature-band",
            "bio1",
            "--temperature-scale",
            "0.1",
            "--temperature-offset",
            "-273.15",
            "--temperature-threshold-c",
            "5.0",
            "--accept-hole-filling-approximation",
            "--bbox",
            "6",
            "45",
            "12",
            "48",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ready_for_export"])

    def test_export_refuses_missing_temperature_before_initialization(self) -> None:
        result = run_cli(
            "--export",
            "--project",
            "test-project",
            "--mountain-asset",
            "projects/example/assets/high_mountain",
            "--bbox",
            "6",
            "45",
            "12",
            "48",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CHELSA", result.stderr)

    def test_online_mode_initializes_before_geometry(self) -> None:
        module = load_run_module()
        events = []
        with mock.patch.object(module, "initialize", side_effect=lambda project: events.append("initialize")), mock.patch.object(
            module.ee.Geometry,
            "Rectangle",
            side_effect=lambda *args, **kwargs: events.append("geometry") or mock.sentinel.region,
        ), mock.patch.object(module, "build_products", return_value={}), mock.patch.object(
            module, "run_check", side_effect=lambda *args: events.append("check")
        ):
            result = module.main(
                [
                    "--check",
                    "--allow-missing-temperature",
                    "--project",
                    "test-project",
                    "--mountain-asset",
                    "projects/example/assets/high_mountain",
                    "--bbox",
                    "6",
                    "45",
                    "12",
                    "48",
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(events, ["initialize", "geometry", "check"])

    def test_welch_critical_values_are_conservative_for_small_samples(self) -> None:
        module = load_run_module()
        self.assertEqual(module.conservative_t_critical_95(1), -12.706)
        self.assertEqual(module.conservative_t_critical_95(5), -2.571)
        self.assertEqual(module.conservative_t_critical_95(10.5), -2.228)
        self.assertEqual(module.conservative_t_critical_95(11), -2.228)
        self.assertEqual(module.conservative_t_critical_95(12), -2.179)
        self.assertEqual(module.conservative_t_critical_95(500), -1.980)

    def test_temperature_qa_rejects_empty_and_degenerate_samples(self) -> None:
        module = load_run_module()
        with self.assertRaisesRegex(ValueError, "empty or degenerate"):
            module.validate_temperature_qa_info({})
        with self.assertRaisesRegex(ValueError, "empty or degenerate"):
            module.validate_temperature_qa_info(
                {
                    "temperature_c_count": 4,
                    "temperature_c_min": 2,
                    "temperature_c_max": 2,
                    "temperature_c_histogram": {"histogram": [4]},
                }
            )
        module.validate_temperature_qa_info(
            {
                "temperature_c_count": 4,
                "temperature_c_min": 1,
                "temperature_c_max": 2,
                "temperature_c_histogram": {"histogram": [2, 2]},
            }
        )

    def test_registry_write_is_atomic_and_valid_json(self) -> None:
        module = load_run_module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            module.write_registry_atomic(path, {"tasks": [{"state": "PLANNED"}]})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["tasks"][0]["state"], "PLANNED")
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
