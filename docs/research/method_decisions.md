# Global treeline method decisions

- 状态：当前有效
- 最近更新：2026-08-31
- 适用入口：`gee/runs/2026824/code_step1_jrc_forest_tiles.py`、`gee/runs/2026824/code_step2_gmba_treeline.py`、`gee/runs/2026824/code_step2b_treeline1km_from_30m.py`

本文件记录当前已接受的科学和计算决定。历史 `2026821/code_region_revised_v2.py` 的完整 GMBA 单阶段方法已被本架构取代。

## MD-001：三阶段边界

- 状态：已接受
- 类型：计算架构决定

Step 1 独立生成连续的二值森林瓦片；Step 2A 只消费这些瓦片并生成逐山体 30 m 树线与 QA；Step 2B 只消费已完成并验收的 30 m 树线 Asset，生成 30 arc-second 汇总。Step 2A 不得读取原始冠层高度、重新二值化或重做森林 MMU；Step 2B 不得重建 Step 2A 完整计算图。这样把全球连续邻域、逐山体统计和低分辨率聚合分别物化并验收。

## MD-002：森林定义与年份

- 状态：已接受
- 类型：论文披露 + 实现决定

- 数据源为 GLAD 2000/2020 约 30 m 冠层高度，两个年份必须来自各自真实源影像。
- 严格 `height >3 m` 是主结果；严格 `height >5 m` 是并行敏感性结果。
- 保留 GLAD 原始有效掩膜，覆盖范围外不得无条件恢复为非森林。

这仍是冠层高度二值图，不等同于 JRC GFC2020 的 10 m 森林土地利用定义。

## MD-003：Step 1 固定 JRC 式 MMU

- 状态：已冻结
- 类型：实现决定，参考 JRC GFC2020 v2 公开源码

每个年份和阈值在未被 GMBA 裁剪的连续计算图上执行：

1. 第一次 `connectedPixelCount(maxSize=500, eightConnected=True)`；
2. `count × pixelArea`，填充面积 `<=5000 m²` 的非森林小连通域；
3. 对填充后的森林 self-mask；
4. 第二次相同八邻域计数；
5. `count × pixelArea`，保留面积 `>=5000 m²` 的森林；
6. 只在原始有效掩膜内恢复为 0/1 Byte 图。

禁止使用对象标签、对象内 `pixelArea` 求和、先删小森林后填孔或可调 MMU CLI。`ee.Image.pixelArea()` 明确重投影到森林图原生投影。

### 与 JRC 的关系

一致：处理顺序、八邻域、0.5 ha 面积规则。

一致：JRC 公开源码同样使用 `maxSize=500`。不一致之处仍包括输入、分辨率和森林定义，因此不能写成完整复现 JRC 森林产品。`maxSize=500` 的服务端成本和大对象截断语义仍需在经授权的有界在线执行中验证。

## MD-004：Step 1 瓦片与格网

- 状态：已接受，纬度范围需 check 决定

- GMBA v2 Standard `MapUnit=Basic` 只筛选相交的 10°×10°导出瓦片，不裁剪/掩膜森林图。
- 输出使用 `EPSG:4326` 和全局对齐 transform `[0.00025,0,-180,0,-0.00025,90]`；相邻瓦片共享同一计算图和格网。
- 默认先检查 `-60°..80°`，但不得称其为完整全球覆盖。check 统计范围外 GMBA、当前 manifest 成员和有效森林；若目标遗漏，清单自动扩展到 `-90°..90°`。
- 3 m、5 m 分别写入 `Global_tree_3m`、`Global_tree_5m`，每个瓦片包含 `tree_2000`、`tree_2020`。

## MD-005：Step 2 研究域与边缘

- 状态：已接受；TABLE 已只读验收
- 类型：研究域变更

- 正式输入是 `projects/ee-wsc/assets/Alpine/GMBA_Sayre`。它仅保留 `hm_fraction >=0.50` 且 `tree_fraction <=0.90` 的 GMBA Basic。
- `hm_fraction=(hm31_km2+hm32_km2)/gmba_area_km2`，高山/极高山占比下限为 50%。
- `tree_fraction=tree_area_km2/gmba_area_km2`，树木面积来自 ESA WorldCover 10 m 2021（`ESA/WorldCover/v200`、`Map` 波段、Class 10）；超过 90% 的山体被剔除。
- TABLE 几何保留入选山体的完整 GMBA Basic，而不是 `GMBA∩Sayre` 的裁剪几何。正式像元分析域、候选中心、森林/非森林局地样本均使用该完整几何。
- 不使用 0.25°格网或山体 buffer。
- 森林集合必须先 mosaic，再执行半径 1 像元中值滤波、Laplacian 8 邻域和 Zero Crossing；分析域只能在边缘已形成后介入，避免把研究域或瓦片边界识别成森林边缘。
- 两个森林集合在 `mosaic().select(["tree_2000", "tree_2020"])` 后、任何像元邻域或跨尺度运算前，必须用 `setDefaultProjection` 显式声明 Step 1 的全局对齐 `EPSG:4326`、0.00025° transform。这里不使用 `reproject()` 强制提前重采样。

2026-08-24 只读验收：3,115 个要素、3,115 个唯一 `GMBA_V2_ID`、全部 `MapUnit=Basic`；`hm_fraction` 最小 0.500055，低于 0.50 为 0；`tree_fraction` 最大 0.899486，高于 0.90 为 0；两字段无空值。首要素几何面积约 325.36 km²，接近 `gmba_area_km2=326.09`，而非 `hm_area_km2=170.95`，据此确认完整 GMBA 几何语义。

2026-08-31 投影 A/B 验收：在固定源码和 10011–10013 三座山体上，仅增加上述默认投影；9/9 对三类产品均不等价，但波段、PixelType、原生输出格网和 pyramiding policy 一致。差异首先出现在 CHELSA 原生格候选聚合及 Otsu 样本/阈值，随后经冷区和局地检验放大到树线 mask 与 1 km 值。因此最终导出 transform 不能补救此前已经发生的聚合和阈值差异，显式默认投影属于科学可复现性要求。

## MD-006：landform、Otsu 与局地高程检验

- 状态：已接受

- 先排除 ALOS landform 41/42。
- Otsu 按每个山体、每个 3/5 m 阈值计算，合并 2000/2020 post-landform 候选；CHELSA 原生格每格只计一次，同一阈值用于两年。
- 样本不足或直方图退化时 `otsu_valid=0`，不得使用全局阈值回退，也不得按瓦片重算。
- 局地检验使用半径 150 m 方形核（约 300 m × 300 m），主结果为单侧 Welch t 检验，森林高程低于非森林高程；每组最少 5 个样本。

论文未完整披露 Otsu 总体、退化策略、t 检验方差/方向和最小样本量，以上属于项目为可复现性固定的实现决定。

## MD-007：完整性、输出与追溯

- 状态：已接受

- Step 2 发起任何任务前，3/5 m 瓦片 ID 必须一致，波段完整，`mmu_max_size=500`，配置哈希与格网一致，无缺失、重复、失败或空 Asset，且覆盖当前山体。
- 完整产品契约仍为每个山体输出 `treeline30m`、`treeline1km`、`qa30m`，但正式生产顺序固定为 Step 2A 默认导出 `treeline30m qa30m`，待 30 m 完成并验收后，再由 Step 2B 单独生成 `treeline1km`。旧完整计算图的 direct 1 km 仅允许通过 `--export-products treeline1km` 单独显式选择作对照，不能与 Step 2A 产品同批。
- Step 2B 将四个固定 30 m 波段一次性执行 `reduceResolution(mean, bestEffort=False, maxPixels=2048)` 并投影到固定 30 arc-second 格网；变化率为两年均值之差除以 20。源 30 m 缺失、为空、波段/投影或 provenance 不匹配时拒绝构图。
- 分类、布尔和计数 QA 使用 `mode`；DEM、高程差和 t 统计量等连续变量使用 `mean`。
- 三个阶段分别记录实现指纹、配置哈希、Git commit、run label、输入、目标和 registry。Step 2B 哈希包含源 Step 2 哈希、聚合方法、输入/输出格网、`maxPixels` 和实现指纹，不含山体 offset、批次大小或单任务 ID。不同哈希不得通过 `--resume` 混用。

2026-08-31 只读恢复诊断确认原 100 山体批次为 292 个任务完成、8 个 direct `treeline1km` 因 OOM 失败；8 个失败山体的兄弟 `treeline30m` 均完成，且 100 个源 30 m Asset 全部通过完整性门禁。11158 的 30 m/QA 已完成，direct 1 km 在第 5 次尝试 OOM 且目标不存在。三个 direct 成功山体的 v1/from-30m v2 比较显示有效掩膜和局部值并不等价；9 个独立 30 arc-second 格用显式细像元相交面积加权复算，最大高程误差约 `1.003e-4 m`、最大变化率误差约 `5.016e-6 m/yr`，通过浮点容差。

## 被取代或拒绝的方案

| 方案 | 状态 | 原因 |
| --- | --- | --- |
| 单入口从原始冠层高度直接构造逐山体树线 | 被取代 | 无法独立验收连续森林中间产品 |
| 未经 Sayre/WorldCover 筛选的全部 GMBA Basic | 被取代 | 当前只保留满足两项比例阈值的山体 |
| 把 `GMBA_Sayre` 几何解释为 Sayre 像元交集 | 拒绝 | 实际几何面积与完整 GMBA 面积一致 |
| 0.25°有效格网 | 拒绝 | 切碎山体统计单元 |
| GMBA 裁剪后再执行森林 MMU/边缘 | 拒绝 | 会制造边界伪影并切断连通对象 |
| 对每个 10°瓦片先提边再拼接 | 拒绝 | 会把瓦片边界引入边缘结果 |
| 依赖 `ImageCollection.mosaic()` 的隐式默认投影 | 拒绝 | A/B 已证实会改变 CHELSA 聚合、Otsu 阈值和最终树线支持；导出格网不能逆转上游非线性决定 |
| 正式 1 km 继续直接执行未物化的完整 Step 2A 图 | 被取代 | 大型山体已出现可重复 OOM；物化 30 m 后的独立单次聚合可验收来源并缩短计算图 |
| 对象标签与对象内面积求和、先删森林后填孔 | 被取代 | 不符合本次固定 JRC 式顺序 |
| `maxSize=50` 方案 | 被取代 | 用户决定恢复 JRC 公开源码使用的 500；旧配置任务已取消且不得恢复混用 |
| Canny 主结果 | 拒绝 | 主方法固定 Zero Crossing |
| 每年或每瓦片独立 Otsu | 拒绝 | 阈值漂移会混入年代/空间变化 |
| 静默全局 Otsu 回退 | 拒绝 | 隐藏无效山体 |

## 待验证

- `maxSize=500` 的服务端计算成本，以及高纬度、小像元面积和极大连通对象上的行为与科学影响。
- Step 1 有效瓦片数、默认纬度范围是否触发自动扩展、两个目标集合的真实执行可行性。
- `hm_fraction` 个别值略高于 1（当前最大 1.002936）的面积/投影数值来源及是否需要数据生产侧修正；运行时不静默裁剪该值。
- Step 2B 的只读图序列化与数值核验已完成；实际 Asset 导出及服务端异步执行尚未获得授权，未验证。

## 主要依据

- Liang et al. (2026), DOI `10.1016/j.jag.2026.105088`。
- Bourgoin et al. (2026), DOI `10.5194/essd-18-1331-2026`。
- JRC GFC2020 v2 public source: `https://figshare.com/articles/software/29315528`。
- Körner et al. (2022), DOI `10.1038/s41597-022-01256-y`。
