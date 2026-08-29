# 两阶段全球高山树线方法

## 架构

当前正式方法拆为两个独立程序：

1. `2026824/code_step1_jrc_forest_tiles.py`：从全球连续 GLAD 冠层高度生成 3 m、5 m 二值森林瓦片。
2. `2026824/code_step2_gmba_treeline.py`：只读取 Step 1 瓦片，在经 Sayre/WorldCover 规则筛选的完整 GMBA Basic 几何内提取树线。

旧 `code_region_revised_v2.py` 仅保留历史兼容；其“完整 GMBA 内直接重算森林 MMU”的计算图不再是正式方法。

## Step 1：连续二值森林

2000/2020 分别读取真实 GLAD 冠层高度。对 3 m、5 m 分别严格执行 `height.gt(threshold)`，保留源影像有效掩膜，不先 clip 或 mask 到 GMBA。

每个年份/阈值固定顺序为：

```text
连续冠层高度二值图
→ connectedPixelCount(maxSize=500, eightConnected=True)
→ count × pixelArea
→ 填充面积 <=5000 m² 的非森林小连通域
→ 再次 connectedPixelCount(maxSize=500, eightConnected=True)
→ count × pixelArea
→ 保留面积 >=5000 m² 的森林
→ 在原始有效掩膜内恢复0/1 Byte图
```

`ee.Image.pixelArea()` 重投影到森林图投影。禁止对象标签/对象内面积归约，也禁止先删除小森林再填孔。两次计数均固定 `maxSize=500`。

这采用了 JRC GFC2020 v2 的处理顺序、八邻域、0.5 ha 规则和 `maxSize=500`。但 JRC 是 10 m 森林土地利用产品，本研究是约 30 m GLAD 冠层高度二值图，因此仍不是森林定义或产品层面的完整复现。

3 m、5 m 分别写入：

```text
projects/ee-alpine-506212/assets/Global_tree_3m
projects/ee-alpine-506212/assets/Global_tree_5m
```

每个 10°瓦片均含 `tree_2000`、`tree_2020`，采用全局对齐 `EPSG:4326` transform `[0.00025,0,-180,0,-0.00025,90]` 和 `mode` 金字塔策略。GMBA Basic 只用于选择相交瓦片；输出瓦片内部保留完整森林背景。

默认检查 `-60°..80°`，但这不是完整全球覆盖。只读 check 统计范围外 GMBA、manifest 目标和有效森林；若目标遗漏，瓦片清单自动扩展到 `-90°..90°`。

## Step 2：逐山体树线

Step 2 默认读取 `projects/ee-wsc/assets/Alpine/GMBA_Sayre`。该 TABLE 的山体筛选规则为：

- `hm_fraction >=0.50`：Sayre 31/32 高山与极高山面积至少占完整 GMBA Basic 的 50%；
- `tree_fraction <=0.90`：ESA WorldCover 10 m 2021（`ESA/WorldCover/v200`，`Map` 波段 Class 10）树木覆盖面积最多占 90%，即剔除 `>90%` 的山体。

TABLE 几何保留入选山体的完整 GMBA Basic，不是 Sayre 31/32 裁剪交集。Step 2 先分别 mosaic 3 m、5 m ImageCollection，再选择 2000/2020 波段。对连续图执行半径 1 像元方形中值滤波、Laplacian 8 邻域和 Zero Crossing；只有边缘形成后才应用逐山体完整 GMBA 域。这避免把瓦片或 GMBA 边界误识别成森林边缘。

正式顺序为：

```text
森林瓦片 mosaic
→ 中值滤波
→ Zero Crossing
→ 筛选后完整GMBA Basic分析域
→ 排除 ERGo landform 41/42
→ 合并2000/2020候选
→ CHELSA原生格每格计一次
→ 每山体/每阈值 Otsu
→ 同阈值用于两年
→ 冷区候选
→ 分析域内300 m局地单侧Welch检验
```

无山体 buffer，无 0.25°格网。Otsu 样本不足或退化时 `otsu_valid=0`，不使用全局回退，也不按瓦片重算。

## 输出与 QA

- `treeline30m`：3/5 m × 2000/2020 树线高程。
- `treeline1km`：30 arc-second 格网中的两年平均树线高程和 20 年平均年变化率。
- `qa30m`：分析域、森林、候选/landform 后边缘、Otsu 有效性、DEM、样本数、高程差和 t 统计量。

分类、布尔和计数 QA 使用 `mode`；`hm_fraction`、`tree_fraction`、DEM、高程差、t 统计量等连续变量使用 `mean`。`gmba_mask` 是完整 GMBA 分析域；`sayre_high` 仅表示该山体通过 `hm_fraction >=0.50` 筛选，不是逐像元 Sayre 掩膜。

## 完整性门禁与限制

Step 2 在构图前核对两个森林集合的瓦片 ID、波段、非空状态、`mmu_max_size=500`、配置哈希、CRS/transform、缺失/重复/失败记录和当前山体所需瓦片。任一不符即拒绝创建任务。

只读 TABLE 验收确认 3,115 个唯一 Basic 山体且全部满足两项阈值。尚待服务端确认：`maxSize=500` 的计算成本及其在高纬度/极大对象上的影响、Step 1 导出图执行可行性，以及代表性山体的 Step 2 深度检查。

## 参考

- Liang et al. (2026), DOI `10.1016/j.jag.2026.105088`。
- Bourgoin et al. (2026), DOI `10.5194/essd-18-1331-2026`。
- JRC GFC2020 v2 public source: https://figshare.com/articles/code/Joint_Research_Centre_-_Global_Forest_Cover_for_year_2020_version_2_Code_source/29315528
