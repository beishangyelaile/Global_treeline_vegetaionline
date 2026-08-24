# Sources

- Paper DOI: https://doi.org/10.1016/j.jag.2026.105088
- CHELSA-bioclim V2.1 variable definitions: https://www.chelsa-climate.org/datasets/chelsa_bioclim
- CHELSA V2.1 model: https://www.chelsa-climate.org/models/chelsa
- AW3D30 v4.1 Earth Engine catalog: https://developers.google.com/earth-engine/datasets/catalog/JAXA_ALOS_AW3D30_V4_1
- Global ALOS landforms Earth Engine catalog: https://developers.google.com/earth-engine/datasets/catalog/CSP_ERGo_1_0_Global_ALOS_landforms
- ESA WorldCover v100 Earth Engine catalog: https://developers.google.com/earth-engine/datasets/catalog/ESA_WorldCover_v100
- Maintained local implementation: `code_region_revised_v2.py`
- Historical JavaScript sources and structured first-run evidence are preserved in
  Git history or under `D:\实验复现\Globaltreeline_artifacts\2026821`.

Accessed 2026-08-21. The CHELSA target unit is sourced from the official variable definition; the uploaded asset's deci-Kelvin storage interpretation is an inference from its UInt16 metadata and measured pixel range.
# Asset export API

- Earth Engine `Export.image.toAsset`: https://developers.google.com/earth-engine/apidocs/export-image-toasset
