# 2000/2020 年观测树线提取：运行说明

本目录包含两个可直接粘贴到 Google Earth Engine Code Editor 的 JavaScript：

- `gee_treeline_no_aspect.js`：主分析版本，不做坡向分区。
- `gee_treeline_polar_equator.js`：敏感性分析版本，按半球划分极向坡和赤道向坡。

另有 `code.py`，它将两份脚本合并为 Python/geemap 入口，通过 `--aspect-mode none|polar-equator` 选择版本。`--check` 仅生成 `first_run_console.json` 与 `first_run_map.html`；只有显式 `--export` 且提供固定 °C Otsu 阈值时才会启动导出。Python 使用 `ee-wsc` 的 ADC 凭据，详细命令见 `RUN.md`。

## 1. 研究域

代码使用以下交集作为像元级研究域：

`GMBA v2 Standard ∩ Sayre high/scattered-high（31/32）∩ 论文的有效 0.25° 网格`

有效 0.25° 网格仍按论文筛选：Sayre 高山覆盖率不低于 10%，ESA WorldCover 树覆盖不高于 95%。GMBA Standard 含层级重叠多边形；代码可用它定义研究域，但不能把全部层级的山体统计量当作独立样本。若要山体级推断，请使用 Standard-Basic，或根据资产属性固定一个互不重叠层级。

## 2. 首次运行

1. 在脚本顶部修改 `CONFIG.BBOX`。默认值是一个很小的流程测试框，不是正式研究范围。
2. 保持 `RUN_EXPORTS: false`，先运行并检查 Console 中的资产、GMBA 命中数、CHELSA 范围、Otsu 阈值和图层。
3. CHELSA 官方变量 `bio01` 的物理单位为 °C，但当前上传资产是 UInt16，首检范围为 2693--2800，表明它存储的是十分之一 Kelvin。按本次约定，Otsu 直接在原始整数直方图上计算；得到原始阈值后，再用 `raw * 0.1 - 273.15` 将阈值转换为 °C。首检得到原始阈值 2748，对应 1.65 °C。
4. `OTSU_THRESHOLD_C: null` 时，脚本仅为当前批次估计阈值。正式多瓦片导出前必须把一次统一标定得到的阈值写回 `OTSU_THRESHOLD_C`；脚本不允许使用逐瓦片阈值启动正式导出。
5. 阈值固定后设置 `RUN_EXPORTS: true`。每个 BBOX 单独提交任务，避免一次导出全球 30 m 影像；Drive 目标文件夹为 `Globaltreeline`。

## 3. 已修正的复现问题

- DEM 从存在纬度覆盖缺口的 NASADEM 改为全球 AW3D30 v4.1；仅把 `MSK == 1` 当作无效值，并输出 MSK/STK 供质量控制。`STRICT_AW3D_NATIVE_ONLY: true` 可只保留 `MSK == 0`，用于填补 DEM 敏感性分析。
- 谷地数据改为全球 `CSP/ERGo/1_0/Global/ALOS_landforms`，排除 41/42 类，避免继续继承 SRTM 高纬覆盖缺口。
- 取消会移动森林外边界的形态学 closing。现在对非森林连通域进行标记，只填充不接触处理瓦片外边界的内部孔洞；处理区比导出区外扩 2 km，避免瓦片边缘生成假树线。
- CHELSA Otsu 阈值不再允许按正式导出瓦片分别估计；正式导出必须复用同一个固定阈值。
- 300 m 邻域检验采用方向明确的单侧检验：森林高程显著低于非森林高程才保留。`T_TEST_VARIANCE` 可在 `welch` 与 `pooled` 之间切换。
- 30 m 检测结果按固定 30 arc-second CHELSA 网格计算 1 km 均值；位移率只在两个年份均有树线的像元上计算。

## 4. 论文未披露、不能声称“精确复现”的参数

论文正文没有给出孔洞大小/连通规则、中值滤波核、Otsu 的空间作用域、t 检验等方差假设/单双侧及最小样本数。脚本将这些设为集中配置，并保留 QA 输出。默认值是可审计的实现选择，不是论文原参数：

- `HOLE_MAX_SIZE_PIXELS: 512`
- `MEDIAN_RADIUS_PIXELS: 1`
- `T_TEST_VARIANCE: 'welch'`
- `MIN_SAMPLES_PER_GROUP: 5`
- 单侧 `alpha = 0.05`

## 5. 坡向版本定义

- 北半球：北向坡为极向坡，南向坡为赤道向坡。
- 南半球：南向坡为极向坡，北向坡为赤道向坡。
- 北向坡为 `[315°, 360°) ∪ [0°, 45°)`；南向坡为 `[135°, 225°)`。
- 东/西向坡不进入两组；坡度小于 `MIN_SLOPE_DEG` 的像元不分组；赤道两侧 `EQUATOR_BUFFER_DEG` 范围不分组。
- 300 m 窗口中的森林和非森林样本也限制在同一坡向组内，避免相反坡向混入局地高程检验。

## 6. CHELSA 数据需求

当前“观测树线”提取只使用 `CHELSA_bio01_1981-2010_V21` 做年均温 Otsu 冷/热区分割。已上传的 `gst` 与 `bio04` 不参与这一步。

若后续复现论文的潜在树线（TREELIM），还缺少同一时期、同一版本的 `gsl`（growing season length）；仅有 GST 不足以执行 GSL ≥ 94 d 且 GST ≥ 6.4 °C 的联合规则。若进一步复现驱动模型，还需暖季温度/降水、火频度和人类足迹等数据，但它们不是本次观测树线提取的输入。
