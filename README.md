# Global treeline reproduction

本仓库复现 Liang et al. (2026) 的全球高山树线提取流程：
*Global elevational shifts and drivers of alpine treelines*，DOI:
`10.1016/j.jag.2026.105088`。

## 唯一入口

当前唯一受支持的可执行入口是：

```text
gee/runs/2026821/code_region_revised_v2.py
```

旧版区域合并、2° 分片、JavaScript 和早期 tracer 入口已从当前工作树移除；历史实现仍可通过 Git 历史追溯。方法假设见
[`gee/runs/2026821/METHOD_GMBA_REVISED.md`](gee/runs/2026821/METHOD_GMBA_REVISED.md)，完整运行说明见
[`gee/runs/2026821/RUN_REGION.md`](gee/runs/2026821/RUN_REGION.md)。

当前 `per-gmba-v4-jrc-mmu` 方法固定采用 JRC GFC2020 v2 式二值 MMU 后处理：八邻域删除面积 `<=0.5 ha` 的森林斑块，再填充面积 `<0.5 ha` 的内部非森林间隙，面积均由连通对象内 `ee.Image.pixelArea()` 求和。这只对齐 JRC 的二值后处理；森林输入仍为 GLAD 约 30 m 冠层高度，`>3 m` 为主结果、`>5 m` 为敏感性结果。

## 环境与测试

固定使用 Python 3.11.9；运行库和测试库的精确版本分别记录在 `requirements.txt` 和 `requirements-dev.txt`。安装开发环境并运行测试：

```powershell
python -m pip install --disable-pip-version-check -r requirements-dev.txt
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -q -p no:cacheprovider
```

所有 CI 测试均为离线测试，不配置 GEE 凭据，不初始化 Earth Engine 或提交任务。

## 安全预览

`--dry-run` 只解析参数并输出计划，不访问 Earth Engine：

```powershell
python .\gee\runs\2026821\code_region_revised_v2.py `
  --dry-run `
  --project ee-wsc `
  --prepared-mountains-asset projects/ee-wsc/assets/Alpine/GMBA_Basic_Sayre_selected_v3 `
  --chelsa-bio01 projects/ee-wsc/assets/Alpine/CHELSA_bio01_1981-2010_V21 `
  --treeline30m-collection projects/ee-alpine-506212/assets/Treeline_30m_Collection `
  --treeline1km-collection projects/ee-alpine-506212/assets/Treeline_1km_Collection `
  --qa30m-collection projects/ee-alpine-506212/assets/Treeline_QA30m_Collection `
  --max-mountains 10 `
  --mountain-offset 0
```

## 运行保护

- `--check` 只检查，不提交导出。
- `--export` 必须显式提供 `--max-mountains`；默认拒绝一次提交超过 100 个山体。
- 固定 MMU 不提供面积、连通性或“不填孔”命令行分支；旧 MMU 参数会被拒绝。
- 默认不覆盖 Asset；恢复相同配置使用 `--resume`，不要使用 `--overwrite-assets`。
- 每个山体默认产生 30 m、1 km 和 QA 三个 Asset 任务，任务登记写入
  `gee/runs/2026821/outputs/tasks/`，该目录不属于源码。

仓库只保存源码、测试、方法文档和复现记录。任务登记、检查报告、HTML 地图与验证包保存在仓库外的运行产物目录。

## 维护与开发

- 修改入口前先增加或调整离线测试，不建立新的平行版本脚本。
- 使用 `feat/`、`fix/`、`test/`、`docs/` 或 `chore/` 短分支，通过 PR 合并到 `main`。
- 入口文件任何变化都会改变实现指纹和 `configuration_hash`；不得用新代码恢复旧哈希 Asset。
- 科学代码变化依次经过 dry-run、单山体 check、最多 10 山体 pilot，再考虑扩大批次。
- 详细规则见 [`CONTRIBUTING.md`](CONTRIBUTING.md)，版本变化见 [`CHANGELOG.md`](CHANGELOG.md)。
