# Repository instructions for coding agents

本文件适用于整个仓库。它规定自动化编码代理在本仓库中的工作边界、读取顺序、验证门禁和交付要求。它不是科学方法说明，也不记录某一次任务的进度。

## 1. 项目目标与权威入口

- 项目目标是复现并审慎改造 Liang et al. (2026) 的全球高山树线提取流程。
- 当前唯一受支持的可执行入口是：

  ```text
  gee/runs/2026821/code_region_revised_v2.py
  ```

- 不得新建 `v3.py`、`final.py`、复制版脚本或重新启用历史 tracer、区域合并、2° 分片和 JavaScript 入口。
- 历史代码只用于追溯，不能作为当前实现依据。

## 2. 开始工作前的必读顺序

代理在修改仓库前必须依次读取：

1. 本文件 `AGENTS.md`；
2. `docs/exec-plans/active/task-<三位编号>-<YYYY-MM-DD>.md` 中唯一的活动计划；
3. `docs/research/method_decisions.md`；
4. 与任务相关的实现、测试和运行文档；
5. `CONTRIBUTING.md` 与 `CHANGELOG.md`。

如果文件之间冲突，按以下优先级处理：

1. 用户在当前任务中的明确指令；
2. `docs/research/method_decisions.md` 中状态为“已接受/已冻结”的决定；
3. 当前入口代码与测试共同表达的契约；
4. 运行手册、README 和历史记录。

发现冲突时不得静默选择；应记录冲突并停止会改变科学结果或启动云端任务的操作。

## 3. 角色与授权边界

- 研究者负责最终科学判断、计算成本和 GEE 任务授权。
- ChatGPT Work 可用于文献核查、方法讨论、决策整理和任务定义。
- Codex/编码代理负责检查仓库、实现已批准的修改、运行安全验证并报告证据。
- “讨论方案”“检查代码”不等于授权修改代码；“修改/实现”不等于授权提交 GEE 任务、覆盖 Asset、推送 GitHub 或合并 PR。
- 未获得明确授权时，不得运行 `--prepare-mountains`、`--export`、`task.start()`、`--overwrite-assets` 或其他产生云端写入/费用的操作。
- `--dry-run` 为离线操作；`--check` 虽不提交导出，但会访问 Earth Engine，执行前仍应说明目标山体、项目和预期计算范围。

## 4. 当前科学不变量

完整定义和理由见 `docs/research/method_decisions.md`。除非用户明确重新打开相应决定，否则不得改变以下内容：

- 分析单位是经审核清单选中的 GMBA v2 Standard `MapUnit=Basic` 山体。
- Sayre 31/32 只用于清单阶段选择山体；选中后，正式研究域是完整 GMBA Basic 几何，不再应用 Sayre 像元掩膜或 0.25°有效格网。
- GLAD 冠层高度 `>3 m` 为主结果，`>5 m` 为并行敏感性结果。
- 二值 MMU 使用八邻域和 `ee.Image.pixelArea()`：先删除面积 `<=0.5 ha` 的森林斑块，再填充面积 `<0.5 ha` 的内部非森林间隙。
- 不保留“不填孔”正式分支，不允许通过 CLI 改写固定 MMU 阈值或连通性。
- Zero Crossing 是主边缘方法；Canny 只允许使用独立 run label 做敏感性分析。
- Otsu 按“每个 GMBA × 每个冠层阈值”计算；2000/2020 共用由两年 post-landform 候选合并得到的阈值。
- 一个山体是一个科学统计单元。若内部切片用于计算，不能在每个内部片重新估计 Otsu。

## 5. 代码修改规则

- 先增加或调整能够表达问题的测试，再修改唯一入口。
- 精确 GMBA 几何只应用于正式分析掩膜和最终裁剪；复杂上下文运算应使用经验证的简化几何或其他低复杂度计算域，同时保证完整 GMBA 被覆盖。
- 不得用 `maxPixels`、`tileScale`、批量大小或无依据的参数缩减掩盖算法错误。
- `connectedComponents(maxSize)` 的 `maxSize` 会屏蔽超过窗口尺寸的对象；不得把它描述成面积阈值，也不得声称它完全不影响对象处理语义。
- 每个任务应尽量直接从 prepared Asset 按唯一 `gmba_id_text` 过滤山体，避免把批次 `toList()` 依赖嵌入所有计算图。
- 任何科学方法、默认参数、计算图、输出波段或元数据变化，都必须同步修改：

  - `docs/research/method_decisions.md`（若决定发生变化）；
  - `gee/runs/2026821/METHOD_GMBA_REVISED.md`；
  - `gee/runs/2026821/RUN_REGION.md`；
  - `notes/reproduction-log.md`；
  - `CHANGELOG.md`；
  - 相应测试。

- 不得删除、覆盖或格式化与当前任务无关的用户修改。工作树不干净时，先报告冲突范围。

## 6. Python、GEE 与输出约定

- Python 固定为 3.11.9；依赖版本以 `requirements.txt` 和 `requirements-dev.txt` 为准。
- 所有正式运行固定使用 Cloud Project `ee-wsc`，除非用户明确修改。
- 输出 Asset 默认不得覆盖；恢复运行只允许使用配置哈希完全一致的 `--resume`。
- 当前默认每个山体生成：30 m 树线、1 km 汇总和 30 m QA 三项 Asset。
- QA 连续变量应使用与变量语义相符的金字塔策略；不能对所有 Float QA 波段统一使用 `mode`。
- 配置、输出元数据和 registry 必须能够追溯 Git commit、`configuration_hash`、run label、输入 Asset 和方法版本。

## 7. 验证门禁

每次 PR 至少完成：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -q -p no:cacheprovider
python -m py_compile .\gee\runs\2026821\code_region_revised_v2.py
git diff --check
git status --short
```

还应执行完整参数的离线 dry-run，并确认：

- `ready=true`；
- `missing_requirements=[]`；
- 山体选择范围正确；
- `expected_task_count` 与产品数量一致。

科学计算图变化的在线验证顺序固定为：

1. 指定山体的 `--check`；
2. 对历史失败或高复杂度山体做定向回归；
3. 使用新 run label 运行不超过 10 个山体的 pilot；
4. 验收任务状态、Asset 波段、QA 和科学合理性；
5. 经研究者明确确认后扩大批次。

离线测试、任务配置成功序列化或 Earth Engine 初始化成功，都不能替代服务端执行验证。

## 8. 安全、产物与版本控制

- 不得提交凭据、token、ADC 文件或包含敏感路径的信息。
- registry、错误报告、控制台输出、HTML 地图、验证数据、压缩包和批量导出结果保存在仓库外。
- 不得使用 `git reset --hard`、强制推送或覆盖用户未提交修改。
- 分支使用 `feat/`、`fix/`、`test/`、`docs/` 或 `chore/` 前缀；一个 PR 只解决一个明确问题。
- 不得自行推送、创建/合并 PR 或发布版本，除非用户明确要求。

## 9. 代理交付格式

交付时必须区分：

- 已修改的文件；
- 已确认的事实和验证结果；
- 尚未验证的推测或风险；
- 未执行的在线操作；
- 需要研究者授权的下一步。

不要仅用“测试通过”概括结果；应给出测试数量、dry-run 关键字段以及是否真正执行过 Earth Engine 服务端计算。
