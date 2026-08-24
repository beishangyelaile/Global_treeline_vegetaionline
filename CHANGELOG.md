# Changelog

本文件记录源码、CLI、测试和输出契约的显著变化。实验过程和 GEE 任务状态继续记录在 `notes/reproduction-log.md`。

## [Unreleased]

### Added

- 后续变化在合并前记录于此。

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
