# Global treeline reproduction

This workspace reproduces the observed-treeline and 2000--2020 shift workflow
from Liang et al. (2026), *Global elevational shifts and drivers of alpine
treelines* (DOI: 10.1016/j.jag.2026.105088).

The runnable Earth Engine implementation is in
`gee/runs/20260818-1126-global-treeline/code.py`. It is export-safe by default:
plain and `--dry-run` execution never starts a task. A verified CHELSA V2.1
annual-mean-temperature asset is required for `--export`.

## Dry run

```powershell
& 'D:\LenovoSoftstore\Install\anaconda3\envs\geeclaude\python.exe' `
  '.\gee\runs\20260818-1126-global-treeline\code.py' `
  --dry-run --project ee-wsc `
  --mountain-asset 'projects/ee-remote/assets/Alpine/high_mountain' `
  --bbox 6 45 12 48
```

The dry run reports `ready_for_export: false` until
`--temperature-asset <CHELSA_ASSET>` and its band/scale/offset are supplied.

## Partial tracer check

This checks the non-temperature part over the European Alps and is explicitly
an approximation, not a paper-faithful result:

```powershell
& 'D:\LenovoSoftstore\Install\anaconda3\envs\geeclaude\python.exe' `
  '.\gee\runs\20260818-1126-global-treeline\code.py' `
  --check --allow-missing-temperature --project ee-wsc `
  --mountain-asset 'projects/ee-remote/assets/Alpine/high_mountain' `
  --bbox 6 45 12 48
```

## Export

After providing the missing CHELSA layer and confirming the ROI, scale and
Google Drive destination, replace `<...>` and run with `--export`. Earth Engine
writes to the default Drive destination; no Drive folder is set.

```powershell
& 'D:\LenovoSoftstore\Install\anaconda3\envs\geeclaude\python.exe' `
  '.\gee\runs\20260818-1126-global-treeline\code.py' `
  --export --project ee-wsc `
  --mountain-asset 'projects/ee-remote/assets/Alpine/high_mountain' `
  --temperature-asset '<CHELSA_ASSET>' --temperature-band '<BAND>' `
  --temperature-scale '<SCALE>' --temperature-offset '<OFFSET>' `
  --temperature-threshold-c '<FIXED_OTSU_THRESHOLD>' `
  --accept-hole-filling-approximation `
  --bbox '<WEST>' '<SOUTH>' '<EAST>' '<NORTH>'
```

One export invocation is limited to 25,000 km². Larger studies must be split
into explicit smaller bboxes that all reuse the same temperature threshold.

See `RUN.md`, `DATA_LAYER.md`, and `notes/reproduction-log.md` for assumptions,
known gaps, commands, and validation status.
