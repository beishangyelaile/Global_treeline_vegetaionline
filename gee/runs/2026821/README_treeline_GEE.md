# Globaltreeline GEE 入口索引

当前工作流不再由单个脚本直接从冠层高度生成树线。

```text
code_step1_jrc_forest_tiles.py
  └─ Global_tree_3m / Global_tree_5m
       └─ code_step2_gmba_treeline.py
            ├─ Treeline_30m_Collection
            ├─ Treeline_1km_Collection
            └─ Treeline_QA30m_Collection
```

- 正式代码：`gee/runs/2026824/`
- 方法：`gee/runs/2026821/METHOD_GMBA_REVISED.md`
- 命令：`gee/runs/2026821/RUN_REGION.md`
- 数据契约：`gee/runs/2026821/DATA_LAYER_REGION.md`
- 决策历史：`docs/research/method_decisions.md`
- 当前计划：`docs/exec-plans/active/task-001-2026-08-24.md`

Step 1 必须先完成并验收，Step 2 才可正式在线检查或导出。Step 2 固定读取 `projects/ee-wsc/assets/Alpine/GMBA_Sayre`：只保留 `hm_fraction >=0.50`、WorldCover 2021 Class 10 `tree_fraction <=0.90` 的山体，几何为入选山体的完整 GMBA Basic。

旧 `code_region_revised_v2.py` 仅用于历史追溯和兼容回归。完整命令及安全门禁请以 `RUN_REGION.md` 为准。
