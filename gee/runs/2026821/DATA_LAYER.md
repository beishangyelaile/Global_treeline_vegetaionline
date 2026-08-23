# Data Layer

## Scope

- Target: observed alpine treeline elevation in 2000/2020 and 1 km annual shift rate.
- First-run extent: `[11.2,47.1,11.3,47.2]` plus a 2 km processing buffer.
- Fine scale: GLAD 0.00025° grid; aggregation: fixed 30 arc-second CHELSA grid.
- Output: local JSON diagnostics and geemap HTML preview; no export.

## Selected datasets

| Role | Dataset/asset | Type/band | Unit or classes | Transform / QA |
|---|---|---|---|---|
| Mountain polygons | `projects/ee-remote/assets/Alpine/GMBA_v2` | TABLE | GMBA v2 Standard hierarchy | Select polygons intersecting Sayre 31/32; levels overlap |
| High mountain | `projects/ee-remote/assets/Alpine/high_mountain` | IMAGE `b1`, Int8, ~0.002083° | 31 high mountain; 32 scattered high mountain | Use classes 31/32 |
| Canopy 2000 | `projects/glad/GLCLU2020/Forest_height_2000` | IMAGE `b1`, UInt8, 0.00025° | metres | Forest when height >3 m |
| Canopy 2020 | `projects/glad/GLCLU2020/Forest_height_2020` | IMAGE `b1`, UInt8, 0.00025° | metres | Forest when height >3 m |
| Climate | `projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21` | IMAGE `b1`, UInt16, 0.008333° | target physical unit °C | Uploaded storage is deci-Kelvin: `°C = raw*0.1 - 273.15` |
| Elevation | `JAXA/ALOS/AW3D30/V4_1` | ImageCollection `DSM`, `MSK`, `STK` | metres / mask / stack count | Default valid `MSK != 1`; strict sensitivity uses `MSK == 0` |
| Valleys | `CSP/ERGo/1_0/Global/ALOS_landforms` | IMAGE `constant`, 90 m | landform classes | Exclude 41 valley and 42 narrow valley |
| Tree-cover screen | `ESA/WorldCover/v100` | ImageCollection `Map`, 10 m | class 10 tree cover | Aggregate tree fraction to 0.25° |

## CHELSA BIO1 semantic check

The official CHELSA-bioclim definition gives `bio01` in °C. The uploaded asset itself contains no scale/offset metadata and is UInt16. Its first-run raw range is 2693–2800, which is physically impossible as °C but maps to −3.85–6.85 °C under `raw*0.1−273.15`. The transformed mean is 0.6559 °C, physically plausible for the selected Alpine tile.

| Stage | Formula | First-run range | Dtype/meaning |
|---|---|---|---|
| Uploaded storage | raw | 2693–2800 | UInt16, deci-Kelvin |
| Physical BIO1 | `raw*0.1−273.15` | −3.85–6.85 °C | floating-point annual mean temperature |
| Otsu input | unchanged uploaded BIO1 at 2020 candidate cells | 2693–2800 | UInt16 raw integers; minimum bucket width 1 |
| Otsu raw output | between-class variance maximum | 2748 | tracer-specific raw cold/hot threshold |
| Threshold conversion | `2748*0.1−273.15` | 1.65 °C | threshold used/reported in physical units |

The workflow runs Otsu before conversion, on the unchanged integer values. Only the resulting threshold is transformed to °C. Because the transform is positive and the raw bucket width of 1 corresponds to 0.1 °C, the cold/hot partition is preserved.

## Known risks

- The uploaded CHELSA asset has no metadata property recording its storage transform; the transform is inferred from dtype/range plus the official target unit and should be recorded with future asset ingestion metadata.
- The 1.65 °C Otsu threshold is not a global calibration result.
- Connected-component hole filling and local 300 m tests require buffered tiles.
- GMBA Standard level overlap prevents treating every selected feature as an independent mountain observation.
