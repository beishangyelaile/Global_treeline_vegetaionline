# 逐 GMBA 树线方法决策记录

## 与论文一致的主干

Liang et al. (2026) 的正文方法依次描述：Sayre 高山/稀疏高山域、0.25°格网筛选、冠层高大于 3 m、孔洞填充、删除小于 0.5 ha 的斑块、中值滤波和 zero crossing、谷地排除、年均温 Otsu，以及 300 m × 300 m 窗口中的森林/非森林高程 t 检验。

本实现有一项明确的研究域变更：Sayre 相交清单只负责选择 GMBA 山体；选择完成后，在每个完整 GMBA Basic 多边形内执行后续处理，不再使用 Sayre 31/32 像元掩膜和 0.25°有效格网。5 m 冠层阈值作为 3 m 主结果的并行敏感性分析。

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
5. 孔洞尺度：至少选择多个最大尺寸进行小样本试验。GEE 参数限制的是连通对象宽/高，不等同于孔洞面积。
6. Otsu 诊断：记录候选 CHELSA 格数、直方图桶数、阈值和有效标记；对样本过少或单峰/退化山体不使用静默全局回退。
7. 研究域诊断：抽样比较完整 GMBA 域与 `GMBA ∩ Sayre 31/32` 域，检查低海拔森林边缘是否显著改变 Otsu 阈值和最终树线高程。

## 不能称为“精确复现”的细节

- 文中引用的 hole-filling 参考文献讨论的是 SRTM 高程空洞填补，并没有给出本研究二值森林孔洞的连通规则与尺度。
- 中值滤波核大小未披露。
- Otsu 空间作用域未披露。
- t 检验的等方差假设、单双侧、最小样本量及窗口边界处理未披露。

因此，相关参数全部进入命令行、配置哈希和 Asset 元数据；改变任一科学参数都会生成不同的输出 ID。

## 参考

- Liang et al. (2026), *Global elevational shifts and drivers of alpine treelines*, International Journal of Applied Earth Observation and Geoinformation 146, 105088. DOI: 10.1016/j.jag.2026.105088.
- Körner et al. (2022), *A global inventory of mountains for bio-geographical applications*, Scientific Data 9, 103. DOI: 10.1038/s41597-022-01256-y.
- Google Earth Engine API: `Image.connectedComponents`, `Image.zeroCrossing`, `Algorithms.CannyEdgeDetector`, `Export.table.toAsset`.
