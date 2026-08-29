# 维护与贡献规范

## 开发对象

正式工作流由以下两个入口组成：

- `gee/runs/2026824/code_step1_jrc_forest_tiles.py`
- `gee/runs/2026824/code_step2_gmba_treeline.py`

不要通过复制文件创建 `v3.py` 或 `final.py`。旧 `2026821/code_region_revised_v2.py` 仅保留兼容测试，科学变化应进入对应的新阶段入口。

## 分支、提交与修改顺序

- 使用 `feat/`、`fix/`、`test/`、`docs/`、`chore/` 短分支；一个 PR 只处理一个问题。
- 固定顺序：先补失败测试 → 修改对应入口 → 更新方法/运行/数据文档 → 完整测试与 dry-run。
- Step 1 和 Step 2 的变化尽量分别提交；依赖升级必须使用独立 PR。
- CLI、默认值、波段、metadata 或 registry 变化必须同步测试与 `CHANGELOG.md`。

## 离线门禁

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -q -p no:cacheprovider
python -m py_compile .\gee\runs\2026824\code_step1_jrc_forest_tiles.py .\gee\runs\2026824\code_step2_gmba_treeline.py
git diff --check
git status --short
```

CI 只执行离线测试，不保存 Earth Engine 凭据、不访问私有 Asset、不执行 `--check` 或 `--export`。

## 科学变化

- Step 1 算法、源数据、MMU、格网或波段变化时，使用新的 Step 1 `configuration_hash`，重新生成全部所需瓦片；不得混合不同哈希。
- Step 2 研究域、边缘、Otsu、局地检验或输出 schema 变化时，使用新的 run label 和 Step 2 哈希。
- 同步更新 `docs/research/method_decisions.md`、`METHOD_GMBA_REVISED.md` 和 `notes/reproduction-log.md`。
- 新代码不得用 `--resume` 继承旧哈希 Asset，不得用覆盖写入作为常规恢复方式。

## Earth Engine 人工门禁

Cloud Project 固定为 `ee-wsc`。推荐顺序：

1. Step 1 离线 dry-run 和只读 check；
2. 人工确认有效瓦片、纬度诊断、目标集合和任务数；
3. 明确授权后执行小批 Step 1 pilot，并验收两个集合；
4. Step 1 全量完成且完整性检查通过；
5. Step 2 单山体只读 check；
6. 明确授权后执行不超过 10 山体 pilot；
7. pilot 验收后再授权扩批。

任何 `--export` 都必须预先确认输入、输出、范围、任务数和成本。分析 TABLE 固定为 `projects/ee-wsc/assets/Alpine/GMBA_Sayre`，并复核 `hm_fraction >=0.50`、`tree_fraction <=0.90` 和唯一 `GMBA_V2_ID`；不得换用其他 Asset。

## 产物

Git 只保存源码、测试、依赖和文档。registry、错误报告、检查报告、地图、验证数据和导出结果写到仓库外，例如：

```powershell
--registry-dir D:\实验复现\Globaltreeline_artifacts\<YYYYMMDD-run_label>\tasks
```

每批归档 registry、输入/输出 Asset、两阶段配置哈希、Git commit、run label、验收结果和 SHA-256 清单；不得归档凭据。
