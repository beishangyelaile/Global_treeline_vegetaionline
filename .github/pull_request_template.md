## 变更目的

<!-- 一个 PR 只描述一个明确目的。 -->

## 变化类型

- [ ] 文档或测试
- [ ] CLI、registry 或运行保护
- [ ] 科学方法、计算图、默认参数或输出 schema
- [ ] 依赖或 CI

## 合并门禁

- [ ] 三个正式入口（Step 1、Step 2A、Step 2B）均可编译；旧 2026821 v2 仅作历史兼容
- [ ] 已运行 `python -m pytest -q -p no:cacheprovider`
- [ ] Step 1/2A dry-run 为 `ready=true`、无缺项且任务数量正确；Step 2B dry-run 为单任务且 `exports_started=false`
- [ ] 已运行 `git diff --check`
- [ ] Git 状态不含缓存、凭据、registry、地图、报告、数据或压缩包
- [ ] CLI/输出契约变化已同步 README、运行手册和测试
- [ ] 科学变化已记录旧/新配置哈希、run label、方法说明和复现日志

## GEE 影响

- [ ] 本 PR 未启动 GEE 任务
- [ ] 若需要后续在线验证，已列明项目、Asset、山体范围、任务数量和人工确认点
- [ ] 未使用 `--overwrite-assets`
