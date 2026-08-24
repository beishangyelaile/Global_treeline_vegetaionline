# Sources

- Paper DOI: https://doi.org/10.1016/j.jag.2026.105088
- JRC GFC2020 v2 paper: https://doi.org/10.5194/essd-18-1331-2026
- JRC GFC2020 v2 public code source: https://figshare.com/articles/code/Joint_Research_Centre_-_Global_Forest_Cover_for_year_2020_version_2_Code_source/29315528
- CHELSA-bioclim V2.1 variable definitions: https://www.chelsa-climate.org/datasets/chelsa_bioclim
- CHELSA V2.1 model: https://www.chelsa-climate.org/models/chelsa
- AW3D30 v4.1 Earth Engine catalog: https://developers.google.com/earth-engine/datasets/catalog/JAXA_ALOS_AW3D30_V4_1
- Global ALOS landforms Earth Engine catalog: https://developers.google.com/earth-engine/datasets/catalog/CSP_ERGo_1_0_Global_ALOS_landforms
- ESA WorldCover v100 Earth Engine catalog: https://developers.google.com/earth-engine/datasets/catalog/ESA_WorldCover_v100
- Maintained local implementation: `code_region_revised_v2.py`
- Historical JavaScript sources and structured first-run evidence are preserved in
  Git history or under `D:\实验复现\Globaltreeline_artifacts\2026821`.

Accessed 2026-08-24. The CHELSA target unit is sourced from the official variable definition; the uploaded asset's deci-Kelvin storage interpretation is an inference from its UInt16 metadata and measured pixel range. JRC alignment in this repository is limited to the binary 0.5 ha MMU post-processing and does not imply reproduction of the complete 10 m JRC forest land-use definition.
# Asset export API

- Earth Engine `Export.image.toAsset`: https://developers.google.com/earth-engine/apidocs/export-image-toasset
