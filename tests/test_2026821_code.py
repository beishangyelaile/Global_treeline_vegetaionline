"""Offline tests for the 2026-08-21 Python/geemap port."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "gee" / "runs" / "2026821" / "code.py"


def load_module():
    spec = importlib.util.spec_from_file_location("treeline_2026821", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PythonPortTests(unittest.TestCase):
    def test_otsu_threshold_is_deterministic(self) -> None:
        module = load_module()
        threshold = module.otsu_threshold_from_histogram(
            {"histogram": [10, 1, 1, 10], "bucketMeans": [0, 1, 2, 3]}
        )
        self.assertEqual(threshold, 1)

    def test_otsu_rejects_empty_histogram(self) -> None:
        module = load_module()
        with self.assertRaisesRegex(ValueError, "empty or degenerate"):
            module.otsu_threshold_from_histogram({})

    def test_plausible_celsius_does_not_request_conversion(self) -> None:
        module = load_module()
        result = module.assess_bio1_conversion(
            {"bio01_raw_min": -8, "bio01_raw_max": 12, "bio01_raw_mean": 3}, 1, 0
        )
        self.assertIn("no conversion indicated", result["verdict"])

    def test_deci_kelvin_conversion_is_recognized(self) -> None:
        module = load_module()
        result = module.assess_bio1_conversion(
            {"bio01_raw_min": 2693, "bio01_raw_max": 2800, "bio01_raw_mean": 2738},
            0.1,
            -273.15,
        )
        self.assertIn("conversion confirmed", result["verdict"])

    def test_parser_defaults_match_uploaded_chelsa_encoding(self) -> None:
        module = load_module()
        args = module.build_parser().parse_args(["--dry-run"])
        self.assertEqual(args.temperature_scale, 0.1)
        self.assertEqual(args.temperature_offset, -273.15)

    def test_raw_otsu_threshold_is_converted_after_segmentation(self) -> None:
        module = load_module()
        self.assertAlmostEqual(module.convert_raw_temperature(2748, 0.1, -273.15), 1.65)

    def test_default_drive_folder_is_globaltreeline(self) -> None:
        module = load_module()
        args = module.build_parser().parse_args(["--dry-run"])
        self.assertEqual(args.drive_folder, "Globaltreeline")

    def test_dry_run_never_calls_earth_engine(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run", "--project", "ee-wsc"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ready"])
        self.assertIn("Application Default Credentials", payload["auth"])


if __name__ == "__main__":
    unittest.main()
