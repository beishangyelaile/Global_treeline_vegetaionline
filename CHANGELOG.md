# Changelog

本文件记录源码、CLI、测试和输出契约的显著变化。实验过程和 GEE 任务状态继续记录在 `notes/reproduction-log.md`。

## [Unreleased]

### Added

- 两个正式入口：全球连续森林瓦片 Step 1 与筛选后逐 GMBA 树线 Step 2。
- 两组 2026824 离线契约测试、Step 1 瓦片清单和 Step 2 森林产品完整性门禁。
- 两阶段数据层文档和 GEE 入口索引。

### Changed

- 正式架构由单入口改为 Step 1/Step 2；旧 v2 入口降为历史兼容。
- Step 1 固定 JRC 式顺序、八邻域、`maxSize=50` 和 `count×pixelArea`：先填充 `<=0.5 ha` 非森林小间隙，再保留 `>=0.5 ha` 森林。
- Step 2 分析 TABLE 固定为 `GMBA_Sayre`：保留 `hm_fraction >=0.50`、WorldCover 2021 Class 10 树木覆盖率 `tree_fraction <=0.90` 的山体，正式域为入选山体的完整 GMBA Basic 几何。
- Step 2 先 mosaic/中值/Zero Crossing，之后才应用完整 GMBA 分析域，不使用山体 buffer。
- Step 2 从 `GMBA_V2_ID` 派生运行键，新增 `hm_fraction`、`tree_fraction` QA/元数据，并在 check/export 前复核 TABLE schema、唯一性和阈值。
- CI dry-run 同时验证两个新入口，仍保持完全离线。

### Removed

- 当前正式流程中的对象标签/对象内面积求和、先删小森林后填孔和 `maxSize=500` 方案。
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
