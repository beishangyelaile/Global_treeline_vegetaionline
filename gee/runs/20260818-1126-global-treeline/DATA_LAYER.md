# Data layer audit

| Role | Asset | Band / classes | Units / dtype / valid range | Mask and fill semantics | Native scale | Status |
|---|---|---|---|---|---|---|
| High mountains | user parameter; supplied asset `projects/ee-remote/assets/Alpine/high_mountain` | `b1`, classes 31/32 | categorical int8; metadata range -128--127 | masked pixels are treated as non-high-mountain; source nodata code UNKNOWN | ~232 m, EPSG:4326 | Access verified |
| Canopy 2000 | `projects/glad/GLCLU2020/Forest_height_2000` | `b1`, observed 0--35 | metres, uint8 metadata range 0--255; forest is >3 m | physical 0 means no detected canopy; source nodata/mask code UNKNOWN | ~27.8 m, EPSG:4326 | Access verified |
| Canopy 2020 | `projects/glad/GLCLU2020/Forest_height_2020` | `b1`, observed 0--35 | metres, uint8 metadata range 0--255; forest is >3 m | physical 0 means no detected canopy; source nodata/mask code UNKNOWN | ~27.8 m, EPSG:4326 | Access verified |
| Elevation | `NASA/NASADEM_HGT/001` | `elevation` | metres relative to EGM96, int16 metadata range -32768--32767 | catalog fill/mask handling used; explicit fill code not remapped | 30 m | Public; coverage 56 S--60 N |
| Tree cover screen | `ESA/WorldCover/v100` | `Map == 10` | categorical uint8 metadata range 0--255 | masked pixels become zero only for coverage fractions; source fill code UNKNOWN | 10 m | Public, CC-BY-4.0 |
| Valleys | `CSP/ERGo/1_0/Global/SRTM_landforms` | `constant`; 41/42 valleys | categorical uint8 metadata range 0--255 | source fill/mask code UNKNOWN; masked pixels remain excluded | 90 m | Public, CC-BY-NC-SA-4.0 |
| Annual mean temperature | user parameter | UNKNOWN | `raw * scale + offset` Celsius; dtype/range UNKNOWN | nodata/mask UNKNOWN and must be documented before export | expected 30 arc-second | Missing verified asset/band/transform |

Output bands are cast to Float32 before export. Categorical intermediate masks
remain binary and are not mixed into exported float images.
