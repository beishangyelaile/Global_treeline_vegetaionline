# 维护与贡献规范

## 开发对象

正式工作流由以下三个入口组成：

- `gee/runs/2026824/code_step1_jrc_forest_tiles.py`
- `gee/runs/2026824/code_step2_gmba_treeline.py`
- `gee/runs/2026824/code_step2b_treeline1km_from_30m.py`

不要通过复制文件创建 `v3.py` 或 `final.py`。旧 `2026821/code_region_revised_v2.py` 仅保留兼容测试，科学变化应进入对应的新阶段入口。

## 分支、提交与修改顺序

- 使用 `feat/`、`fix/`、`test/`、`docs/`、`chore/` 短分支；一个 PR 只处理一个问题。
- 固定顺序：先补失败测试 → 修改对应入口 → 更新方法/运行/数据文档 → 完整测试与 dry-run。
- Step 1、Step 2A 和 Step 2B 的变化尽量分别提交；依赖升级必须使用独立 PR。
- CLI、默认值、波段、metadata 或 registry 变化必须同步测试与 `CHANGELOG.md`。

## 离线门禁

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -q -p no:cacheprovider
python -m py_compile .\gee\runs\2026824\code_step1_jrc_forest_tiles.py .\gee\runs\2026824\code_step2_gmba_treeline.py .\gee\runs\2026824\code_step2b_treeline1km_from_30m.py
git diff --check
git status --short
```

CI 只执行离线测试，不保存 Earth Engine 凭据、不访问私有 Asset、不执行 `--check` 或 `--export`。

## 科学变化

- Step 1 算法、源数据、MMU、格网或波段变化时，使用新的 Step 1 `configuration_hash`，重新生成全部所需瓦片；不得混合不同哈希。
- Step 2 研究域、边缘、Otsu、局地检验或输出 schema 变化时，使用新的 run label 和 Step 2 哈希。
- `step2_validated_upstream_20260831.json` 只适用于其精确绑定的 Asset ID、Step 1 manifest、参数及科学/验证逻辑。任一绑定项变化后，先执行 `--check --revalidate-upstream --deep-check` 并归档证据，再审查更新凭据；不得仅为绕过不匹配而手工改哈希。
- Step 2B 聚合方法、输入/输出格网、`maxPixels` 或实现变化时，使用新的 Step 2B run label、实现 SHA 和聚合哈希；源 Step 2 哈希必须另存，不得冒充新哈希。
- 同步更新 `docs/research/method_decisions.md`、`METHOD_GMBA_REVISED.md` 和 `notes/reproduction-log.md`。
- 新代码不得用 `--resume` 继承旧哈希 Asset，不得用覆盖写入作为常规恢复方式。

## Earth Engine 人工门禁

Cloud Project 固定为 `ee-wsc`。推荐顺序：

1. Step 1 离线 dry-run 和只读 check；
2. 人工确认有效瓦片、纬度诊断、目标集合和任务数；
3. 明确授权后执行有界 Step 1 导出，并验收两个集合；
4. Step 1 全部完成且一次性完整性检查通过，当前固定上游凭据与输入精确匹配；
5. 上游和科学图未变化时，后续 Step 2A 批次只需默认凭据 dry-run；发生变化时才重新执行单山体 live/deep check；
6. 明确授权后执行有界 Step 2A 导出，等待并验收 30 m Asset；
7. Step 2B 只读 diagnose/check；
8. 明确授权后执行有界 Step 2B 导出。监控不得自动启动下一阶段。

任何 `--export` 都必须预先确认输入、输出、范围、任务数和成本。分析 TABLE 固定为 `projects/ee-wsc/assets/Alpine/GMBA_Sayre`，并复核 `hm_fraction >=0.50`、`tree_fraction <=0.90` 和唯一 `GMBA_V2_ID`；不得换用其他 Asset。

## 产物

Git 只保存源码、测试、依赖和文档。registry、错误报告、检查报告、地图、验证数据和导出结果写到仓库外，例如：

```powershell
--registry-dir D:\实验复现\Globaltreeline_artifacts\<YYYYMMDD-run_label>\tasks
```

每批归档 registry、输入/输出 Asset、三阶段配置哈希、Git commit、run label、验收结果和 SHA-256 清单；不得归档凭据。
