# Reproduction Log

## Current maintained implementation (2026-08-24)

- Sole supported entry point: `gee/runs/2026821/code_region_revised_v2.py`.
- Offline regression suite: `tests/test_code_region_revised_v2.py`.
- Current method and run documentation: `gee/runs/2026821/METHOD_GMBA_REVISED.md` and `gee/runs/2026821/RUN_REGION.md`.
- Earlier tracer, JavaScript, region-union and 2-degree-shard source files were removed from the working tree after their history had been preserved in Git.
- Generated task registries, console reports, HTML maps and the validation package were moved outside the source repository to `D:\实验复现\Globaltreeline_artifacts\2026821`.
- No Earth Engine task or Asset was changed during this repository cleanup.

## Paper

- Source: E:\论文+模拟\Zetero数据库\llm-for-zotero-mineru\1892
- Source type: directory
- Title: Global elevational shifts and drivers of alpine treelines
- Authors: Tianchen Liang, Feng Tian, Linqing Zou, Mathieu Gravey, Sabine B. Rumpf
- Journal/year: International Journal of Applied Earth Observation and Geoinformation 146 (2026), 105088
- DOI: 10.1016/j.jag.2026.105088

## Parsed Inputs

- Primary text: `full.md` (63,102 bytes)
- Metadata: `manifest.json`, `_llm_source.json`, `content_list.json`
- Figures: 12 extracted JPG files; Figure 1 workflow inspected.
- MinerU caveats: DOI strings contain spacing/OCR breaks; supplement is not included.

## Code Search

- `notes/paper-links.json` contains no code-like URL.
- Searched exact title/DOI with GitHub and the open web on 2026-08-18.
- No official released code was found or archived.
- Supplementary material was referenced by DOI but no direct public file was found.

## Data

- Supplied high-mountain asset is readable and contains classes 31/32 at ~232 m.
- Supplied GLAD 2000/2020 height assets are readable, band `b1`, observed values 0--35 at ~27.8 m.
- Public NASADEM, WorldCover 2020 and ERGo landforms were verified.
- Resolved on 2026-08-21: `projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21` is readable. Its UInt16 raw values require `raw*0.1-273.15` to recover °C.
- Coverage gap: NASADEM in the official catalog covers only 56 S to 60 N, so the paper's polar-global result cannot be exactly reproduced from this DEM alone.

## Target

- Target: GEE.
- Scope implemented: observed treeline elevation for 2000/2020 and 1-km annual shift rate.
- Full potential-treeline/TREELIM, predictor models and null model are outside this tracer because their climate, validation and human-footprint inputs were not supplied.

## Historical implementation (2026-08-18)

- Main script: `gee/runs/20260818-1126-global-treeline/code.py`
- Metadata probe: `gee/probe_assets.py`
- Tests: `tests/test_cli.py`
- Export is opt-in and writes a task registry under `outputs/tasks/`.
- Reusable graph components avoid rebuilding large collections per feature.

### Deviations

- Hole-filling radius is not stated in the paper; implementation uses a parameterized 30 m morphological closing.
- Morphological closing can move outer boundaries and is not equivalent to topology-preserving hole filling; export requires explicit acceptance.
- Otsu spatial scope is not stated; checks may derive a threshold, while all formal tiles must reuse one fixed threshold.
- The paper does not state equal-variance choice or test sidedness; implementation uses a two-sided alpha=0.05 Welch test with Welch-Satterthwaite degrees of freedom and at least five samples per group.
- CHELSA temperature filtering is implemented; the remaining formal-export requirement is a fixed threshold calibrated over a common, scientifically defined domain.

### Validation

- `geeclaude` environment and `ee-wsc` online initialization passed on 2026-08-18.
- All three user-specified Earth Engine assets were opened successfully.
- Seven CLI/ordering/statistical-table/temperature-QA/registry tests pass.
- Partial tracer exposed and fixed mutually exclusive neighborhood masks by setting `skipMasked=False`.
- After all independent-review fixes, the non-temperature tracer over `[11.2,47.1,11.3,47.2]` returned 4,477 (2000) and 4,539 (2020) treeline pixels plus 98 non-empty 1-km shift pixels.
- The tracer remains a partial validation because the CHELSA cold-zone filter was intentionally skipped.
- A separate API-only temperature-path check used WorldClim BIO1 (not a scientific substitute), found 128 candidate temperature cells, derived an Otsu threshold of 1.8 C, and returned 3,263 (2000) and 3,258 (2020) treeline pixels plus 68 non-empty 1-km shift pixels.
- Independent subagent review identified domain-boundary, t-test, Otsu alignment, projection, EECU and task-registry issues; these were corrected or promoted to explicit export blockers/approximations.
- Final independent subagent review found no remaining P0/P1 code issue and confirmed that only explicit `--export` can reach `task.start()`.
- The 2026-08-18 formal export was not started because temperature data were then missing; the 2026-08-21 asset and transform resolution below supersedes that blocker.

## 2026-08-21 Python/geemap port and first run

- Reviewed `gee/runs/2026821/gee_treeline_no_aspect.js`, `gee_treeline_polar_equator.js`, and `README_treeline_GEE.md`.
- Added `gee/runs/2026821/code.py`, merging both variants behind `--aspect-mode` and initializing Earth Engine explicitly with ADC for project `ee-wsc`.
- Corrected CHELSA handling after the uploaded UInt16 asset returned raw 2693--2800: physical conversion is `raw*0.1-273.15`, giving -3.85--6.85 °C in the tracer.
- Main-version first run over `[11.2,47.1,11.3,47.2]` passed: GMBA/Sayre hit count 1, Otsu threshold 1.65 °C, 393/417 treeline pixels for 2000/2020, and 26 non-empty 1-km shift pixels.
- Generated `first_run_console.json` and `first_run_map.html`; no export task was created or started.
- Eight offline tests cover Otsu, empty histograms, temperature semantics, raw-threshold conversion, Drive-folder defaults, and no-network dry-run behavior.

## 2026-08-21 raw-BIO1 Otsu adjustment

- Otsu now uses the unchanged UInt16 BIO1 values with a one-unit minimum histogram bucket width.
- The tracer derived a raw threshold of 2748; only this threshold was converted with `raw*0.1-273.15`, yielding 1.65 °C.
- Python and both JavaScript exports now target the Google Drive folder `Globaltreeline`.
- ADC authentication remained valid for `ee-wsc`; the repeated read-only tracer passed and started no export.
- Eight offline tests pass after adding raw-threshold conversion and Drive-folder defaults.

## Next steps

1. Define a common global/regional Otsu calibration domain and derive one fixed °C threshold.
2. Confirm buffered export tiling and whether to run the no-aspect or slope-partition product first.
3. Run the polar/equator online tracer, then start registered exports only after explicit confirmation.

## 2026-08-21 eight-region Asset workflow

- Added `gee/runs/2026821/code_region.py` support for the supplied eight-region manifest.
- Filtered GMBA v2 to `MapUnit=Basic`; integer-string `GMBA_V2_ID` joining matched all 978 manifest rows across eight `region_id` classes.
- Replaced Drive output with 24 planned `Export.image.toAsset` tasks: one treeline30m, treeline1km, and qa30m image per region.
- Verified the three destination ImageCollections exist and were empty; all 24 planned child asset IDs were absent.
- Retained the common fixed 1.65 °C threshold derived from raw BIO1 Otsu 2748.
- A bounded R8 tracer passed with ADC on `ee-wsc`; 14 offline tests passed. The full exports remain high compute risk because the region unions are large and geometrically complex.

## 2026-08-21 GMBA quarter-grid redesign

- Superseded the unsubmitted 24-task region-union plan. The study domain is now the complete geometry of all 978 matched GMBA Basic features, with no Sayre intersection.
- Generated globally aligned 0.25-degree analysis cells over the eight manifest-labelled GMBA sets: 14,392 cells in total.
- Grouped cells into 427 globally aligned 2-degree export shards (1,281 product tasks), limiting each shard to at most 64 quarter-degree cells.
- Replaced the fixed 1.65 °C formal-export threshold with per-shard Otsu on unchanged integer BIO1; only each derived threshold is converted to °C. Invalid histograms produce masked outputs and QA metadata, not a fixed fallback.
- ADC online check matched 978/978 features, resolved all 427 shards, found zero existing planned destinations, and passed the Alpine tracer without starting exports.

## 2026-08-22 first-20-mountain dynamic-Otsu pilot

- Selected the first 20 matched GMBA Basic mountains by numeric `GMBA_V2_ID` ascending: 10011, 10012, 10013, 10014, 10016, 10017, 10018, 10053--10061, and 10064--10067. All are in `R3_EUROPEAN_ALPS`.
- The 20 mountains resolved to 87 quarter-degree cells in five 2-degree shards, producing 15 Asset tasks under run label `first20_dynamic_v2` (30 m, 1 km, and QA per shard).
- Used explicit `shard-dynamic` raw-integer BIO1 Otsu with accepted shard dependence and accepted `hole_max_size_pixels=512`; only each resulting threshold is converted to degrees Celsius.
- Avoided synchronous full-shard Otsu and dependent `bandNames().getInfo()` checks after both returned `Object too large (115926796 bytes)`; the tracer still evaluates Otsu online, while formal dynamic graphs use static schema validation and asynchronous export execution.
- Submitted all 15 tasks to the three requested ImageCollections. Registry: `gee/runs/2026821/outputs/tasks/20260821T164838Z-treeline_region.json`.
- Fixed task-ID persistence by recording `task.id` after `task.start()` and added description-based recovery for the already-written registry. First recovered monitor: 3 RUNNING, 12 READY, 0 failed.
- Thirty-five offline tests passed after the selection, dynamic-preflight, and task-registry changes.
- A later status check confirmed all 15 pilot tasks completed successfully with no failed tasks.

## 2026-08-22 next-30-mountain dynamic-Otsu export

- Added offset-based mountain selection and selected numeric GMBA positions 21--50 (`--mountain-offset 20 --max-mountains 30`): 10068--10075, 11167, 11200, 11209, 11237, 11245, 11247, 11249--11252, 11255, 11259, 11278, 11284, 11286, 11288, 11319, 11320, 11323, 11334, 11335, and 11356.
- The selection spans five manifest regions: R2 Dry Tibetan Plateau (3 mountains), R3 European Alps (8), R5 Alaska/Yukon (2), R6 North American Rockies (7), and R7 Andes (10).
- The final online check passed with 30 selected mountains, 817 quarter-degree cells, 55 two-degree shards, 165 planned Asset exports, and no pre-existing destination conflicts. The bounded Alpine tracer returned raw BIO1 range 2750--2820 and raw Otsu 2785, equivalent to 5.35 degrees Celsius after threshold conversion.
- With explicit user confirmation, submitted all 165 tasks under run label `next30_dynamic_v2`, using `shard-dynamic`, accepted per-shard threshold dependence, and accepted `hole_max_size_pixels=512`.
- Submission completed without skips: 55 treeline30m, 55 treeline1km, and 55 qa30m tasks. Registry: `gee/runs/2026821/outputs/tasks/20260822T064715Z-treeline_region.json`.
- Initial registry-backed monitor at 2026-08-22T06:52:59Z reported 4 RUNNING and 161 READY, with no failed tasks and no recovered/missing task IDs.
- Before starting the following batch, the next-30 registry had advanced to 104 COMPLETED, 4 RUNNING, and 57 READY, with no failed tasks.

## 2026-08-22 next-50-mountain dynamic-Otsu export

- Selected numeric GMBA positions 51--100 (`--mountain-offset 50 --max-mountains 50`): 11362, 11525, 11526, 11573--11575, 11580, 11630, 11631, 11647, 11648, 11670, 11682, 11698, 11732, 11739, 11747, 11757, 11773, 11777, 11783--11785, 11791, 11812, 11888, 11901, 11904, 11909, 11913, 11933, 11934, 11967, 11990, 12003, 12014, 12015, 12049, 12050, 12053, 12054, 12085, 12086, 12102, 12116, 12133, 12136, 12137, 12195, and 12196.
- The selection spans six manifest regions: R1 Wet Himalaya (1 mountain), R2 Dry Tibetan Plateau (10), R5 Alaska/Yukon (7), R6 North American Rockies (18), R7 Andes (9), and R8 East African High Mountains (5).
- The user explicitly requested no formal `--check`; a read-only call to the production selection and grid-planning functions resolved 1,595 quarter-degree cells and 95 two-degree shards. Shard counts by region were R1=3, R2=16, R5=34, R6=21, R7=10, and R8=11.
- Submitted all 285 planned Asset tasks under run label `next50_dynamic_v2`, using `shard-dynamic`, accepted per-shard threshold dependence, and accepted `hole_max_size_pixels=512`. Submission had no existing/active skips: 95 treeline30m, 95 treeline1km, and 95 qa30m tasks.
- Registry: `gee/runs/2026821/outputs/tasks/20260822T132429Z-treeline_region.json`. The initial registry-backed monitor at 2026-08-22T13:33:53Z reported all 285 tasks READY, with no failed tasks and no recovered/missing task IDs.
