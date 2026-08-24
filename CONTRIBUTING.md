# 维护与贡献规范

## 基本原则

- 唯一公开入口是 `gee/runs/2026821/code_region_revised_v2.py`。
- 后续直接迭代该文件，不复制 `v3.py`、`final.py` 或其他平行入口。
- `main` 必须始终可安装、离线测试通过且 dry-run 可运行。
- 一个分支和 PR 只处理一个目的明确的变更。
- CI 不连接 Earth Engine；所有在线检查和导出都由人工确认后在本地运行。

## 开发环境

固定使用 Python 3.11.9：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --disable-pip-version-check -r requirements-dev.txt
```

运行依赖和测试依赖均使用精确版本。依赖升级必须单独提交，并重新执行本文全部离线门禁。

## 分支与提交

从最新 `main` 创建短分支：

- `feat/<name>`：新增行为。
- `fix/<name>`：修复缺陷。
- `test/<name>`：只调整测试。
- `docs/<name>`：只调整文档。
- `chore/<name>`：构建、依赖或仓库维护。

提交应小且可独立审查。推荐提交前缀为 `feat:`、`fix:`、`test:`、`docs:`、`build:`、`refactor:` 或 `chore:`。禁止直接向 `main` 强制推送。

## 固定修改顺序

1. 先增加一个能够暴露问题或表达新需求的离线测试。
2. 修改唯一入口；不得建立平行版本脚本。
3. 按变化类型更新 README、运行手册、方法记录或复现日志。
4. 运行离线测试、dry-run 和 Git 差异检查。
5. 推送分支并通过 PR 合并。

每个 PR 至少执行：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -q -p no:cacheprovider
git diff --check
git status --short
```

dry-run 必须使用完整但无敏感信息的参数，确认：

- `ready=true`
- `missing_requirements=[]`
- `selection` 与预期批次一致
- `expected_task_count` 正确

## 变化分类

### 文档或测试变化

- 不改变入口代码时，科学配置哈希不应变化。
- 更新 `CHANGELOG.md` 的 `Unreleased` 段落。

### CLI、registry 或运行保护变化

- 同步修改测试、README 和 `RUN_REGION.md`。
- 检查旧 registry 是否仍可由 `--monitor-once` 读取。
- 若入口文件发生任何变化，必须记录新的 `configuration_hash`。

### 科学方法、计算图、默认参数或输出 schema 变化

- 更新 `METHOD_GMBA_REVISED.md` 和 `notes/reproduction-log.md`。
- 记录变更前后的 `configuration_hash`、Git commit 和 run label。
- 使用新的 run label 和 Git 版本标签。
- 不得对旧哈希的 Asset 使用 `--resume`，不得使用 `--overwrite-assets` 混写结果。

当前 `configuration_hash` 包含入口文件完整 SHA-256。因此，即使入口中的维护性代码变化不影响科学计算，也会产生新的配置哈希。这是当前保守的防混用策略。若未来拆分内部模块，必须先让实现指纹覆盖全部运行源码并增加相应测试。

## Earth Engine 人工门禁

固定 Cloud Project 为 `ee-wsc`。科学代码合并后，在线验证顺序为：

1. 验证 Python、Earth Engine API、geemap、凭据和 `ee-wsc` 初始化。
2. 运行无网络副作用的 dry-run。
3. 对一个代表性山体运行 `--check`；该模式不得提交导出。
4. 使用新 run label 提交不超过 10 个山体的 pilot。
5. pilot 任务和 Asset 验收通过后才扩大批次。

任何 `--prepare-mountains` 或 `--export` 都必须预先确认输入 Asset、输出集合、山体范围、任务数量和成本风险。禁止把 GEE 凭据写入仓库或 GitHub Actions。

## 产物与归档

Git 只保存源码、测试、依赖和文档。任务 registry、错误报告、控制台输出、HTML 地图、验证数据和压缩包不得提交。

导出时优先显式指定仓库外目录：

```powershell
--registry-dir 'D:\实验复现\Globaltreeline_artifacts\<YYYYMMDD-run_label>\tasks'
```

每批归档至少保留：registry、错误报告、配置哈希、Git commit、run label、目标 Asset ID、验收结果和文件 SHA-256 清单。归档目录不得包含凭据。

## 版本号

- `PATCH`：文档、测试或不改变科学输出的维护。
- `MINOR`：算法、默认参数、输出波段或 Asset schema 变化。
- `MAJOR`：研究域、核心数据源或分析单位变化。
