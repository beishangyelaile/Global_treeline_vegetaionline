# Globaltreeline GEE 入口索引

当前工作流不再由单个脚本直接从冠层高度生成树线。

```text
code_step1_jrc_forest_tiles.py
  └─ Global_tree_3m / Global_tree_5m
       └─ code_step2_gmba_treeline.py (Step 2A)
            ├─ Treeline_30m_Collection
            │    └─ code_step2b_treeline1km_from_30m.py (Step 2B)
            │         └─ Treeline_1km_Collection
            └─ Treeline_QA30m_Collection
```

- 正式代码：`gee/runs/2026824/`
- 方法：`gee/runs/2026821/METHOD_GMBA_REVISED.md`
- 命令：`gee/runs/2026821/RUN_REGION.md`
- 数据契约：`gee/runs/2026821/DATA_LAYER_REGION.md`
- 决策历史：`docs/research/method_decisions.md`
- 已完成执行记录：`docs/exec-plans/completed/2026-08-30-two-stage-treeline-explicit-projection.md`

Step 1 必须先完成并验收，Step 2A 才可正式在线检查或导出。Step 2A 固定读取 `projects/ee-wsc/assets/Alpine/GMBA_Sayre`：只保留 `hm_fraction >=0.50`、WorldCover 2021 Class 10 `tree_fraction <=0.90` 的山体，几何为入选山体的完整 GMBA Basic。Step 2A v2 在森林集合 mosaic/select 后显式声明 Step 1 的 0.00025°默认投影，再执行邻域和 CHELSA 聚合；默认只导出 30 m 与 QA。

对应 30 m Asset 完成并通过类型、非空、四带、格网和 provenance 门禁后，Step 2B 才能单独生成 1 km v2。旧 direct 1 km 只保留为单独显式对照，不得与 Step 2A 同批，也不得用旧 run label、配置哈希或普通 `--resume` 恢复新结果。

旧 `code_region_revised_v2.py` 仅用于历史追溯和兼容回归。完整命令及安全门禁请以 `RUN_REGION.md` 为准。
