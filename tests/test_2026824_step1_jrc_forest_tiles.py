"""Offline contracts for the Step 1 global forest-tile workflow."""

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
SCRIPT = ROOT / "gee" / "runs" / "2026824" / "code_step1_jrc_forest_tiles.py"


def load_module():
    spec = importlib.util.spec_from_file_location("step1_jrc_forest_tiles", SCRIPT)
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


class Step1ForestTileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_fixed_sources_and_mmu_constants(self) -> None:
        self.assertEqual(
            self.module.FOREST_HEIGHT_2000,
            "projects/glad/GLCLU2020/Forest_height_2000",
        )
        self.assertEqual(
            self.module.FOREST_HEIGHT_2020,
            "projects/glad/GLCLU2020/Forest_height_2020",
        )
        self.assertEqual(self.module.MMU_AREA_M2, 5000)
        self.assertEqual(self.module.MMU_MAX_SIZE, 50)
        self.assertEqual(self.module.MMU_CONNECTIVITY, 8)

    def test_jrc_sequence_uses_two_eight_connected_counts(self) -> None:
        source = inspect.getsource(self.module.apply_jrc_mmu)
        self.assertEqual(source.count("connectedPixelCount"), 2)
        self.assertEqual(source.count("eightConnected=True"), 2)
        self.assertIn("ee.Image.pixelArea().reproject(crs=proj)", source)
        self.assertIn("area1.lte(MMU_AREA_M2)", source)
        self.assertIn("area2.gte(MMU_AREA_M2)", source)
        self.assertLess(source.index("forest_after_fill"), source.index("count2"))
        self.assertNotIn("connectedComponents", source)
        self.assertNotIn("reduceConnectedComponents", source)

    def test_binary_threshold_is_strict_and_preserves_source_mask(self) -> None:
        source = inspect.getsource(self.module.build_forest_year)
        self.assertIn(".gt(canopy_threshold_m)", source)
        self.assertNotIn(".gte(canopy_threshold_m)", source)
        mmu_source = inspect.getsource(self.module.apply_jrc_mmu)
        self.assertIn("valid_mask = forest_raw.mask()", mmu_source)
        self.assertIn(".unmask(0)", mmu_source)
        self.assertIn(".updateMask(valid_mask)", mmu_source)

    def test_products_use_real_2000_and_2020_sources(self) -> None:
        source = inspect.getsource(self.module.build_threshold_product)
        self.assertIn("FOREST_HEIGHT_2000", source)
        self.assertIn("FOREST_HEIGHT_2020", source)
        self.assertIn('rename("tree_2000")', source)
        self.assertIn('rename("tree_2020")', source)

    def test_regular_grid_is_deterministic_and_not_mislabelled_global(self) -> None:
        tiles = self.module.generate_regular_tiles(-60, 80)
        self.assertEqual(len(tiles), 504)
        self.assertEqual(len({tile["tile_id"] for tile in tiles}), 504)
        self.assertEqual(tiles[0]["bbox"], [-180.0, -60.0, -170.0, -50.0])
        self.assertEqual(tiles[-1]["bbox"], [170.0, 70.0, 180.0, 80.0])
        self.assertFalse(self.module.latitude_range_is_global(-60, 80))
        self.assertTrue(self.module.latitude_range_is_global(-90, 90))

    def test_dry_run_with_manifest_is_exact_and_offline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "tiles.json"
            manifest.write_text(
                json.dumps(
                    {
                        "latitude_diagnostics": {
                            "outside_range_gmba_count": 0,
                            "outside_range_manifest_count": 0,
                            "outside_range_with_valid_forest_count": 0,
                        },
                        "tiles": self.module.generate_regular_tiles(-60, -40)[:3],
                    }
                ),
                encoding="utf-8",
            )
            result = run_cli(
                "--dry-run",
                "--tile-manifest",
                str(manifest),
                "--max-tiles",
                "2",
                "--tile-offset",
                "1",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["selection"]["selected_tile_count"], 2)
        self.assertEqual(payload["expected_task_count"], 4)
        self.assertEqual(payload["products"], ["h3m", "h5m"])

    def test_export_is_bounded_and_never_implicit(self) -> None:
        result = run_cli("--export")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--tile-manifest", result.stderr)
        self.assertIn("--max-tiles", result.stderr)

    def test_export_configuration_uses_transform_without_scale_or_gmba_clip(self) -> None:
        exporter = inspect.getsource(self.module.make_asset_export_task)
        self.assertIn("crsTransform=FINE_TRANSFORM", exporter)
        self.assertNotIn("scale=", exporter)
        self.assertNotIn("clip(", exporter)
        self.assertNotIn("clipToCollection", exporter)
        self.assertNotIn("updateMask", exporter)
        self.assertIn('".default": "mode"', exporter)

    def test_planned_assets_are_separate_by_threshold(self) -> None:
        args = self.module.build_parser().parse_args(["--dry-run"])
        tile = self.module.generate_regular_tiles(-60, -50)[0]
        records = self.module.planned_export_records(args, [tile])
        self.assertEqual(len(records), 2)
        self.assertEqual({record["product"] for record in records}, {"h3m", "h5m"})
        for record in records:
            self.assertTrue(record["destination"].endswith("/GFC_2000_2020_" + tile["tile_id"]))
            self.assertEqual(record["pyramiding_policy"]["tree_2000"], "mode")
            self.assertEqual(record["pyramiding_policy"]["tree_2020"], "mode")


if __name__ == "__main__":
    unittest.main()
