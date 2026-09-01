# Reproduction Log

## 2026-09-01 Step 2A Otsu aggregation tile fix

- A live read-only snapshot of the first 200-mountain Step 2A v2 registry found 87 `COMPLETED`, 2 `RUNNING`, 301 `READY`, and 10 `FAILED` tasks. Failures were paired `treeline30m`/`qa30m` tasks for GMBA 10095, 11106, 11121, 11153, and 11158, with identical sibling errors of `Object too large` at 121,353,480; 289,918,440; 182,570,412; 290,907,120; and 289,952,784 bytes respectively. Exact h3m reproduction for 10095 had the same 121,353,480-byte error before Welch testing or either product branch, fixing the root cause at the shared per-GMBA Otsu histogram aggregation rather than export size, projection, task queue, or product schema.
- The Step 2A Otsu `reduceRegion` default `tileScale` changed from 4 to 8. No projection, pixel grid, histogram reducer, threshold rule, Welch test, product band, Asset collection, or Step 2B behavior changed. The explicit fine-grid default projection after each forest mosaic remains mandatory. A larger `tileScale` reduces the aggregation tile footprint and is an execution-memory control, not a scientific resampling change.
- Live evaluation with `tileScale=8` returned `valid=1` for both h3m/h5m in every failed mountain: 10095 = 15.75/15.65 C, 11106 = 19.25/19.25 C, 11121 = 4.55/4.55 C, 11153 = -6.45/-6.45 C, and 11158 = -5.65/-5.55 C. Representative GMBA 10067 remained exactly identical to the prior `tileScale=4` receipt across sample counts, histogram bucket counts, raw thresholds, Celsius thresholds, source, and valid flags: 2.45 C for h3m and 2.55 C for h5m.
- Trusted receipt `gee/runs/2026824/step2_validated_upstream_20260831.json` advanced to validation ID `upstream-gmba-sayre-step1-20260831-v2`, configuration hash `d49bd16592abfc9bc5654068a49d5da8e522f7718afcfc49ba02c33fa416134f`, and implementation SHA-256 `908895acd130bc3d57fc7a2e0724e845fb506f59fb45e565a3217ffa92a333cb`. Its raw/canonical SHA-256 values are `e328dd7e8fd231378e9f2650ed0842c11eefd68d5edaa5af41f8ec22d0309677` and `98ec7186e2578266a4bfaee1118938a2081a30d03abbb74790e5dc7c311d32f3`. The previously fixed TABLE, Step 1 inventory, all-mountain coverage, and 10067 threshold facts were reused without repeating the expensive upstream gates.
- Integrated `--check --deep-check` for GMBA 10067 passed with `exports_started=false`, serialized 101,516-byte treeline30m and 115,829-byte qa30m task graphs, and confirmed the unchanged thresholds. Report `D:\\实验复现\\Globaltreeline_artifacts\\20260901-step2a-otsu-tilescale8\\step2a_check_gmba10067.json` has SHA-256 `871553aacc8d7ec2de1892bdd1c891fb3e1c963751e1b05ea7601b7449993ef6`.
- Offline acceptance passed 30 focused Step 2A tests, then all 81 pytest tests plus 21 subtests; all three maintained Python entries compiled and `git diff --check` passed. The CI Step 2A fixture dry-run now explicitly selects offline live-revalidation planning so its `test-project` and one-tile fixture are not compared with the production receipt; final CI dry-run counts are Step 1 = 2, Step 2A = 20, and Step 2B = 1.
- Commit `a243efb` fixed the production implementation before recovery submission, so exported metadata records the corrected code revision. Five separate bounded dry-runs at offsets 29, 31, 36, 42, and 46 each resolved two default products, receipt v2, and hash `d49bd16592...`. Ten recovery tasks were then submitted under run label `gmba_sayre_step2_v2_ts8_recovery_20260831`, with registries in `D:\\实验复现\\Globaltreeline_artifacts\\20260901-step2a-v2-ts8-recovery\\tasks`: 10095 = `BQELIBWRZRO5U4T5PUAJ4XXA`/`BLNWKNYKH2LCOLJTRPF3N5ZI`, 11106 = `YIMJWM4DPOVLXXENVFNKGKOB`/`S6BSHZKPR35NP3QPU42S5WBK`, 11121 = `UTMKFFK6IBXJX7LFTS3IZ7N2`/`7HJDU3K5KNOZOMDDCX2TV5AB`, 11153 = `3BV3L3L6PVJ2KHZAQF3VAFE7`/`6S6D2YQ6QXTOFDKHNBTTW32U`, and 11158 = `C4W323GZVENIEDMVKECVBQEF`/`NUHM6JXW63TJXH4UQO7WBYFO` (treeline30m/qa30m order). All ten initially entered `READY`; no old task or Asset was cancelled, deleted, resumed, or overwritten, and no Step 2B or 200-mountain top-up was started.
- The original 200-mountain registry later reached its terminal state with 320 `COMPLETED` and 80 `FAILED` tasks. The failures were exactly 40 paired `treeline30m`/`qa30m` mountains and every error was `Object too large`; no other failure class occurred. After the first five recovery mountains, the remaining 35 were 11179, 11199, 11205, 11216, 11238, 11259, 11278, 11280, 11284, 11296, 11323, 11325, 11338, 11345, 11379, 11382, 11392, 11393, 11408, 11422, 11424, 11449, 11472, 11487, 11494, 11495, 11499, 11501, 11502, 11508, 11513, 11517, 11522, 11527, and 11529. Their zero-based offsets, recomputed from the original registry after deduplicating the two product records per mountain, were 55, 61, 64, 67, 73, 83, 87-89, 93, 103, 105, 111, 114, 130, 133, 137-138, 142, 150-151, 162, 174, 177, 179-180, 182-185, 188, 190-191, 193, and 195.
- All 26 bounded dry-runs for those offsets passed with 35 mountains, 70 expected tasks, default products `treeline30m,qa30m`, trusted validation ID `upstream-gmba-sayre-step1-20260831-v2`, and configuration hash `d49bd16592abfc9bc5654068a49d5da8e522f7718afcfc49ba02c33fa416134f`. The 70 tasks were then submitted serially from clean repository HEAD `89b06d7` under the same recovery run label and registry directory, using queue safety limit 100. Together with the first ten tasks, the directory contains 31 registries, 40 unique mountains, 80 unique task IDs, and 80 unique destinations; every mountain has exactly one task in `Treeline_30m_Collection_v2` and one in `Treeline_QA30m_Collection_v2`.
- The post-submission Earth Engine snapshot at `2026-09-01T07:57:59Z` returned all 80 requested task IDs with 10 `COMPLETED`, 2 `RUNNING`, 68 `READY`, zero failures/cancellations, and zero missing IDs. Hourly heartbeat `step-2a-tilescale-8-recovery-monitor` now monitors only these recovery registries; it does not repeat deep/upstream checks or start Step 2B. It may submit the already authorized next 200-mountain Step 2A batch at offset 200 only after all 80 recovery tasks and Assets pass validation and the active READY count is below 100.

## 2026-08-31 trusted upstream receipt for Step 2A batch startup

- Added tracked receipt `gee/runs/2026824/step2_validated_upstream_20260831.json`, validation ID `upstream-gmba-sayre-step1-20260831-v1`. It binds project `ee-wsc`, all formal input Asset IDs, the exact Step 1 manifest canonical SHA-256 `fffb0bec26a8865fbef86e41d2cd690b199b59188b6ed94d4ee5dce3ce263f2e`, all scientific parameters, and scientific plus validation source SHA-256 `e111a64f945d88a9bca3e943e6b1701d6584da115ffef4a3b31a43f3ec26bd18`. Receipt raw/canonical SHA-256 values are `1f72e4241b9e4d538829cded26dfa7691cb243fe3a73b8eed939a911ff35c93e` and `d2282a45c8348485de71067018170aa21c7e98e23a93c9cc354e4b88032d182d`.
- Fixed the already established TABLE facts: 3,115 source/complete/selected features, 3,115 distinct GMBA IDs, all `MapUnit=Basic`, and zero records outside the fixed high-mountain/tree-fraction limits. Fixed the Step 1 facts: 304 expected h3m and 304 h5m non-empty validated child Assets under configuration hash `33b97c487aa3117d2bc6fb988b0cb0490949bfcd26dbb3ba4355f5a658940542`.
- A one-time read-only all-domain coverage evaluation found that all 3,115 selected mountains intersect 182 unique 10-degree tiles, all present in the 304-tile manifest. The sorted required-tile SHA-256 is `2ddf57615849e45f14a37f678c946b7e57a65856070d08eee7fcb34d43902239`. The previously completed GMBA 10067 deep check is fixed as representative execution evidence: h3m/h5m were both valid, with Otsu thresholds 2.45 C and 2.55 C.
- Ordinary Step 2A dry-run/check/export now verifies the receipt identity locally, then skips remote TABLE aggregation, 608 individual Step 1 child reads, per-batch 648-tile coverage evaluation, and deep check. `--revalidate-upstream` restores the live TABLE/Step 1/current-batch coverage gates; `--check --revalidate-upstream --deep-check` also repeats the reducer. Output-target conflicts, active-task checks, queue limits, bounded selection, graph construction and export metadata remain active. No export task was created in this change.
- Final offline acceptance compiled all three maintained entries and passed 81 pytest tests plus 21 subtests; `git diff --check` passed. The real formal manifest dry-run for `mountain-offset=105`, `max-mountains=200` resolved `ready=true`, `mode=trusted_receipt`, validation ID `upstream-gmba-sayre-step1-20260831-v1`, default products `treeline30m,qa30m`, and 400 expected tasks without an Earth Engine request.

## 2026-08-31 GMBA 10067 materialized direct/from-30m A/B export

- Commit `8ceead249cdcfcb2c05a7f84e282bfeb5d35e8f1` switched the three defaults to `Treeline_30m_Collection_v2`, `Treeline_1km_Collection_v2`, and `Treeline_QA30m_Collection_v2`; added a three-product A/B exception restricted to `--max-mountains 1`; and aligned both legacy direct and Step 2B aggregation to `reduceResolution(mean, bestEffort=False, maxPixels=2048)`. The ordinary Step 2A default remains `treeline30m qa30m`.
- The authorized Step 2A run selected only GMBA 10067 (`mountain-offset=19`) with configuration hash `cca8f504c770d21441b5fed4a31cc097e5b4168474d6f3b5cabdf93a1350b740`. Tasks `EVAMGVIM67GDIVTD2HLPLHUU` (`treeline30m`), `PYKC3ELY7XI4DSBWWRJUTKK7` (legacy direct `treeline1km`), and `C2Y4OWI2425ZONTSQRQIJVTO` (`qa30m`) all completed. All three Assets were non-empty IMAGEs with exact bands, fixed output grids, source hash, mountain ID, run label, workflow, and Git provenance.
- Only after those three completions and Asset validation, Step 2B check passed source/provenance/target guards, serialized one 8,517-byte graph, and passed three independent fine-pixel overlap checks. The single Step 2B task `UYPVDTILPXWFB42JR7EQKSKQ` completed on attempt 1. Its aggregation hash is `a05ee72128f881bb7c2c9e8e59752cbe261db287349737e93f3aeed885dd8244`.
- The final check compared the two materialized 1 km Assets, not a virtual graph. Across the all-six-band complete cases, direct had 64 valid cells, Step 2B had 65, and one cell had a different complete-case mask. Per-band direct/Step2B valid counts were 68/70, 71/71, 68/70, 66/68, 67/67, and 64/65; mask-mismatch counts were 2, 0, 2, 2, 2, and 1. Pairwise mean differences (Step 2B minus direct) were `-0.520414`, `-0.984145`, `-0.0104776`, `-1.961039`, `-0.393513`, and `+0.0332253`; maximum absolute differences were `281.753662`, `281.753662`, `1.877380`, `262.290283`, `261.806396`, and `2.819885` in band units.
- Equal `maxPixels` and `bestEffort` did not make the paths equivalent because their aggregation inputs use different grids. The legacy direct graph inherited the upstream AW3D30 1-arc-second projection (`0.000277777...°`, tile anchor 6°/46°), whereas Step 2B read the exported globally aligned 0.00025° Asset (clipped transform anchored at 6.7665°/46.025° for this mountain). Thus the target cells receive different source-pixel supports/weights before the identical mean reducer. This is the observed cause of the mask and value differences; it is not a 4096/2048 artifact.
- Final report: `D:\实验复现\Globaltreeline_artifacts\20260831-step2a-ab-gmba10067-v2\step2b_final_materialized_comparison.json`, SHA-256 `5888EBCBE0D4F28C55F0BA8DDB3321D44F6D741DE589829FE199528B36D13CBD`. The three independent cell checks remained within Float32 tolerance; maximum elevation error was `7.97688e-5 m`, and maximum rate error was `3.82484e-6 m/year`.

## 2026-08-31 materialized Step 2B recovery and A/B production split

- Live read-only diagnosis of `D:\实验复现\Globaltreeline_artifacts\20260828-step2-gmba-batch100-o005\tasks\20260829T002900Z-step2-gmba-batch100-o005.json` found 292 `COMPLETED` and 8 `FAILED` tasks. All failures were direct `treeline1km` OOM with completed sibling `treeline30m`: mountains 11106, 11153, 11158, 11199, 11205, 11259, 11284, and 11296. The other four required failure groups were zero.
- All 100 source `treeline30m` Assets existed and passed type, non-zero size, exact four-band order, shared 0.00025-degree aligned grid, mountain ID, run label, source configuration hash, Git commit, workflow, and child-name validation. Mountain 11158 had `treeline30m=COMPLETED` on attempt 1, `qa30m=COMPLETED` on attempt 1, and direct `treeline1km=FAILED` on attempt 5; the failed 1 km destination did not exist. Its GMBA area was 7,677.70824 km2, bounds area 33,574.27695 km2, ratio 4.37296, and required Step 1 tile count 1.
- The OOM root cause is the old direct 1 km export retaining the complete forest/temperature/DEM/edge/Otsu/local-test dependency graph. Added `code_step2b_treeline1km_from_30m.py`, which reads only the materialized four-band 30 m Asset, applies one four-band `reduceResolution(mean, bestEffort=False, maxPixels=2048)`, reprojects to the fixed 30 arc-second grid, and derives both 20-year rates. It never resumes the old direct graph.
- Step 2A now defaults to `treeline30m qa30m`; legacy direct `treeline1km` must be selected alone. The formal sequence is Step 2A, wait and validate 30 m, then a separately authorized Step 2B export. Monitoring never auto-starts the next phase.
- Final Step 2B source hash is `6d6ea51646f2a01127c91d7e785e3d94c6aa4a2f3cc77fd0e35a94c2c8e815c3`; implementation SHA-256 is `a1bac334acbaaecd1d4d69e34c0356cce3962be10842eb126d47bbe1ac2239b1`; aggregation configuration hash is `5c9e1fa8e7289c027e038ff3aa023b3491f3957cbd3682783a917187583c6f3c`. Read-only check serialized four 8,516-byte task expressions for 11158/10067/11293/11323, found zero new-target conflicts, and started no task.
- Direct-v1 versus virtual from-30m-v2 joint valid/mismatch grid counts were 64/65/1 for 10067, 380/383/19 for 11293, and 11,573/13,127/1,858 for 11323. Per-band mean differences (new minus direct) ranged from -2.0703 to +0.2470 m for elevation and -0.01125 to +0.03587 m/year for rates; maximum absolute differences reached 281.7537 m and 6.5283 m/year. The versions are therefore not pixel-wise equivalent and must not be silently mixed.
- Nine fixed-seed 30 arc-second cells were independently recomputed from source pixels with explicit pixel/cell overlap weights. All passed: maximum elevation error was `1.00296e-4 m`, and maximum rate error was `5.01627e-6 m/year`, within the fixed `1e-3 m` and `1e-4 m/year` tolerances. Ordinary unweighted region means were not used because the 33 1/3-to-1 grid ratio can bias boundary cells.
- Machine-readable reports are stored outside Git under `D:\实验复现\Globaltreeline_artifacts\20260831-step2b-materialized-1km\`: diagnosis `step2b-diagnosis-20260831.json` has SHA-256 `3E259C510972D205202E2174280D019A60BEC8C94AC1533313C1057DA62219F7`, and the final comparison `step2b-check-comparison-final-20260831.json` has SHA-256 `7794C73F4929E99BA2C81D80D6695FF8613F5FA994E06C5963D7E2CC57CE25AA`. No export, task start/cancel, Asset creation/deletion/overwrite, or Git push was performed.
- Final offline acceptance passed: all three maintained 2026824 Python entries compiled, `68` pytest tests and `21` subtests passed with one unrelated dependency deprecation warning, and `git diff --check` passed. Final audit also fixed the check path to reject any failed independent overlap result, rejected reuse of the source Step 2A run label, and added literal-grid and partial-overlap/masked-band regression contracts.

## 2026-08-31 Step 2 explicit-projection decision and formal integration

- Completed the single-variable projection A/B for GMBA 10011–10013. The v1 configuration hash was `c0bf6042dc0aa9eaa61bfa9a51075bb33bd92d3e851ef81b15dab807bdc2a36d`; the experimental explicit-projection hash was `6ac8b99afdb261d5f9ee754581f2b55f28d5809c15ee9da43c3da7d44bc2ae2d`.
- All nine v1/v2 Asset pairs had identical bands, PixelTypes, native output grids, and pyramiding policies, but all nine were pixel-wise non-equivalent. For mountains 10011/10012/10013, `treeline30m` mask differences were 4,710/1,661/698 pixels; `treeline1km` mask/value differences were 181/94, 56/25, and 27/13 pixels; maximum absolute 1 km differences were 366.1398/280.8879/76.4750 m. Otsu sample counts and thresholds changed in all three mountains.
- The first divergence occurs when post-landform candidates are aggregated to the CHELSA native grid for Otsu. The forest and candidate-edge exports were unchanged, but the mosaic's implicit default projection changed the support/weights used by `reduceResolution`; later Otsu, cold-zone, and local-test decisions are nonlinear, so the final export transform cannot repair them.
- Accepted the explicit-projection branch. The formal Step 2 now applies `setDefaultProjection(EPSG:4326, [0.00025,0,-180,0,-0.00025,90])` after mosaic/select and before pixel-neighborhood or cross-scale operations, without adding `reproject()` or changing the scientific graph elsewhere. The workflow and default run label are v2; old outputs cannot be resumed under the new hash.
- The machine-readable comparison is outside Git at `D:\实验复现\Globaltreeline_artifacts\20260830-step2-projection-ab-v2\comparison.json`, SHA-256 `B8F2F51DC45B0DAE02525B0E57AF7F9D7342A7A4F2D1F722AC445482F9100330`. No additional comparison Asset was created for formal integration.
- Formal offline acceptance passed under both the repository baseline Python 3.11.9 and the GEEmu Python 3.11.15 environment: each ran 46 pytest tests successfully with one unrelated dependency deprecation warning; both 2026824 entries compiled; `git diff --check` passed. Step 1 dry-run resolved 304 available tiles and 20 tasks for the selected 10-tile slice. Step 2 v2 dry-run resolved three default products, three tasks for one mountain, implementation SHA-256 `61d297fbcb388569f582a64d6d024b96de8b16c06a07f21e39b4e5548661990c`, and configuration hash `eae0a0ae1f206cf90601e7684f3ee42386d2d97b98d297d6b7652d38615eabc7`.
- The formal shallow `--check` used ADC/project `ee-wsc`, mountains 10011–10013, and check mountain 10012. It passed TABLE validation (3,115 selected/unique Basic features), Step 1 integrity (304 h3m + 304 h5m, configuration hash `33b97c487aa3117d2bc6fb988b0cb0490949bfcd26dbb3ba4355f5a658940542`), target-collection validation, and compact serialization of all three export DAGs (101,516/104,589/115,829 bytes). `exports_started=false`; no deep reducer or Asset write was run. Report: `D:\实验复现\Globaltreeline_artifacts\20260831-step2-explicit-projection-formal-check\step2_check_report.json`, SHA-256 `3AB87804F6C96665FCC16CBDA665AD6FFC39E5D1C0AB5739C107ECAC1BFA74B1`.
- A final read-only projection metadata probe confirmed that both h3m/h5m mosaics and all four 2000/2020 edge graphs resolve in Earth Engine to `EPSG:4326` with transform `[0.00025,0,-180,0,-0.00025,90]`. The probe queried projection metadata only; it did not read pixels, run reducers, or create tasks.

## 2026-08-29 Step 2 selective-product follow-up preparation

- The running 100-mountain registry recorded one failed task: `UARTBY7XOLZDS2E4CNPCXSU7`, mountain `11106`, product `treeline1km`, with the Earth Engine message `Execution failed; out of memory.` It was not retried. Monitoring is to continue until all 300 tasks reach terminal states, then report every recorded failure before any later batch starts.
- Added opt-in `--export-products` support. At that time the default remained `treeline30m treeline1km qa30m`; this historical default was superseded by the 2026-08-31 Step 2A/Step 2B split above.
- The authorized next selection is `mountain-offset=105`, `max-mountains=200`, exporting only `treeline30m` and `qa30m`. Offline dry-run resolved `ready=true`, no missing requirements, and 400 expected tasks. No tasks from this batch have been submitted yet.

## 2026-08-29 Step 2 pilot validation and 100-mountain batch

- The five-mountain pilot completed all 15 tasks. All destinations passed read-only validation for IMAGE type, non-zero `sizeBytes`, product-specific bands, `configuration_hash`, `run_label`, and `mountain_id` after aligning `expected_product_bands()` with the existing QA construction order (`hm_fraction`, `tree_fraction`, then `non_valley`). This correction did not alter the Step 2 image graph or existing Assets.
- The contract correction changed the source fingerprint and therefore the traceability-only Step 2 configuration hash to `6d6ea51646f2a01127c91d7e785e3d94c6aa4a2f3cc77fd0e35a94c2c8e815c3`; implementation SHA-256 is `6069b1a3cd7984ae21d43edfd9bec889a83d6fccb03792e14591df65da9347a1`.
- The authorized follow-up selection used `mountain-offset=5` and `max-mountains=100`, excluded all five pilot mountains, and resolved 100 unique mountains with three products each. Dry-run returned `ready=true`, no missing requirements, and 300 expected tasks. Preflight found no existing target Asset or matching remote description; current READY was 0 and projected READY was exactly the queue limit of 300.
- Submitted all 300 tasks without `--resume` or overwrite. Registry: `D:\实验复现\Globaltreeline_artifacts\20260828-step2-gmba-batch100-o005\tasks\20260829T002900Z-step2-gmba-batch100-o005.json`. It contains 300 unique task IDs, 300 unique destinations, 100 unique mountains, three products per mountain, and one configuration hash.
- Initial monitor at `2026-08-29T00:39:38Z` reported 4 COMPLETED, 4 RUNNING, and 292 READY, with no failed or cancelled task.

## 2026-08-24 two-stage forest/treeline architecture

- Added `gee/runs/2026824/code_step1_jrc_forest_tiles.py` and `code_step2_gmba_treeline.py`; the 2026821 v2 script remains historical/compatibility code.
- Step 1 now builds globally continuous GLAD binary forest graphs before any GMBA operation. It uses strict 3/5 m thresholds, preserves the source mask, runs two eight-neighbour counts with `maxSize=500`, fills non-forest components `<=5000 m²`, then retains forest components `>=5000 m²`.
- The method follows the JRC sequence and uses the public-code `maxSize=500`, but it does not reproduce the JRC forest land-use definition because the input and resolution differ.
- GMBA Basic only selects intersecting 10-degree tiles. The default -60 to 80 degree range is diagnosed rather than described as global; the check expands the manifest to -90 to 90 when target mountains or valid forest would be omitted.
- Step 2 only reads `Global_tree_3m` and `Global_tree_5m`, mosaics before median filtering/Zero Crossing, and then restricts candidates and 300 m local samples to each selected complete GMBA Basic geometry.
- Step 2 verifies paired tile IDs, both year bands, non-empty IMAGE Assets, max size 500, one Step 1 configuration hash, the fixed grid, and the tiles required by the selected mountains before creating export configurations.
- The analysis TABLE is `projects/ee-wsc/assets/Alpine/GMBA_Sayre`. It contains 3,115 unique Basic units selected by `hm_fraction >=0.50` and `tree_fraction <=0.90`; the latter uses ESA WorldCover 10 m 2021 `Map=10` tree cover.
- Read-only validation found no null or threshold-violating rows. The first geometry area matches full `gmba_area_km2`, not `hm_area_km2`, so the formal domain is the selected complete GMBA Basic geometry rather than a clipped Sayre intersection.
- Step 2 derives `gmba_id_text` and `gmba_sort_key` from `GMBA_V2_ID`, re-applies both fixed filters, and records `hm_fraction`/`tree_fraction` in QA and metadata. `sayre_high` is a mountain-level selection flag, not a pixelwise Sayre mask.
- The formerly referenced current-manifest Asset was not present in the `ee-wsc/Alpine` folder at check time. Its outside-range count is therefore recorded as unavailable rather than inferred from the full-GMBA TABLE. The conservative check expanded latitude coverage to -90..90.
- Initial `maxSize=50` read-only Step 1 check: 648 candidate global-latitude tiles, 304 GMBA-intersecting tiles, 608 expected exports, both target ImageCollections present and empty, two non-empty serialized sample task configurations, `exports_started=false`, configuration hash `f5f06aaedea56604d570bb3bc4219debb34929a8a69670612b4d03f4e948c9e0`. This configuration is superseded and must not be resumed.
- GEEMu environment verification passed for project `ee-wsc` with Python 3.11.15, earthengine-api 1.7.32 and geemap 0.37.2. Python compilation and all 41 offline tests passed. The default Step 2 dry-run is ready with configuration hash `06db2931ba0024a4c85ab2242b60245779ca30c5aa70a8c70707650ffedccaab`.

## 2026-08-24 Step 1 maxSize=500 mid-latitude pilot

- At the user's direction, both `connectedPixelCount` calls changed from `maxSize=50` to `maxSize=500`; task descriptions, workflow ID, metadata, Step 2 integrity checks, tests and method documentation changed to `ms500`/`mmu_max_size=500`.
- The two `ms50` tasks for tile `N40_E000` were cancelled before execution; neither target ImageCollection contained a child Asset.
- The new read-only check passed with `exports_started=false`, 304 valid tiles, two non-empty serialized task configurations and configuration hash `33b97c487aa3117d2bc6fb988b0cb0490949bfcd26dbb3ba4355f5a658940542`.
- The bounded dry-run selected only `N40_E000` (`0..10°E`, `40..50°N`) and expected two exports. The submitted `ms500` tasks are `GOKSPBE5OBKNZT4VTS2BCIPQ` (3 m) and `MVMAQ44BBUC4RWCORT6OFJ7S` (5 m); both were `READY` at the first monitor pass.
- Registry: `D:\实验复现\Globaltreeline_artifacts\20260824-step1-ms500-midlat-pilot\tasks\20260824T141110Z-step1-ms500-midlat-N40_E000.json`.

## Current maintained implementation (2026-08-24)

- Historical baseline entry point: `gee/runs/2026821/code_region_revised_v2.py`; superseded by the two 2026824 entries above.
- Regression suites include the historical test plus both 2026824 stage contracts.
- Current method and run documentation: `gee/runs/2026821/METHOD_GMBA_REVISED.md` and `gee/runs/2026821/RUN_REGION.md`.
- Earlier tracer, JavaScript, region-union and 2-degree-shard source files were removed from the working tree after their history had been preserved in Git.
- Generated task registries, console reports, HTML maps and the validation package were moved outside the source repository to `D:\实验复现\Globaltreeline_artifacts\2026821`.
- No Earth Engine task or Asset was changed during this repository cleanup.

## 2026-08-24 fixed JRC-style binary MMU

- Updated the sole entry point to workflow identifier `per-gmba-v4-jrc-mmu` and default run label `mountain_v4_jrc_mmu`.
- Fixed the binary post-processing order to: GLAD canopy-height threshold, eight-neighbour forest components, remove forest area `<=0.5 ha`, eight-neighbour non-forest components, fill internal non-forest gaps `<0.5 ha`, median filter, then Zero Crossing.
- Both forest and non-forest component areas are the sum of `ee.Image.pixelArea()` within each connected object. The value `512` remains only as the connected-component computation protection limit and is not a scientific area or hole-size threshold.
- Removed the four-neighbour hole test, vector `buffer(-90) + difference` border ring, pixel-count area approximation, configurable MMU CLI options, and the export-time hole-assumption acknowledgement.
- Added QA bands `forest_small_patch_removed_*` and `nonforest_small_gap_filled_*`.
- This change aligns only the binary MMU post-processing with JRC GFC2020 v2. The study still uses approximately 30 m GLAD canopy height with `>3 m` primary and `>5 m` sensitivity thresholds; it does not reproduce the complete JRC 10 m forest land-use definition.
- With the production Asset arguments and a 10-mountain dry-run, `ready=true`, `missing_requirements=[]`, `expected_task_count=30`, and configuration hash `aa634a00da7502f6e966950c1d14c2c699e480a9b87c81d8eac9d37601efe777`.
- Python compilation and all 19 maintained offline regression tests passed. Earth Engine initialization for project `ee-wsc` passed with Python 3.11.9, earthengine-api 1.7.32, and geemap 0.37.2. No new Earth Engine check, export, preparation task, or Asset write was started.
- Boundary/compute risk remains high for very large GMBA geometries because connected components are not generally tile-safe; staged online validation and a <=10-mountain pilot remain required before expansion.
- User-supplied reference copies were preserved outside the source repository under `D:\实验复现\Globaltreeline_artifacts\20260824-jrc-mmu-reference` after their hashes were recorded.
- JRC method source: https://doi.org/10.5194/essd-18-1331-2026; public source code: https://figshare.com/articles/code/Joint_Research_Centre_-_Global_Forest_Cover_for_year_2020_version_2_Code_source/29315528.

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
