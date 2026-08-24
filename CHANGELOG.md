# Changelog

本文件记录源码、CLI、测试和输出契约的显著变化。实验过程和 GEE 任务状态继续记录在 `notes/reproduction-log.md`。

## [Unreleased]

### Added

- QA 波段 `forest_small_patch_removed_*` 和 `nonforest_small_gap_filled_*`。

### Changed

- 二值森林后处理固定为 JRC GFC2020 v2 式 0.5 ha MMU：八邻域、连通对象内 `pixelArea()` 面积求和、先删除小森林斑块再填充小非森林间隙。
- 工作流配置标识更新为 `per-gmba-v4-jrc-mmu`，默认 run label 更新为 `mountain_v4_jrc_mmu`。

### Removed

- 四邻域孔隙判定、矢量边界环和无面积约束填孔实现。
- 可调整固定 MMU 的 CLI 参数及正式导出的填孔假设确认参数。

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
