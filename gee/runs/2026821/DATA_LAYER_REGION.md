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

## Step 2 输入

| 角色 | Asset | 契约 |
| --- | --- | --- |
| 3 m森林 | `projects/ee-alpine-506212/assets/Global_tree_3m` | Step 1 完整验收后读取 |
| 5 m森林 | `projects/ee-alpine-506212/assets/Global_tree_5m` | 瓦片/哈希/格网与3 m一致 |
| 分析山体 | `projects/ee-wsc/assets/Alpine/GMBA_Sayre` | 3,115 个唯一 Basic；完整 GMBA 几何；代码从 `GMBA_V2_ID` 派生运行键 |
| 树木覆盖筛选 | `ESA/WorldCover/v200` | 2021，`Map` 波段，Class 10，10 m；计算 `tree_fraction` |
| 温度 | `projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21` | 原生格网每候选格只计一次；`raw*0.1-273.15` |
| DEM | `JAXA/ALOS/AW3D30/V4_1` | `DSM/MSK/STK` |
| landform | `CSP/ERGo/1_0/Global/ALOS_landforms` | 排除41/42 |

固定筛选为：

```text
hm_fraction = (hm31_km2 + hm32_km2) / gmba_area_km2 >= 0.50
tree_fraction = WorldCover Class 10 tree_area_km2 / gmba_area_km2 <= 0.90
```

因此剔除高山/极高山占比不足 50% 或 WorldCover 树木覆盖率大于 90% 的山体。`GMBA_Sayre` 的几何没有裁剪到 Sayre 31/32；首要素几何约 325.36 km²，接近完整 `gmba_area_km2=326.09`，而不是 `hm_area_km2=170.95`。

2026-08-24 验收：3,115 个要素与唯一 `GMBA_V2_ID`；全部 `MapUnit=Basic`；`hm_fraction` 范围 0.500055–1.002936，低于阈值 0；`tree_fraction` 范围 0–0.899486，高于阈值 0；两字段无空值。`hm_fraction` 略高于 1 的记录保留原值，尚待数据生产侧解释，不在运行时静默截断。

## Step 2 输出

目标集合固定为：

```text
projects/ee-alpine-506212/assets/Treeline_30m_Collection
projects/ee-alpine-506212/assets/Treeline_1km_Collection
projects/ee-alpine-506212/assets/Treeline_QA30m_Collection
```

每个山体分别生成一个 30 m 树线、一个 1 km 汇总和一个 30 m QA IMAGE。所有输出记录 Step 1 两个输入集合、分析 TABLE、CHELSA、两阶段哈希、run label、mountain ID 和 Git commit。

## 完整性门禁

Step 2 必须拒绝以下任一情况：3/5 m 瓦片集合不一致、缺少任一年波段、空 Asset、`mmu_max_size` 非500、哈希或格网混合、重复/失败瓦片、当前山体需要的瓦片不在清单、分析 TABLE 类型/ID/唯一键不满足契约。
