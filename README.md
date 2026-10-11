# Cookie Cats 关卡门位置 A/B 实验评估

对《Cookie Cats》手游一次真实随机实验的完整评估。

产品团队把第一道关卡门从 30 级推迟到 40 级，想验证能否提升玩家留存。
数据是 90,189 名玩家的随机分流结果，观测窗口 14 天。

结论：**不建议上线**，保留门在第 30 级。

## 结果

| 指标 | 对照组 gate_30 | 实验组 gate_40 | 差异 | p 值 |
|---|---|---|---|---|
| 用户数 | 44,700 | 45,489 | +789 | — |
| 次日留存率 | 44.82% | 44.23% | −0.59 pp | 0.074 |
| 7 日留存率 | 19.02% | 18.20% | −0.82 pp | 0.0016 |
| 平均回合数 | 52.46 | 51.30 | −1.16 | 0.376 |
| 零活跃玩家占比 | 4.33% | 4.52% | +0.19 pp | 0.169 |

7 日留存差异的 95% 置信区间是 [−1.33, −0.31] 个百分点，完全落在 0 以下。

## 分析过程

整个过程围绕「能不能上线」这个决策展开，每一步解决上一步暴露出来的问题。

### 1. 分流健康度

卡方拟合优度检验得 χ² = 6.9024、p = 0.0086。分流比不是严格 1:1，实验组多了 789 人（0.87%），
落在 0.001 到 0.05 之间的灰色区间。把实验组随机削到与对照组等量、重复 2,000 次，
效应估计从 −0.8201 pp 变成 −0.8212 pp，符号翻转率为 0，结论不受这点失衡影响。

### 2. 指标口径

留存率的分母统一用全部进组玩家，不用「活跃玩家」，避免分母本身被实验影响。
结果表里同时保留分子和分母，方便复算。

### 3. 显著性检验

两比例 z 检验、2×2 卡方检验、1 万次 Bootstrap 假设检验，三种方法结论一致。
7 日留存 z = 3.1644、p = 0.0016；次日留存 p = 0.074。

次日留存这个 p 值需要小心解读：该实验检出对应效应的功效只有 42.7%，
所以正确的说法是「不可判定」，而不是「两组没有差异」。

### 4. 灵敏度

每组 44,700 人时，7 日留存的最小可检测效应是 0.729 pp，比它更小的变化测不出来。
要检测相对 2% 的提升，需要每组 17.2 万人。

### 5. 短期与长期信号

第 1 天的效应是 −0.59 pp（不显著），第 7 天是 −0.82 pp（显著），效应放大了 1.39 倍。
只看第 1 天会得到「略差但问题不大」的错误结论。

### 6. 分群异质性

按活跃度四分位拆分，7 日留存效应依次是 +0.06、−0.05、−1.52、−2.54 个百分点，单调递增。
游戏的门设在 30 级，Q1、Q2 的玩家走不到门口，所以门放在哪对他们没有影响。
14 个检验统一做 BH-FDR 校正后，原始显著的 5 个降为 4 个。

### 7. 业务换算

数据集没有付费字段，算不出 LTV，所以做的是盈亏平衡反推：
如果一个 7 日留存用户值 100 元，门后移需要额外带来 0.82 元/用户才能回本，
而实验里没有任何变现数据能支持这个假设。按点估计，每 100 万安装会少留住 8,201 个留存用户。

### 8. 监控方式的影响

模拟每天查看一次 p 值：14 次查看把假阳性率从 5% 抬到 21.5%。
用 O'Brien-Fleming 边界可以把整体假阳性率控制在 5%，代价是功效从 88.31% 降到 87.47%。
本次实验最终 |Z| = 3.1506，高于序贯边界的 2.0903，结论不变。

## 目录结构

```
ab-cookie-cats/
├── data/
│   └── cookie_cats.csv              # 原始数据（见下方「数据来源」）
├── sql/                             # MySQL 8.0 数仓层
│   ├── 01_ods.sql                   # 原始层：建库建表 + LOAD DATA + 质检
│   ├── 02_dwd.sql                   # 明细层：去重、类型转换、四分位、p95 缩尾
│   ├── 03_ads.sql                   # 应用层：分组汇总 / 分位汇总 / SRM 统计量
│   ├── 04_tableau_extract.sql       # Tableau 导出查询
│   └── README.md                    # 执行方式与核对清单
├── src/
│   ├── ablib.py                     # 统计工具库
│   ├── 99_selftest.py               # 20 项断言，用教科书临界值反验
│   ├── 00_inspect.py                # 数据探查
│   ├── 01_validate_srm.py           # S1
│   ├── 02_metrics.py                # S2
│   ├── 03_significance.py           # S3
│   ├── 04_power_cuped.py            # S4
│   ├── 05_novelty.py                # S5
│   ├── 06_segments_fdr.py           # S6
│   ├── 07_decision.py               # S7
│   ├── 08_sequential_peeking.py     # S8
│   └── run_all.py                   # 一键复现
└── report/
    ├── 实验评估报告.md              # 汇总报告（S1–S8 全文）
    ├── s1_*.md / *.json             # 各模块独立产出
    └── tableau/                     # 15 个看板抽取表（直接喂 Tableau Public）
```

## 运行

```bash
# 依赖：Python 3.9+，仅需 numpy 与 pandas（不需要 scipy / statsmodels / matplotlib）
pip install numpy pandas

# 1) 先跑统计库自校验（确保手写分布函数正确）
python src/99_selftest.py

# 2) 一键跑完 S1–S8 并生成汇总报告
python src/run_all.py
```

输出：

- `report/实验评估报告.md` —— 完整评估报告
- `report/tableau/*.csv` —— 15 个看板抽取表
- `report/s*.json` —— 各模块结构化结果

SQL 层执行方式见 `sql/README.md`（需要 MySQL 8.0，并开启 `local_infile`）。

## 数据来源

Cookie Cats 移动游戏 A/B 实验公开数据集（用户级随机分流，观测期 14 天）：

- Kaggle 原始数据集：[yufengsui/mobile-games-ab-testing](https://www.kaggle.com/datasets/yufengsui/mobile-games-ab-testing)
- Kaggle 副本：[mursideyarkin/mobile-games-ab-testing-cookie-cats](https://www.kaggle.com/datasets/mursideyarkin/mobile-games-ab-testing-cookie-cats)
- 免登录镜像：[yfsui/Mobile-Games-AB-Testing-Project](https://github.com/yfsui/Mobile-Games-AB-Testing-Project)、[Jisha-p/A-B-Testing-Mobile-Game---Cookie-Cat](https://github.com/Jisha-p/A-B-Testing-Mobile-Game---Cookie-Cat)

字段：`userid` / `version`（`gate_30` 对照、`gate_40` 实验）/ `sum_gamerounds` / `retention_1` / `retention_7`。
校验值：行数 90,189，对照组 44,700、实验组 45,489。

## 已知局限

1. 分群变量 `sum_gamerounds` 在实验期内测得，条件化在它上面会带来选择偏倚，
   因此分群梯度是提示性的，不能当成因果证据。严格做法是按实验前历史关卡分层。
2. CUPED 要求协变量在实验前测得，本数据集不满足，S4 只能作为方法演示。
3. 没有日级明细，画不出留存曲线，novelty effect 只能用两个时间点近似。
4. 没有变现字段，S7 做的是盈亏平衡反推，不是 ROI 计算。
5. SRM 处于灰色区间（p = 0.0086），报告中保留披露。
6. 效应只在 14 天窗口内成立，更长期的变化这份数据回答不了。

## 实现说明

- 统计函数全部手写（运行环境没有 scipy / statsmodels），包括卡方上尾、t 分布上尾、
  Wilson 区间、Newcombe 区间、Bootstrap 假设检验、BH-FDR / Holm 校正、CUPED。
  正确性由 `src/99_selftest.py` 用教科书临界值反验，20 项断言全部通过。
- 正态尾概率用 `erfc` 而不是 `2(1-Φ(z))`，避免 |z| > 6 时的浮点相消。
- Bootstrap 用二项等价形式实现：0/1 数据的个体级重采样与 `Binomial(n, p̂)` 分布相同，
  但内存开销小得多（4.5 万样本 × 1 万次重采样，朴素索引矩阵需要数 GB）。
- 序贯边界通过模拟布朗运动路径标定。H0 下统计量序列满足 Cov(Z_j, Z_k) = √(t_j/t_k)，
  比在每个查看点独立抽二项分布更准确。
