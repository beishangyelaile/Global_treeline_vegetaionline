# 三阶段树线工作流运行手册

所有命令从仓库根目录运行，Cloud Project 固定为 `ee-wsc`。`<...>` 是必须由研究者填写的值，不能直接复制执行。

## 1. 离线门禁

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -q -p no:cacheprovider
python -m py_compile .\gee\runs\2026824\code_step1_jrc_forest_tiles.py .\gee\runs\2026824\code_step2_gmba_treeline.py .\gee\runs\2026824\code_step2b_treeline1km_from_30m.py
```

## 2. Step 1 只读 check

以下命令访问 GEE 并序列化计算图，但不会启动任务。它筛选 GMBA Basic 相交的 10°瓦片，并诊断 `-60°..80°` 以外目标；需要时自动扩展清单到 `-90°..90°`。

```powershell
python .\gee\runs\2026824\code_step1_jrc_forest_tiles.py `
  --check `
  --project ee-wsc `
  --gmba-asset projects/ee-remote/assets/Alpine/GMBA_v2 `
  --current-manifest-asset projects/ee-wsc/assets/Alpine/GMBA_8regions_Sayre31_32_manifest `
  --tree3m-collection projects/ee-alpine-506212/assets/Global_tree_3m `
  --tree5m-collection projects/ee-alpine-506212/assets/Global_tree_5m `
  --write-tile-manifest D:\实验复现\Globaltreeline_artifacts\<run>\step1_tile_manifest.json `
  --report-json D:\实验复现\Globaltreeline_artifacts\<run>\step1_check_report.json
```

验收：`status=step1-read-only-graph-preflight-passed`、`exports_started=false`、两个目标类型正确、四个计算图的任务配置非空；记录有效瓦片数、纬度诊断、是否自动扩展以及 `configuration_hash`。

## 3. Step 1 离线 dry-run

```powershell
python .\gee\runs\2026824\code_step1_jrc_forest_tiles.py `
  --dry-run `
  --project ee-wsc `
  --tile-manifest D:\实验复现\Globaltreeline_artifacts\<run>\step1_tile_manifest.json `
  --max-tiles 10 `
  --tile-offset 0
```

确认 `ready=true`、`missing_requirements=[]`、选中瓦片 ID 正确，任务数为 `selected_tile_count × 2`。

## 4. Step 1 导出（本次任务未授权执行）

只有研究者明确确认范围、任务数和成本后才可执行：

```powershell
python .\gee\runs\2026824\code_step1_jrc_forest_tiles.py `
  --export `
  --project ee-wsc `
  --tile-manifest D:\实验复现\Globaltreeline_artifacts\<run>\step1_tile_manifest.json `
  --max-tiles <N> `
  --tile-offset <OFFSET> `
  --queue-safety-limit 100 `
  --registry-dir D:\实验复现\Globaltreeline_artifacts\<run>\tasks
```

断点恢复只在参数和哈希完全一致时增加 `--resume`。脚本没有覆盖 Asset 的常规选项。

监控一次：

```powershell
python .\gee\runs\2026824\code_step1_jrc_forest_tiles.py `
  --monitor-once D:\实验复现\Globaltreeline_artifacts\<run>\tasks\<registry>.json `
  --project ee-wsc
```

Step 1 全部完成后，确认两个集合瓦片 ID 一致、每个 Asset 非空、波段为 `tree_2000/tree_2020`、元数据/哈希/格网一致。

## 5. Step 2A dry-run

分析 TABLE 固定为 `projects/ee-wsc/assets/Alpine/GMBA_Sayre`。其几何是经过 `hm_fraction >=0.50`、`tree_fraction <=0.90` 筛选后的完整 GMBA Basic。Step 2A 默认产品为 `treeline30m qa30m`：

```powershell
python .\gee\runs\2026824\code_step2_gmba_treeline.py `
  --dry-run `
  --project ee-wsc `
  --analysis-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Sayre `
  --step1-manifest D:\实验复现\Globaltreeline_artifacts\<run>\step1_tile_manifest.json `
  --max-mountains 1 `
  --mountain-offset 0
```

确认 `products=[treeline30m,qa30m]`、任务数为山体数乘 2。旧 direct `treeline1km` 只能用 `--export-products treeline1km` 单独显式选择作对照；与其他产品同时选择会在联网前拒绝。

## 6. Step 2A 完整性与单山体 check

该命令读取两个森林集合和正式分析 TABLE，核对完整性并序列化 30 m 与 QA 两个输出图，不启动任务：

```powershell
python .\gee\runs\2026824\code_step2_gmba_treeline.py `
  --check `
  --project ee-wsc `
  --analysis-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Sayre `
  --step1-manifest D:\实验复现\Globaltreeline_artifacts\<run>\step1_tile_manifest.json `
  --max-mountains 1 `
  --check-mountain-id <GMBA_ID> `
  --deep-check `
  --report-json D:\实验复现\Globaltreeline_artifacts\<run>\step2a_check_report.json
```

验收：分析 TABLE 为 3,115 个唯一 Basic 山体、无阈值违规；`status=step2-integrity-and-graph-preflight-passed`、`exports_started=false`、`step1_integrity.ready=true`、两个任务配置非空、Otsu `evaluated` 且有效。

## 7. Step 2A 有界导出（需明确授权）

```powershell
python .\gee\runs\2026824\code_step2_gmba_treeline.py `
  --export `
  --project ee-wsc `
  --analysis-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Sayre `
  --step1-manifest D:\实验复现\Globaltreeline_artifacts\<run>\step1_tile_manifest.json `
  --max-mountains <N> `
  --mountain-offset <OFFSET> `
  --run-label <NEW_STEP2A_RUN_LABEL> `
  --queue-safety-limit 100 `
  --registry-dir D:\实验复现\Globaltreeline_artifacts\<run>\tasks
```

每个山体默认产生 2 个任务。相同配置中断后可加 `--resume`；不同哈希必须使用新 Asset 名和 run label。

## 8. 等待并验收 30 m

Step 2A 监控只更新 Step 2A registry，不自动启动 Step 2B。只有对应 `treeline30m` 任务为 `COMPLETED`，且 Asset 存在、非空、四个波段/格网/provenance 完整时，山体才可进入 Step 2B。

## 9. Step 2B 原批次只读诊断

```powershell
python .\gee\runs\2026824\code_step2b_treeline1km_from_30m.py `
  --diagnose `
  --project ee-wsc `
  --source-registry D:\实验复现\Globaltreeline_artifacts\<run>\tasks\<step2a-registry>.json `
  --report-json D:\实验复现\Globaltreeline_artifacts\<run>\step2b_diagnosis.json
```

诊断刷新所有原任务状态，并在新报告中记录目标/源存在性、源完整性、GMBA/外包矩形面积、面积比、Step 1 瓦片数、恢复资格和五类失败统计；不得覆盖原 registry。

## 10. Step 2B dry-run 与只读 check

```powershell
python .\gee\runs\2026824\code_step2b_treeline1km_from_30m.py `
  --dry-run `
  --source-registry D:\实验复现\Globaltreeline_artifacts\<run>\tasks\<step2a-registry>.json `
  --failed-only `
  --max-mountains <N>

python .\gee\runs\2026824\code_step2b_treeline1km_from_30m.py `
  --check `
  --project ee-wsc `
  --source-registry D:\实验复现\Globaltreeline_artifacts\<run>\tasks\<step2a-registry>.json `
  --mountain-ids <GMBA_ID> [<GMBA_ID> ...] `
  --report-json D:\实验复现\Globaltreeline_artifacts\<run>\step2b_check.json
```

`--check` 验收源 30 m、目标冲突并序列化任务图，不启动任务。显式选择中若包含旧 direct 已完成山体，最多比较 3 个山体，并各抽取 3 个 30 arc-second 格执行细像元相交面积加权核验。

## 11. Step 2B 有界导出与监控（需明确授权）

```powershell
python .\gee\runs\2026824\code_step2b_treeline1km_from_30m.py `
  --export `
  --project ee-wsc `
  --source-registry D:\实验复现\Globaltreeline_artifacts\<run>\tasks\<step2a-registry>.json `
  --failed-only `
  --max-mountains <N> `
  --run-label <NEW_STEP2B_RUN_LABEL> `
  --queue-safety-limit 100 `
  --registry-dir D:\实验复现\Globaltreeline_artifacts\<run>\tasks

python .\gee\runs\2026824\code_step2b_treeline1km_from_30m.py `
  --monitor-once D:\实验复现\Globaltreeline_artifacts\<run>\tasks\<step2b-registry>.json
```

`--failed-only` 仅选择旧 direct 1 km OOM 且 30 m 已完成/有效的山体；也可显式使用 `--mountain-ids` 或 `--all-eligible`。新目标同哈希只有 `--resume` 可跳过，异哈希一律拒绝。普通 Step 2 `--resume` 不得用于恢复旧 OOM 图。

## 12. Step 2A 显式投影版本

2026-08-31 起，正式 Step 2A 在两个森林集合完成 `mosaic` 和双年份波段选择后，显式声明 Step 1 的 0.00025°默认投影，再进入中值、Zero Crossing 和 CHELSA 聚合。默认 `--run-label` 为 `gmba_sayre_step2_v2`。

该科学计算图与 v1 及投影 A/B 实验均有不同的实现指纹和配置哈希。不得对旧结果使用 `--resume`，也不得覆盖旧 child Asset；未来正式导出须先执行当前源码的 dry-run/只读 check，并使用新 run label 和新目标名称。
