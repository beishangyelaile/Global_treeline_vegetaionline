# 数据层与 Asset 契约

## Step 1 输入

| 角色 | Asset | 契约 |
| --- | --- | --- |
| 冠层高度 2000 | `projects/glad/GLCLU2020/Forest_height_2000` | 首波段；有效掩膜必须保留 |
| 冠层高度 2020 | `projects/glad/GLCLU2020/Forest_height_2020` | 首波段；不得用 2000 占位 |
| 瓦片筛选 | `projects/ee-remote/assets/Alpine/GMBA_v2` | TABLE；只取 `MapUnit=Basic`；不得裁剪森林图 |
| 当前目标核对 | `projects/ee-wsc/assets/Alpine/GMBA_8regions_Sayre31_32_manifest` | TABLE；用于纬度范围外目标计数 |

2026-08-24 只读检查时，上述 manifest Asset 在该文件夹中已不存在。代码将该项记录为 `asset_not_found`，不使用其他 TABLE 替代，并保守扩展到 `-90°..90°`；正式运行前应由研究者确认该 manifest 是否需要恢复。

## Step 1 输出

| 阈值 | ImageCollection | 子 Asset |
| --- | --- | --- |
| `>3 m` | `projects/ee-alpine-506212/assets/Global_tree_3m` | `GFC_2000_2020_{tile_id}` |
| `>5 m` | `projects/ee-alpine-506212/assets/Global_tree_5m` | `GFC_2000_2020_{tile_id}` |

每个 IMAGE 必须非空并包含顺序固定的 `tree_2000`、`tree_2020` Byte 波段。固定格网为 `EPSG:4326`、`[0.00025,0,-180,0,-0.00025,90]`；金字塔策略为 `mode`。

必备属性：`canopy_threshold_m`、`mmu_max_size=500`、`mmu_area_m2=5000`、`mmu_connectivity=8`、`mmu_method=JRC_sequence_maxSize500`、两个源 Asset、`tile_id`、`bbox`、格网、`configuration_hash`、`implementation_sha256`、run label 和 Git commit。

## Step 2A 输入

| 角色 | Asset | 契约 |
| --- | --- | --- |
| 3 m森林 | `projects/ee-alpine-506212/assets/Global_tree_3m` | Step 1 完整验收后读取 |
| 5 m森林 | `projects/ee-alpine-506212/assets/Global_tree_5m` | 瓦片/哈希/格网与3 m一致 |
| 分析山体 | `projects/ee-wsc/assets/Alpine/GMBA_Sayre` | 3,115 个唯一 Basic；完整 GMBA 几何；代码从 `GMBA_V2_ID` 派生运行键 |
| 树木覆盖筛选 | `ESA/WorldCover/v200` | 2021，`Map` 波段，Class 10，10 m；计算 `tree_fraction` |
| 温度 | `projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21` | 原生格网每候选格只计一次；`raw*0.1-273.15` |
| DEM | `JAXA/ALOS/AW3D30/V4_1` | `DSM/MSK/STK` |
| landform | `CSP/ERGo/1_0/Global/ALOS_landforms` | 排除41/42 |

3 m、5 m 集合均先 `mosaic`、选择 `tree_2000/tree_2020`，再以 `setDefaultProjection` 显式声明 Step 1 的 `EPSG:4326`、`[0.00025,0,-180,0,-0.00025,90]` 默认网格；该声明必须位于中值、Zero Crossing 和 CHELSA 跨尺度聚合之前。不得用全局 `reproject()` 替代，也不能只依赖最终导出的 transform。

固定筛选为：

```text
hm_fraction = (hm31_km2 + hm32_km2) / gmba_area_km2 >= 0.50
tree_fraction = WorldCover Class 10 tree_area_km2 / gmba_area_km2 <= 0.90
```

因此剔除高山/极高山占比不足 50% 或 WorldCover 树木覆盖率大于 90% 的山体。`GMBA_Sayre` 的几何没有裁剪到 Sayre 31/32；首要素几何约 325.36 km²，接近完整 `gmba_area_km2=326.09`，而不是 `hm_area_km2=170.95`。

2026-08-24 验收：3,115 个要素与唯一 `GMBA_V2_ID`；全部 `MapUnit=Basic`；`hm_fraction` 范围 0.500055–1.002936，低于阈值 0；`tree_fraction` 范围 0–0.899486，高于阈值 0；两字段无空值。`hm_fraction` 略高于 1 的记录保留原值，尚待数据生产侧解释，不在运行时静默截断。

## Step 2B 输入

| 角色 | Asset | 契约 |
| --- | --- | --- |
| 物化 30 m 树线 | `projects/ee-alpine-506212/assets/Treeline_30m_Collection_v2/<child>` | 对应任务为 `COMPLETED`；IMAGE 非空；四个固定 Float 波段顺序一致；所有波段同一 0.00025°全局对齐格网；mountain ID、源 Step 2 哈希、run label、Git commit 和 child 名匹配 |
| 原任务 registry | 本地 JSON | 只读；最新远端状态优先于提交时快照；不得覆盖或用普通 Step 2 `--resume` 恢复 OOM direct 1 km 图 |

Step 2B 不读取 Step 1 森林集合、WorldCover、温度、DEM 或 landform。四个 30 m 波段一次性聚合到固定 30 arc-second 格网，`bestEffort=False`、`maxPixels=2048`。

## Step 2 输出

目标集合固定为：

```text
projects/ee-alpine-506212/assets/Treeline_30m_Collection_v2
projects/ee-alpine-506212/assets/Treeline_1km_Collection_v2
projects/ee-alpine-506212/assets/Treeline_QA30m_Collection_v2
```

每个山体的完整产品仍为一个 30 m 树线、一个 1 km 汇总和一个 30 m QA IMAGE。Step 2A 默认先生成 30 m 树线与 QA；Step 2B 仅在 30 m 完成并验收后生成新的 1 km child。旧 direct 1 km Asset 不覆盖、不删除，也不与 from-30m v2 混用为同一方法版本。

Step 2A 输出记录 Step 1 两个输入集合、分析 TABLE、CHELSA、配置哈希、run label、mountain ID、Git commit，以及森林 mosaic 默认投影的 CRS、transform 和插入位置。Step 2B 另记录源 30 m Asset、源 Step 2 配置哈希/run label/Git commit、聚合方法、输入/输出格网、`maxPixels=2048`、`bestEffort=false`、聚合配置哈希、实现 SHA、当前 Git commit 和被恢复的旧 task ID。

## 完整性门禁

Step 2A 默认读取受版本控制的 `gee/runs/2026824/step2_validated_upstream_20260831.json`。该凭据绑定正式项目、分析 TABLE、两个 Step 1 集合、manifest 内容哈希、科学与完整性验证函数源码、全部输入数据/格网/阈值参数，并保存已通过的 TABLE、Step 1 全量完整性、全分析域瓦片覆盖和 representative deep check 事实。任一身份字段不一致时，必须在创建任务前拒绝旧凭据。

凭据匹配时，Step 2A 不再逐批远程复查以下已确定事实：3/5 m 各 304 个瓦片、两年波段、非空 IMAGE、`mmu_max_size=500`、统一 Step 1 哈希/格网、全部 3,115 个山体所需 182 个瓦片无缺失、分析 TABLE 3,115 个完整唯一 Basic 记录，以及 GMBA 10067 的 h3m/h5m deep check 有效。`--revalidate-upstream` 恢复 TABLE、Step 1 inventory 和当前批次覆盖的实时门禁；`--check --revalidate-upstream --deep-check` 另恢复 Otsu 实算。

该优化明确假设固定 ID 下的 GEE Asset 内容未被原地覆盖；本地代码无法廉价证明远端不可变性。若任何上游 Asset 被重建/覆盖、manifest 或绑定参数/代码变化，必须先实时重验并建立新凭据。无论使用哪种上游模式，输出集合类型与冲突、已有任务、队列限制、选择范围和配置哈希都不会被跳过。

Step 2B 必须拒绝以下任一情况：源 30 m 任务未完成、Asset 缺失或为空、四带名称/顺序错误、CRS/分辨率/全局格网对齐错误、mountain ID/run label/配置哈希/provenance 不匹配；新目标同哈希仅可用 `--resume` 跳过，异哈希一律拒绝。
