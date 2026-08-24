# 两阶段树线工作流运行手册

所有命令从仓库根目录运行，Cloud Project 固定为 `ee-wsc`。`<...>` 是必须由研究者填写的值，不能直接复制执行。

## 1. 离线门禁

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -q -p no:cacheprovider
python -m py_compile .\gee\runs\2026824\code_step1_jrc_forest_tiles.py .\gee\runs\2026824\code_step2_gmba_treeline.py
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

## 5. Step 2 dry-run

分析 TABLE 已固定为 `projects/ee-wsc/assets/Alpine/GMBA_Sayre`。其几何是经过 `hm_fraction >=0.50`、`tree_fraction <=0.90` 筛选后的完整 GMBA Basic：

```powershell
python .\gee\runs\2026824\code_step2_gmba_treeline.py `
  --dry-run `
  --project ee-wsc `
  --analysis-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Sayre `
  --step1-manifest D:\实验复现\Globaltreeline_artifacts\<run>\step1_tile_manifest.json `
  --max-mountains 1 `
  --mountain-offset 0
```

## 6. Step 2 完整性与单山体 check

该命令会读取两个森林集合和正式分析 TABLE，核对完整性并序列化三个输出图，不会启动任务：

```powershell
python .\gee\runs\2026824\code_step2_gmba_treeline.py `
  --check `
  --project ee-wsc `
  --analysis-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Sayre `
  --step1-manifest D:\实验复现\Globaltreeline_artifacts\<run>\step1_tile_manifest.json `
  --max-mountains 1 `
  --check-mountain-id <GMBA_ID> `
  --deep-check `
  --report-json D:\实验复现\Globaltreeline_artifacts\<run>\step2_check_report.json
```

验收：分析 TABLE 为 3,115 个唯一 Basic 山体、无阈值违规；`status=step2-integrity-and-graph-preflight-passed`、`exports_started=false`、`step1_integrity.ready=true`、三个任务配置非空、Otsu `evaluated` 且有效。

## 7. Step 2 pilot（本次任务未授权执行）

只在 Step 1 全量验收、Step 2 check 通过并获得明确授权后运行不超过 10 山体：

```powershell
python .\gee\runs\2026824\code_step2_gmba_treeline.py `
  --export `
  --project ee-wsc `
  --analysis-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Sayre `
  --step1-manifest D:\实验复现\Globaltreeline_artifacts\<run>\step1_tile_manifest.json `
  --max-mountains 10 `
  --mountain-offset 0 `
  --run-label <NEW_RUN_LABEL> `
  --queue-safety-limit 100 `
  --registry-dir D:\实验复现\Globaltreeline_artifacts\<run>\tasks
```

每个山体产生 3 个任务。相同配置中断后可加 `--resume`；不同哈希必须使用新 Asset 名和 run label。
