# 逐 GMBA 山体的树线提取：运行手册

唯一受支持的入口是 `code_region_revised_v2.py`。不要把 Git 历史中的旧版区域合并、2° 分片或 tracer 结果与本版结果混合。

以下命令均从仓库根目录运行。Cloud Project 固定为 `ee-wsc`。

## 1. 分析单位与流程

- 准备阶段将 GMBA v2 `MapUnit=Basic` 与已审核的 GMBA/Sayre 清单连接，验证 978 个唯一 GMBA ID 和 8 个区域后，导出一个 TABLE Asset。
- TABLE 保留原始、互不重叠的 GMBA Basic 几何。Sayre 31/32 只用于确定进入清单的山体；选中后，完整 GMBA Basic 几何就是正式处理域。
- 每个 GMBA 山体独立构图与导出，不使用任意 2° 空间分片。
- 默认同时输出冠层高 `>3 m` 主分析与 `>5 m` 敏感性分析。
- 二值 MMU 固定为：八邻域删除面积 `<=0.5 ha` 的森林斑块，再以八邻域填充面积 `<0.5 ha` 的内部非森林间隙；两类面积均由连通对象内 `ee.Image.pixelArea()` 求和。
- MMU 后依次执行中值滤波和 Zero Crossing；本规则只对齐 JRC GFC2020 v2 的二值后处理，不改变当前 GLAD 冠层高度森林定义。
- 一个山体默认产生三项任务：30 m 树线、1 km 汇总、30 m QA。978 个山体共 2,934 项任务，因此必须分批提交。

## 2. 离线检查

```powershell
python -m pytest -q

python .\gee\runs\2026821\code_region_revised_v2.py `
  --dry-run `
  --project ee-wsc `
  --prepared-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Basic_Sayre_selected_v3 `
  --chelsa-bio01 projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21 `
  --treeline30m-collection projects/ee-alpine-506212/assets/Treeline_30m_Collection `
  --treeline1km-collection projects/ee-alpine-506212/assets/Treeline_1km_Collection `
  --qa30m-collection projects/ee-alpine-506212/assets/Treeline_QA30m_Collection `
  --max-mountains 10 `
  --mountain-offset 0
```

`--dry-run` 不访问 Earth Engine，也不提交任务。确认输出中 `ready=true`、`missing_requirements=[]`、选择范围和 `expected_task_count` 正确后再进入在线阶段。

## 3. 准备 GMBA/Sayre 山体 Asset

仅在目标 TABLE 尚未准备时执行：

```powershell
python .\gee\runs\2026821\code_region_revised_v2.py `
  --prepare-mountains `
  --project ee-wsc `
  --gmba-asset projects/ee-remote/assets/Alpine/GMBA_v2 `
  --manifest-asset projects/ee-wsc/assets/Alpine/GMBA_8regions_Sayre31_32_manifest `
  --prepared-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Basic_Sayre_selected_v3
```

准备任务完成后再执行检查或导出。若目的 TABLE 已存在，默认拒绝覆盖。

## 4. 检查一个代表性山体

```powershell
python .\gee\runs\2026821\code_region_revised_v2.py `
  --check `
  --project ee-wsc `
  --prepared-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Basic_Sayre_selected_v3 `
  --chelsa-bio01 projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21 `
  --treeline30m-collection projects/ee-alpine-506212/assets/Treeline_30m_Collection `
  --treeline1km-collection projects/ee-alpine-506212/assets/Treeline_1km_Collection `
  --qa30m-collection projects/ee-alpine-506212/assets/Treeline_QA30m_Collection `
  --max-mountains 1 `
  --mountain-offset 0 `
  --check-strategy median `
  --deep-check `
  --write-map
```

`--check` 不提交任务。默认浅检查只验证山体计划、计算图和 Export 配置；`--deep-check` 会计算 Otsu 字典，建议只对代表性的小、中、大山体分别运行。

## 5. 分批导出

先以 10 个山体进行试运行：

```powershell
python .\gee\runs\2026821\code_region_revised_v2.py `
  --export `
  --project ee-wsc `
  --prepared-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Basic_Sayre_selected_v3 `
  --chelsa-bio01 projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21 `
  --treeline30m-collection projects/ee-alpine-506212/assets/Treeline_30m_Collection `
  --treeline1km-collection projects/ee-alpine-506212/assets/Treeline_1km_Collection `
  --qa30m-collection projects/ee-alpine-506212/assets/Treeline_QA30m_Collection `
  --max-mountains 10 `
  --mountain-offset 0 `
  --run-label gmba_v4_jrc_mmu
```

后续批次保持所有科学参数与 `--run-label` 不变，只调整 `--mountain-offset` 和 `--max-mountains`。中断后使用完全相同的参数加 `--resume`；只有配置哈希一致的既有 Asset 才会被跳过。配置标识固定为 `per-gmba-v4-jrc-mmu`，旧版任务不能通过 `--resume` 混入本版。

脚本默认拒绝一次提交超过 100 个山体，并在预检前、提交前分别验证 `现有 READY + 新任务 <= 2900`。不要把 `--allow-large-batch-submit` 或 `--overwrite-assets` 用作常规操作。

## 6. Otsu 与敏感性配置

默认 `--otsu-scope mountain-pooled`：对每个山体、每个冠层阈值，将 2000/2020 年经过 landform 排除后的边缘候选合并，并在 CHELSA 原生格网中每格只计一次；随后将同一山体阈值用于两个年份。样本少于 `--otsu-min-samples 20` 或直方图退化时，输出 `otsu_valid=0`，不会静默采用全局阈值。

若阈值已通过独立脚本审核，可用固定山体阈值：

```json
{
  "thresholds_c": {
    "10011": {"h3m": 1.2, "h5m": 0.8}
  }
}
```

并传入 `--otsu-scope mountain-fixed --mountain-thresholds-json thresholds.json`。

Canny 只应作为独立敏感性运行，使用新的 run label：

```text
--edge-method canny --canny-threshold 0.1 --canny-sigma 1 --run-label gmba_canny_v4_jrc_mmu
```

## 7. 当前需要显式承认的假设

- 0.5 ha 与八邻域已固定为 JRC GFC2020 v2 式二值 MMU 后处理，不提供命令行调整，也不再要求接受旧的填孔假设。
- 完整 GMBA 域可能包含坡麓、局部低地和非高山森林边缘。它们由 landform、山体级温度 Otsu 和局地高程检验继续筛除；这与论文的 Sayre/0.25° 前置像元筛选不同，必须作为研究域变更报告。
- GEE `connectedComponents(maxSize)` 的 `maxSize=512` 是连通对象计算保护上限，不是科学阈值。默认 16 km 上下文缓冲用于使正式 GMBA 域远离该计算边界；森林与非森林对象面积均通过对象内 `pixelArea()` 求和；QA 分别记录 `forest_small_patch_removed_*` 和 `nonforest_small_gap_filled_*`。
- 这里只对齐 JRC 的二值 MMU 后处理；当前森林输入仍为 GLAD 约 30 m 冠层高度、`>3 m` 主结果和 `>5 m` 敏感性结果，不应称为完整复现 JRC 10 m 森林土地利用产品。
- 默认 300 m 窗口实现为中心像元外扩 150 m 的方形核；森林高程显著低于非森林高程时，以单侧 0.05 Welch t 检验保留。论文未说明等方差、单双侧和最小样本数，因此这些参数均写入配置与元数据。
- 极大 GMBA 若单山体任务超时，可再增加“山体内部计算分片”；必须先冻结该山体的 Otsu 阈值，再切分栅格，不能按内部片重算阈值。

## 8. 监控

每次准备或导出都会在 `gee/runs/2026821/outputs/tasks/` 写任务登记 JSON。该目录是运行状态，不属于源码：

```powershell
python .\gee\runs\2026821\code_region_revised_v2.py `
  --monitor-once .\gee\runs\2026821\outputs\tasks\<registry>.json `
  --project ee-wsc
```

## 9. 2026-08-24 本机验证基线

- Python：`D:\LenovoSoftstore\Install\Python3.11\python.exe`，版本 3.11.9。
- `earthengine-api==1.7.32`、`geemap==0.37.2`、`geopandas==1.0.1` 导入通过。
- Earth Engine 常规凭据文件未发现，但项目 `ee-wsc` 的在线初始化通过；本次没有启动检查、准备或导出任务。
- 大型 GMBA 的边界/计算风险为高：连通域操作不能任意分片，正式运行仍须先检查代表性山体并进行不超过 10 个山体的 pilot。
