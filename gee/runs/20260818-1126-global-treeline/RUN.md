# Run record

## Research design

| Decision | Choice | Reason | Status |
|---|---|---|---|
| Study area | User-supplied bbox; tracer `[6,45,12,48]` | Global 30 m export is high risk and must be tiled | Tracer assumed; final ROI unconfirmed |
| Analysis scale | 30 m detection, 1 km mean aggregation | Matches the paper | Confirmed |
| Output target | Cloud-optimized GeoTIFF to default Google Drive destination | Auditable Earth Engine batch tasks | Destination type assumed; not started |

Boundary risk is high for the global study. Use bbox tiles and exact high-mountain
masking; do not export the full globe as one 30 m task.

## Environment gate

- Python: `D:\LenovoSoftstore\Install\anaconda3\envs\geeclaude\python.exe`
- Python version: 3.9.20
- earthengine-api: 1.3.1 (import passed)
- geemap: 0.35.1 (import passed)
- geopandas: 1.0.1 (import passed)
- Cloud project: `ee-wsc`
- Online Earth Engine initialization: passed on 2026-08-18
- Credential contents: never read or logged
- Proxy: no explicit environment override was needed for the successful check

## Method choices

- High mountains: classes 31 and 32 from the supplied asset.
- Valid 0.25 degree cells: at least 10% high mountain and at most 95% WorldCover tree class.
- Forest: GLAD height >3 m.
- Hole filling: 30 m closing is an explicit approximation; it can move outer boundaries and formal export requires `--accept-hole-filling-approximation`.
- Small outposts: connected forest area <0.5 ha removed.
- Edge: 1-pixel median then Laplacian zero crossing.
- Lower-edge filters: ERGo valley classes 41/42 removed; cold zone from CHELSA Otsu threshold.
- Local direction test: 300 m square window; two-sided alpha=0.05 Welch test with Welch-Satterthwaite degrees of freedom and at least 5 samples per group.
- Shift: `(elevation_2020_1km - elevation_2000_1km) / 20`.

## Export guardrail

`--dry-run` makes no Earth Engine call and reports approximate bbox area and
30 m pixel count. `--export` refuses to run until the CHELSA asset, band,
scale, offset, one fixed Otsu threshold, bbox, project, mountain asset and
method-approximation acknowledgement are resolved. A single call is capped at
25,000 km². A `PLANNED` registry is atomically written before any task starts
and updated after each task transition; use
`--monitor-once <registry>` for one status pass.

## Current blocker

No verified GEE asset ID was found for the CHELSA V2.1 annual mean temperature
used by the paper. NASADEM also lacks coverage north of 60 N and south of 56 S.
The supplement needed to identify the exact topology-preserving hole-fill
method and Otsu spatial scope was not available. Formal exports remain blocked.
