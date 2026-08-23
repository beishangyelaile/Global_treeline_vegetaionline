"""Read-only Earth Engine asset metadata probe."""

from __future__ import annotations

import argparse
import json

import ee


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Earth Engine Cloud project ID")
    parser.add_argument(
        "--asset",
        action="append",
        required=True,
        help="Earth Engine asset ID; repeat for multiple assets",
    )
    return parser.parse_args()


def describe_asset(asset_id: str) -> dict:
    metadata = ee.data.getAsset(asset_id)
    image = ee.Image(asset_id)
    band_names = image.bandNames().getInfo()
    bands = []
    for band_name in band_names:
        band = image.select([band_name])
        bands.append(
            {
                "name": band_name,
                "nominal_scale_m": band.projection().nominalScale().getInfo(),
                "projection": band.projection().getInfo(),
            }
        )
    return {
        "id": asset_id,
        "type": metadata.get("type"),
        "bands": bands,
        "properties": image.propertyNames().getInfo(),
    }


def main() -> None:
    args = parse_args()
    ee.Initialize(project=args.project)
    result = [describe_asset(asset_id) for asset_id in args.asset]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
