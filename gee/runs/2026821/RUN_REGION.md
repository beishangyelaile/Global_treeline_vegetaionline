# 逐 GMBA 山体的树线提取：运行手册

正式入口是 `code_region_revised.py`。旧的 `code_region.py` 及其 2° 分片结果只用于历史追溯，不应与本版结果混合。

## 1. 分析单位与流程

- 准备阶段将 GMBA v2 `MapUnit=Basic` 与已审核的 GMBA/Sayre 清单连接，验证 978 个唯一 GMBA ID 和 8 个区域后，导出一个 TABLE Asset。
- TABLE 保留原始、互不重叠的 GMBA Basic 几何。Sayre 31/32 只用于确定哪些山体进入已审核清单；选中后，完整 GMBA Basic 几何就是正式处理域，不再叠加 Sayre 像元掩膜或 0.25°格网筛选。
- 每个 GMBA 山体独立构图与导出，不再使用任意 2° 空间分片。
- 默认同时输出冠层高 `>3 m` 主分析与 `>5 m` 敏感性分析。
- 一个山体默认产生三项任务：30 m 树线、1 km 汇总、30 m QA。978 个山体共 2,934 项任务，因此必须分批提交。

## 2. 准备 GMBA/Sayre 山体 Asset

```bash
python gee/runs/2026821/code_region_revised.py \
  --prepare-mountains \
  --project ee-wsc \
  --gmba-asset projects/ee-remote/assets/Alpine/GMBA_v2 \
  --manifest-asset projects/ee-wsc/assets/Alpine/GMBA_8regions_Sayre31_32_manifest \
  --prepared-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Basic_Sayre_selected_v3
```

准备任务完成后再执行检查或导出。若目的 TABLE 已存在，默认拒绝覆盖。

## 3. 先做一个山体的检查

```bash
python gee/runs/2026821/code_region_revised.py \
  --check \
  --project ee-wsc \
  --prepared-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Basic_Sayre_selected_v3 \
  --chelsa-bio01 projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21 \
  --treeline30m-collection projects/ee-alpine-506212/assets/Treeline_30m_Collection \
  --treeline1km-collection projects/ee-alpine-506212/assets/Treeline_1km_Collection \
  --qa30m-collection projects/ee-alpine-506212/assets/Treeline_QA30m_Collection \
  --max-mountains 1 \
  --mountain-offset 0 \
  --deep-check \
  --write-map
```

`--check` 不提交任务。默认浅检查只验证山体计划、图和 Export 配置；`--deep-check` 会真正计算 Otsu 字典，建议只对代表性的小、中、大山体分别运行。

## 4. 分批导出

先以 10 个山体进行试运行：

```bash
python gee/runs/2026821/code_region_revised.py \
  --export \
  --project ee-wsc \
  --prepared-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Basic_Sayre_selected_v3 \
  --chelsa-bio01 projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21 \
  --treeline30m-collection projects/ee-alpine-506212/assets/Treeline_30m_Collection \
  --treeline1km-collection projects/ee-alpine-506212/assets/Treeline_1km_Collection \
  --qa30m-collection projects/ee-alpine-506212/assets/Treeline_QA30m_Collection \
  --max-mountains 10 \
  --mountain-offset 0 \
  --accept-hole-filling-assumption \
  --run-label gmba_v3
```

后续批次保持所有科学参数与 `--run-label` 不变，只把 offset 改为 10、20、30……。中断后使用相同参数加 `--resume`；只有配置哈希一致的既有 Asset 才会被跳过。

脚本默认拒绝一次提交超过 100 个山体，并在预检前、提交前分别验证 `现有 READY + 新任务 <= 2900`。`--allow-large-batch-submit` 只解除前一限制，不解除队列保护。

## 5. Otsu 与敏感性配置

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

```bash
--edge-method canny --canny-threshold 0.1 --canny-sigma 1 --run-label gmba_canny_v3
```

## 6. 当前需要显式承认的假设

- 文献未报告 hole-filling 的最大孔洞尺度、连通性和中值核；默认 `hole-max-dimension-pixels=512` 是实现假设，不是已知论文参数。正式导出必须提供 `--accept-hole-filling-assumption`。
- 完整 GMBA 域可能包含坡麓、局部低地和非高山森林边缘。它们将由 landform、山体级温度 Otsu 和局地高程检验继续筛除；这与论文的 Sayre/0.25°前置像元筛选不同，必须作为研究域变更报告。
- GEE `connectedComponents(maxSize)` 的 `maxSize` 是连通对象的最大宽/高，不是像元面积。0.5 ha 小斑块去除则使用连通像元数乘 `pixelArea()` 判断真实面积。
- 默认 300 m 窗口被实现为中心像元外扩 150 m 的方形核；森林高程显著低于非森林高程时，以单侧 0.05 Welch t 检验保留。论文未说明等方差、单双侧和最小样本数，因此这些都写入配置与元数据。
- 极大 GMBA 若单山体任务超时，可再增加“山体内部计算分片”；但必须先冻结该山体的 Otsu 阈值，再切分栅格，不能按内部片重算阈值。

## 7. 监控

每次准备或导出都会在 `outputs/tasks/` 写任务登记 JSON：

```bash
python gee/runs/2026821/code_region_revised.py \
  --monitor-once outputs/tasks/<registry>.json \
  --project ee-wsc
```
