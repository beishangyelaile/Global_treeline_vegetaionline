# Global treeline reproduction

本仓库复现并审慎改造 Liang et al. (2026) 的全球高山树线提取流程（DOI `10.1016/j.jag.2026.105088`）。

## 当前三阶段入口

```text
Step 1  gee/runs/2026824/code_step1_jrc_forest_tiles.py
        GLAD 2000/2020 → 连续二值森林 → 10°瓦片 ImageCollection

Step 2A gee/runs/2026824/code_step2_gmba_treeline.py
        Step 1瓦片 mosaic → Zero Crossing → 筛选后逐GMBA 30 m树线与QA

Step 2B gee/runs/2026824/code_step2b_treeline1km_from_30m.py
        已验收treeline30m → 单次均值聚合 → 30 arc-second treeline1km
```

旧 [`code_region_revised_v2.py`](gee/runs/2026821/code_region_revised_v2.py) 仅作历史兼容，不再是当前完整工作流入口。

Step 1 固定使用严格 `>3 m` 主阈值和 `>5 m` 敏感性阈值。MMU 采用 JRC GFC2020 v2 的处理顺序、八邻域、0.5 ha 规则和 `maxSize=500`；当前输入仍是约 30 m GLAD 冠层高度，而不是 JRC 10 m 森林土地利用产品。因此不得称为森林定义或产品层面的完整 JRC 复现。

Step 2A 默认读取 `projects/ee-wsc/assets/Alpine/GMBA_Sayre`。该 TABLE 仅保留 `hm_fraction >=0.50` 且 `tree_fraction <=0.90` 的 GMBA Basic；树木覆盖率来自 ESA WorldCover 10 m 2021（`ESA/WorldCover/v200`，`Map=10`）。Asset 几何仍是入选山体的完整 GMBA Basic 范围，不是 Sayre 像元交集。Step 2A 默认只提交 `treeline30m + qa30m`；待 30 m 完成并验收后，Step 2B 才单独生成 1 km。三个默认输出集合均使用 `_v2` 后缀。

## 环境与离线测试

仓库依赖固定在 `requirements.txt` 和 `requirements-dev.txt`，CI 使用 Python 3.11.9 且不连接 Earth Engine：

```powershell
python -m pip install --disable-pip-version-check -r requirements-dev.txt
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -q -p no:cacheprovider
python -m py_compile .\gee\runs\2026824\code_step1_jrc_forest_tiles.py .\gee\runs\2026824\code_step2_gmba_treeline.py .\gee\runs\2026824\code_step2b_treeline1km_from_30m.py
```

## 安全预览

Step 1 dry-run 需要由只读 check 生成的瓦片清单：

```powershell
python .\gee\runs\2026824\code_step1_jrc_forest_tiles.py `
  --dry-run `
  --project ee-wsc `
  --tile-manifest D:\实验复现\Globaltreeline_artifacts\<run>\step1_tile_manifest.json `
  --max-tiles 10 `
  --tile-offset 0
```

Step 2A dry-run 使用固定分析 TABLE，并传入 Step 1 清单：

```powershell
python .\gee\runs\2026824\code_step2_gmba_treeline.py `
  --dry-run `
  --project ee-wsc `
  --analysis-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Sayre `
  --step1-manifest D:\实验复现\Globaltreeline_artifacts\<run>\step1_tile_manifest.json `
  --max-mountains 10 `
  --mountain-offset 0
```

Step 2B dry-run 只解析原 Step 2 registry，不联网：

```powershell
python .\gee\runs\2026824\code_step2b_treeline1km_from_30m.py `
  --dry-run `
  --source-registry D:\实验复现\Globaltreeline_artifacts\<run>\tasks\<step2-registry>.json `
  --failed-only `
  --max-mountains 10
```

`--dry-run` 不联网。`--diagnose`/`--check` 只读并序列化计算图或生成本地报告，不启动任务。只有显式 `--export` 能到达 `task.start()`；所有导出必须先通过来源、清单、目标集合、已有任务、哈希和队列保护。旧 direct 1 km 通常只能单独显式对照；仅 `--allow-direct-1km-ab` 可在恰好一个山体上同步提交三产品，用于受控 A/B。

详细方法见 [`METHOD_GMBA_REVISED.md`](gee/runs/2026821/METHOD_GMBA_REVISED.md)，运行命令见 [`RUN_REGION.md`](gee/runs/2026821/RUN_REGION.md)，数据层契约见 [`DATA_LAYER_REGION.md`](gee/runs/2026821/DATA_LAYER_REGION.md)。维护规范见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
