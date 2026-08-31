# Changelog

本文件记录源码、CLI、测试和输出契约的显著变化。实验过程和 GEE 任务状态继续记录在 `notes/reproduction-log.md`。

## [Unreleased]

### Added

- 三个正式入口：全球连续森林瓦片 Step 1、筛选后逐 GMBA 30 m 树线 Step 2A，以及从物化 30 m 生成 30 arc-second 1 km 产品的 Step 2B。
- 2026824 离线契约测试、Step 1 瓦片清单、Step 2A 森林产品门禁和 Step 2B 源 Asset/投影/provenance 门禁。
- Step 2B 原批次只读诊断、目标冲突检查、direct-v1/from-30m-v2 比较和显式细像元相交面积加权核验。
- 三阶段数据层文档和 GEE 入口索引。

### Changed

- 正式架构由单入口改为 Step 1/Step 2A/Step 2B；旧 v2 入口降为历史兼容。
- Step 1 固定 JRC 式顺序、八邻域、`maxSize=500` 和 `count×pixelArea`：先填充 `<=0.5 ha` 非森林小间隙，再保留 `>=0.5 ha` 森林。
- Step 2 分析 TABLE 固定为 `GMBA_Sayre`：保留 `hm_fraction >=0.50`、WorldCover 2021 Class 10 树木覆盖率 `tree_fraction <=0.90` 的山体，正式域为入选山体的完整 GMBA Basic 几何。
- Step 2 先 mosaic/中值/Zero Crossing，之后才应用完整 GMBA 分析域，不使用山体 buffer。
- Step 2 方法身份升级为 v2：森林集合在 mosaic/select 后显式声明 Step 1 的全局对齐 0.00025°默认投影，再执行邻域与 CHELSA 跨尺度运算；投影 A/B 已证明隐式默认投影会改变 Otsu 和最终树线结果。
- Step 2 `--check` 改用紧凑 Earth Engine 表达式序列化，避免把共享计算图递归展开为超大字符串；仍不启动导出任务。
- Step 2 从 `GMBA_V2_ID` 派生运行键，新增 `hm_fraction`、`tree_fraction` QA/元数据，并在 check/export 前复核 TABLE schema、唯一性和阈值。
- Step 2 的 QA 波段验收顺序与实际组装顺序统一：`hm_fraction`、`tree_fraction` 位于 `non_valley` 之前；计算图和既有 Asset 内容不变。
- Step 2A 的 `--export-products` 默认改为 `treeline30m qa30m`；旧 direct `treeline1km` 通常仅允许单独显式选择。受保护的 `--allow-direct-1km-ab` 例外必须恰好选择三产品且 `--max-mountains 1`。
- Step 2A legacy direct 与 Step 2B 的 1 km 聚合均固定为 `bestEffort=False`、`maxPixels=2048`；Step 2B 从已完成且严格验收的 `treeline30m` 一次性聚合四带，并使用独立聚合哈希/子 Asset 名。
- Step 2A/Step 2B 默认输出集合切换到三个 `_v2` ImageCollection；A/B 比较在目标存在时读取实际 Step 2B Asset，并分别报告六波段完整案例与逐波段成对有效区。
- CI dry-run 同时验证两个新入口，仍保持完全离线。

### Removed

- 当前正式流程中的对象标签/对象内面积求和、先删小森林后填孔和 `maxSize=50` 方案。
- Step 2 对原始冠层高度和森林 MMU 的任何依赖。

## [0.1.0] - 2026-08-24

### Added

- `code_region_revised_v2.py` 的统一离线回归测试。
- Python 3.11.9 和 Earth Engine/geemap/pytest 精确依赖版本。
- 不含 GEE 凭据和在线调用的 GitHub Actions 离线门禁。
- 仓库贡献、版本、科学变更和运行产物归档规则。

### Changed

- 将 `code_region_revised_v2.py` 确立为唯一受支持的工作流入口。
- README 和逐 GMBA 运行手册统一使用当前入口和 Asset 导出流程。
- 运行产物改为仓库外保存，并由 SHA-256 清单审计。

### Removed

- 早期 tracer、区域合并、2° 分片和 JavaScript 平行入口。
- 已被统一测试取代的旧测试文件。
- Git 中的任务登记、检查报告、HTML 地图和验证压缩包。
