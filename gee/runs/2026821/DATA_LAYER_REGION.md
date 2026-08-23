# 978 个 GMBA 山体：数据与输出层

| 角色 | 资产 | 用途 |
|---|---|---|
| 区域清单 | `projects/ee-wsc/assets/Alpine/GMBA_8regions_Sayre31_32_manifest` | 提供 978 个 `GMBA_V2_ID` 与 8 个区域标签 |
| 山体几何 | `projects/ee-remote/assets/Alpine/GMBA_v2` | 过滤 `MapUnit=Basic` 后恢复完整山体几何；直接作为研究域 |
| 2000 林高 | `projects/glad/GLCLU2020/Forest_height_2000` | 30 m 森林与树线提取 |
| 2020 林高 | `projects/glad/GLCLU2020/Forest_height_2020` | 30 m 森林与树线提取 |
| CHELSA BIO1 | `projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21` | 每个 2°分片动态划分冷/热区 |
| 高程/QA | `JAXA/ALOS/AW3D30/V4_1` | DSM、MSK、STK |
| 地形类型 | `CSP/ERGo/1_0/Global/ALOS_landforms` | 排除谷地类 41/42 |
| 林地比例 | `ESA/WorldCover/v100` | 排除树覆盖比例大于 95% 的 0.25°格网 |

本方案不读取 `projects/ee-remote/assets/Alpine/high_mountain`，也不做 Sayre 31/32 相交或“高山比例大于 10%”筛选。这是相对论文域定义的显式调整；保留论文的 0.25°处理尺度和大于 95%纯林格网排除规则。

BIO1 上传影像为 UInt16 十分之一 Kelvin。Otsu 对原样整数执行，之后仅将阈值按 `raw*0.1-273.15` 转为 °C。正式导出逐 2°分片动态求阈值；tracer 的 2748/1.65 °C 不作为固定阈值。

连续树线产品输出 Float32/mean pyramiding；QA 输出 Int16/mode pyramiding。每张影像记录 `region_id`、分片索引、相交 GMBA 数和 ID、Otsu 有效性、样本数、原始阈值及摄氏阈值。
