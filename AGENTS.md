# Repository instructions for coding agents

本文件适用于整个仓库，规定编码代理的读取顺序、科学边界、云端授权和交付要求。

## 1. 正式入口

当前受支持的工作流由三个独立入口组成：

```text
gee/runs/2026824/code_step1_jrc_forest_tiles.py
gee/runs/2026824/code_step2_gmba_treeline.py
gee/runs/2026824/code_step2b_treeline1km_from_30m.py
```

- Step 1 从 2000/2020 GLAD 冠层高度生成连续的 3 m、5 m 二值森林瓦片。
- Step 2A 只读取 Step 1 的 ImageCollection，在经 Sayre/WorldCover 规则筛选的逐山体完整 GMBA Basic 几何内生成 `treeline30m` 和 `qa30m`。
- Step 2B 只读取已经完成并验收的 `treeline30m` Asset，生成 30 arc-second `treeline1km`。
- `gee/runs/2026821/code_region_revised_v2.py` 仅保留为历史兼容入口，不再代表当前正式架构。
- 不得新建 `v3.py`、`final.py` 等版本副本；架构演进直接修改上述入口和对应测试。

## 2. 修改前必读顺序

1. `AGENTS.md`；
2. `docs/exec-plans/active/task-<编号>-<日期>.md` 中唯一活动计划；
3. `docs/research/method_decisions.md`；
4. 相关实现、测试、方法和运行文档；
5. `CONTRIBUTING.md` 与 `CHANGELOG.md`。

冲突优先级为：当前用户明确指令 → 已接受的方法决定 → 入口与测试共同表达的契约 → 运行文档与历史。科学结果或云端状态可能改变时，不得静默消解冲突。

## 3. 授权边界

- 讨论/检查不等于授权改代码；改代码不等于授权访问 GEE 或提交任务。
- `--dry-run` 必须离线；`--check` 可读 GEE，但执行前说明项目、Asset 和计算范围。
- 未获得明确授权，不得执行 `--export`、`task.start()`、删除/覆盖 Asset、推送 GitHub、创建或合并 PR。
- 默认 Cloud Project 为 `ee-wsc`；不得猜测缺失 Asset ID。
- Step 2 分析 TABLE 固定为 `projects/ee-wsc/assets/Alpine/GMBA_Sayre`；代码必须复核其类型、唯一 ID 和筛选字段，不得替换或猜测其他 Asset。

## 4. 当前科学不变量

### Step 1

- 输入固定为真实的 GLAD 2000/2020 冠层高度；严格使用 `height > 3 m` 和 `height > 5 m`。
- 保留 GLAD 有效掩膜；不得把覆盖范围外无条件恢复为 0。
- GMBA Basic 只筛选需要导出的 10°×10°瓦片，不能裁剪或掩膜森林计算图。
- 固定执行 JRC 式顺序：第一次八邻域计数 → 填充面积 `<=5000 m²` 的非森林小间隙 → 第二次八邻域计数 → 保留面积 `>=5000 m²` 的森林。
- 面积固定为 `connectedPixelCount × ee.Image.pixelArea()`，两次 `maxSize=500`；不使用对象标签/对象内面积归约，也不保留 `maxSize=50` 方案。
- 处理顺序、八邻域、0.5 ha 规则和 `maxSize=500` 与 JRC 公开源码一致，但本项目的输入、分辨率和森林定义不同，不能称为完整复现 JRC 森林产品。

### Step 2A

- 只读取 `Global_tree_3m`、`Global_tree_5m`，不得再次读取原始冠层高度或执行二值/MMU处理。
- 必须先对森林瓦片 mosaic，再中值滤波和 Laplacian 8 邻域 Zero Crossing，最后才应用分析域。
- 入选规则固定为 `hm_fraction >=0.50` 且 `tree_fraction <=0.90`；后者来自 ESA WorldCover 2021 `Map` 波段 Class 10。
- TABLE 的几何保留入选山体的完整 GMBA Basic 范围；候选中心、森林样本和非森林样本均限于该几何，不使用山体 buffer 或 0.25°格网。
- Otsu 按“每个山体 × 每个冠层阈值”计算，合并 2000/2020 post-landform 候选；每个 CHELSA 原生格只计一次，同一阈值用于两年；退化时标记无效，不使用全局回退。
- 局地检验固定为 300 m 窗口内单侧 Welch t 检验。
- 创建任何 Step 2 任务前必须通过 Step 1 瓦片、波段、哈希、格网和当前山体覆盖完整性检查。
- 默认产品固定为 `treeline30m qa30m`。旧完整计算图的 direct `treeline1km` 仅允许单独显式选择作对照，不能与 Step 2A 产品同批提交。

### Step 2B

- 像元输入只能是已物化的 `treeline30m`；不得重新读取或重做 Step 2A 的森林、温度、DEM、landform、Otsu、邻域或 Welch 计算。
- 源任务必须为 `COMPLETED`，且 Asset 必须存在、非空、四波段顺序固定、与全局 0.00025°格网对齐，并匹配 mountain ID、run label、配置哈希和来源 Git commit。
- 四个源波段必须合并后只执行一次 `reduceResolution(mean, bestEffort=False, maxPixels=2048)`，再显式投影到固定 30 arc-second 格网；两个变化率均除以 20。
- Step 2B 使用独立 workflow、实现指纹、配置哈希、run label 和子 Asset 名。不得用普通 Step 2 `--resume` 重跑旧 direct 1 km 图，也不得覆盖旧 Asset。

## 5. 修改与验证

- 先写或调整测试，再修改入口；行为、波段、CLI、元数据或方法变化必须同步文档和复现日志。
- Python 仓库基线为 3.11.9；GEE 本机检查使用项目配置的 GEEMu 环境。
- 最低离线门禁：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m pytest -q -p no:cacheprovider
python -m py_compile .\gee\runs\2026824\code_step1_jrc_forest_tiles.py .\gee\runs\2026824\code_step2_gmba_treeline.py .\gee\runs\2026824\code_step2b_treeline1km_from_30m.py
git diff --check
git status --short
```

- 在线顺序为：只读 Step 1 check → 完成并验收 Step 1 → Step 2A check/有界导出 → 等待并验收 30 m Asset → Step 2B diagnose/check → 获得明确授权后有界导出。监控不得自动启动下一阶段。
- 编译、离线测试、图序列化或 GEE 初始化成功均不能替代服务端执行验证。

## 6. 安全、产物和版本控制

- 不提交凭据、ADC、token、registry、报告、HTML 地图、验证数据或批量导出结果。
- 默认不覆盖 Asset；恢复只允许配置哈希一致的 `--resume`。
- 不得删除、覆盖或格式化无关用户修改；不得使用破坏性 Git 操作或 force push。
- 未经明确要求不得提交、推送、创建 PR 或发布版本。

## 7. 交付要求

交付必须分别列明：修改文件、确认事实、测试/dry-run/check 结果、尚未验证风险、未执行的在线写入，以及下一步需要研究者提供的 Asset 或授权。
