# 978 个 GMBA 山体的 Asset 导出

## 范围与分片

- 脚本：`code_region.py`
- 认证：ADC；计费/执行项目 `ee-wsc`
- 清单：`projects/ee-wsc/assets/Alpine/GMBA_8regions_Sayre31_32_manifest`
- 山体：`projects/ee-remote/assets/Alpine/GMBA_v2` 中 `MapUnit=Basic`，按整数格式的 `GMBA_V2_ID` 连接
- 清单/匹配山体：978/978；8 个 `region_id`
- 研究域：978 个匹配 GMBA 山体的完整几何；不与 Sayre 31/32 相交
- 分析格网：全局对齐 0.25°；共 14,392 个区域内格网
- 导出分片：相邻分析格网归入全局对齐 2°父格网；共 427 个分片
- 任务计划：427 分片 × 3 个产品 = 1,281 个 Asset 任务
- 方向模式：`none`

分片数：R1=22、R2=68、R3=17、R4=31、R5=104、R6=53、R7=117、R8=15。

## 温度分割

每个 2°分片独立使用候选林线像元处的原始 UInt16 BIO1 做 Otsu；不使用固定温度。算出的原始整数阈值再按 `threshold_c = raw*0.1-273.15` 转成摄氏度。空或退化直方图标记 `otsu_valid=0` 并输出空影像，不回退到全局阈值。

在线 tracer `[11.2,47.1,11.3,47.2]` 的原始阈值为 2748，对应 1.65 °C；这只是试运行结果，不会复用于正式分片。

## 输出集合

| 产品 | 目标 ImageCollection | 类型 | pyramiding |
|---|---|---|---|
| treeline30m | `projects/ee-alpine-506212/assets/Treeline_30m_Collection` | Float32 | mean |
| treeline1km | `projects/ee-alpine-506212/assets/Treeline_1km_Collection` | Float32 | mean |
| qa30m | `projects/ee-alpine-506212/assets/Treeline_QA30m_Collection` | Int16 | mode |

子资产名包含 `region_id`、2°格网横纵索引和方向模式。默认拒绝覆盖已有资产。

## 已完成检查

- 三个目标均为可读的空 `IMAGE_COLLECTION`。
- ADC 有效，quota project 为 `ee-wsc`。
- 在线连接匹配 978/978 个 GMBA Basic 山体。
- 在线解析 14,392 个 0.25°格网、427 个 2°分片和 1,281 个不存在的目标资产。
- tracer 的 CHELSA 原始范围 2693–2800；服务端和本地 Otsu 必须一致。
- 试运行只生成 `region_check_console.json` 与 `region_check_map.html`，不启动导出。

## 风险

2°分片把单任务限制在最多 64 个 0.25°分析格网内，但 30 m 连通域、邻域统计和复杂山体边界仍可能触发 Earth Engine 内存或运行时限制。任务登记 JSON 是提交与重试的权威记录。

旧的“8 个整区 × 3 = 24 个任务、固定 1.65 °C”方案从未提交，现已废止。
