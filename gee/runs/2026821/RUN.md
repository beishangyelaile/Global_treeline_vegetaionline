# GEEMu Run Log

## User Goal / 用户目标

审查 2026-08-21 新增的两份 Earth Engine JavaScript，将无坡向主版本与极向/赤道向敏感性版本合并转换为 Python/geemap，并执行无导出的首次小区域运行。

## Environment / 环境

- Cloud Project: `ee-wsc`
- Auth mode: Google Application Default Credentials (ADC)
- Python: `D:\LenovoSoftstore\Install\anaconda3\envs\geecodex\python.exe` (3.11.15)
- Earth Engine API / geemap: 1.7.32 / 0.37.2
- ADC online check: passed; quota project and detected project both `ee-wsc`
- Network: task-scoped HTTP/HTTPS proxy `127.0.0.1:7897`
- Python entry: `code.py`

## First-run design / 首跑设计

| Decision | Choice | Reason |
|---|---|---|
| Mode | `--check --aspect-mode none` | First tracer of the paper's main, non-aspect analysis |
| ROI | `[11.2, 47.1, 11.3, 47.2]` | Explicit 84.09 km² Alpine tracer |
| Fine grid | EPSG:4326, 0.00025° | Matches GLAD canopy grid used by the JS source |
| Aggregate grid | EPSG:4326, 30 arc-seconds | Fixed CHELSA-aligned output grid |
| Output | JSON Console report + geemap HTML | Read-only validation; no batch export |
| CHELSA transform | `raw * 0.1 - 273.15` | Uploaded UInt16 values are deci-Kelvin; output is °C |

Boundary risk is low for this rectangular tracer. The 2 km processing buffer protects connected-component and neighborhood operations from batch-edge artefacts. Global work remains a buffered tiling problem because connected components and Otsu calibration are not naively tile-independent.

## Command

```powershell
$env:HTTPS_PROXY='http://127.0.0.1:7897'
$env:HTTP_PROXY='http://127.0.0.1:7897'
& 'D:\LenovoSoftstore\Install\anaconda3\envs\geecodex\python.exe' `
  'gee\runs\2026821\code.py' --check --project ee-wsc `
  --gmba-asset 'projects/ee-remote/assets/Alpine/GMBA_v2' `
  --sayre-asset 'projects/ee-remote/assets/Alpine/high_mountain' `
  --chelsa-bio01 'projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21' `
  --bbox 11.2 47.1 11.3 47.2 --aspect-mode none
```

## First-run result / 首跑结果

- Status: `first-run-check-passed`
- GMBA features intersecting Sayre 31/32 in buffered tile: 1
- CHELSA raw range / mean / count: 2693–2800 / 2738.0589 / 156
- CHELSA converted range / mean: −3.85–6.85 °C / 0.6559 °C
- Candidate raw-integer histogram: 87 buckets, weighted sample count 61.0314
- Otsu threshold: 2748 raw; converted after segmentation to 1.65 °C
- Analysis-domain pixels: 137,641
- Treeline pixels: 393 (2000), 417 (2020)
- Non-empty 1 km shift pixels: 26
- AW3D MSK pixels: 137,641
- Exports started: false

The Otsu value is a tracer-specific diagnostic. Formal tiles must reuse a separately calibrated, fixed threshold over a scientifically defined common calibration domain.

## Review fixes / 审查修正

- Replaced the invalid `scale=1, offset=0` interpretation of the uploaded UInt16 CHELSA asset with deci-Kelvin conversion.
- Added ADC initialization explicitly; no legacy Earth Engine credential fallback is required.
- Added empty/degenerate histogram rejection and local, testable Otsu calculation.
- Set the Google Drive `folder` to `Globaltreeline` in both JS scripts and the Python export design.
- Changed Otsu to operate on unchanged integer BIO1 values; only its raw threshold is converted to °C.
- Kept exports behind explicit `--export`; a fixed Otsu threshold is mandatory.
- Added atomic task registry updates and optional GMBA CSV export support.
- Preserved the two JS behaviors through `--aspect-mode none|polar-equator`.

## Files / 生成文件

- Python: `code.py`
- Console: `first_run_console.json`
- Map: `first_run_map.html`
- Data semantics: `DATA_LAYER.md`
- Sources: `sources.md`
- Tests: `tests/test_2026821_code.py`

## Remaining uncertainty / 尚存不确定性

- The paper does not disclose the exact hole-size/connectivity, median kernel, Otsu calibration domain, variance assumption, test sidedness, or minimum sample count.
- GMBA Standard contains overlapping hierarchy levels; the hit count is an intersection diagnostic, not an independent-mountain sample size.
- The polar/equator branch was converted and compiled but was not part of this first online tracer.
- No export was requested or started.
