# 逐 GMBA 树线方法决策记录

## 与论文一致的主干

Liang et al. (2026) 的正文方法依次描述：Sayre 高山/稀疏高山域、0.25°格网筛选、冠层高大于 3 m、孔洞填充、删除小于 0.5 ha 的斑块、中值滤波和 zero crossing、谷地排除、年均温 Otsu，以及 300 m × 300 m 窗口中的森林/非森林高程 t 检验。

本实现有一项明确的研究域变更：Sayre 相交清单只负责选择 GMBA 山体；选择完成后，在每个完整 GMBA Basic 多边形内执行后续处理，不再使用 Sayre 31/32 像元掩膜和 0.25°有效格网。5 m 冠层阈值作为 3 m 主结果的并行敏感性分析。

## 固定的 JRC 式二值 MMU 后处理

孔隙和小斑块处理固定为以下顺序，不再保留“不填孔”或可调 0.5 ha 阈值分支：

1. 将 GLAD 冠层高度按阈值二值化；`>3 m` 为主结果，`>5 m` 为敏感性结果。
2. 以八邻域标记森林连通对象，对对象内 `ee.Image.pixelArea()` 求和，仅保留面积 `>0.5 ha` 的森林斑块。
3. 在保留后的二值图上以八邻域标记非森林连通对象，对对象内 `ee.Image.pixelArea()` 求和，填充大森林斑块内部面积 `<0.5 ha` 的非森林间隙。
4. 对清理后的森林二值图进行中值滤波，再执行 Zero Crossing；Canny 只作为独立敏感性运行。

森林斑块恰好为 0.5 ha 时删除，非森林间隙恰好为 0.5 ha 时不填充。`connectedComponents` 和 `reduceConnectedComponents` 的 `maxSize=512` 仅是连通对象计算保护上限，不是面积阈值或孔洞尺度。旧实现的四邻域判定、无面积约束填孔以及 `buffer(-90) + difference` 边界环均已删除。

这里对齐的是 JRC GFC2020 v2 的二值 MMU 后处理规则，不是完整复现 JRC 的森林定义。当前研究仍使用约 30 m 的 GLAD 冠层高度和 3/5 m 阈值；JRC GFC2020 是 10 m 森林土地利用产品，并结合树冠覆盖、树高、农业、城市和扰动等多类数据。

## Otsu 的决定

结论：主分析采用“每个 GMBA、每个冠层阈值、2000/2020 合并的 post-landform 边缘候选”的温度总体，而不是 GMBA 内所有像元。

理由：

- Otsu 的输入应对应待分割对象。此处待分割的是候选树线边缘，不是整个山体背景。
- GMBA 全像元总体会按山体面积和高程带面积加权，谷地及无关裸地可形成主要温度峰，阈值不再专门描述候选树线。
- 先做 landform 筛选可避免明确要排除的谷地边缘影响温度阈值。
- 2000/2020 共用阈值可避免把逐年阈值变化错误解释成树线变化。
- CHELSA 分辨率约为 1 km。先把候选掩膜聚合到 CHELSA 原生格网、每格计一次，可避免一格温度因含有更多 30 m 边缘像元而被伪重复加权。

这是方法学推断，不是对论文未披露细节的事实陈述。论文没有给出 Otsu 的空间总体、最小样本量或退化分布处理方式。

## 推荐的稳健性分析

1. 主结果：3 m、zero crossing、山体级合并 Otsu、单侧 0.05 Welch t 检验。
2. 冠层敏感性：5 m，其他参数不变。
3. 边缘敏感性：Canny，单独 run label，不能覆盖主结果。
4. t 检验敏感性：pooled variance 或 two-sided；报告最终像元数量和高程变化差异。
5. Otsu 诊断：记录候选 CHELSA 格数、直方图桶数、阈值和有效标记；对样本过少或单峰/退化山体不使用静默全局回退。
6. 研究域诊断：抽样比较完整 GMBA 域与 `GMBA ∩ Sayre 31/32` 域，检查低海拔森林边缘是否显著改变 Otsu 阈值和最终树线高程。

## 不能称为“精确复现”的细节

- Liang et al. 没有披露本研究二值森林孔洞的连通规则与面积边界；本实现明确采用 JRC GFC2020 v2 的 MMU 后处理作为方法决定。
- 中值滤波核大小未披露。
- Otsu 空间作用域未披露。
- t 检验的等方差假设、单双侧、最小样本量及窗口边界处理未披露。

固定 MMU 规则直接写入实现、配置哈希和 Asset 元数据，不提供命令行调整。其他可变科学参数仍进入命令行、配置哈希和 Asset 元数据；改变入口代码或任一科学参数都会生成不同的输出 ID。

## 参考

- Liang et al. (2026), *Global elevational shifts and drivers of alpine treelines*, International Journal of Applied Earth Observation and Geoinformation 146, 105088. DOI: 10.1016/j.jag.2026.105088.
- Bourgoin et al. (2026), *GFC2020: a global map of forest land use for year 2020 to support the EU Deforestation Regulation*, Earth System Science Data 18, 1331–1365. DOI: 10.5194/essd-18-1331-2026. https://essd.copernicus.org/articles/18/1331/2026/
- JRC GFC2020 v2 public code source: https://figshare.com/articles/code/Joint_Research_Centre_-_Global_Forest_Cover_for_year_2020_version_2_Code_source/29315528
- Körner et al. (2022), *A global inventory of mountains for bio-geographical applications*, Scientific Data 9, 103. DOI: 10.1038/s41597-022-01256-y.
- Google Earth Engine API: `Image.connectedComponents`, `Image.reduceConnectedComponents`, `Image.pixelArea`, `Image.zeroCrossing`, `Algorithms.CannyEdgeDetector`, `Export.table.toAsset`.
