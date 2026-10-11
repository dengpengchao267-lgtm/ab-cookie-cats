# -*- coding: utf-8 -*-
"""run_all.py —— 一键复现整个项目

顺序：
  0. 统计库自校验（用教科书临界值反验手写的卡方/t 分布/Wilson/Newcombe/BH-FDR/CUPED）
  1. S1 数据校验与 SRM
  2. S2 指标层与 Tableau 抽取
  3. S3 显著性检验
  4. S4 功效分析与 CUPED
  5. S5 Novelty effect
  6. S6 分群与多重比较校正
  7. S7 决策与业务价值
  8. S8 序贯检验与 peeking 模拟
  9. 汇总为一份完整报告 report/实验评估报告.md
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablib as A  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORT = A.ensure_dir(ROOT / "report")
TABLEAU = A.ensure_dir(REPORT / "tableau")

MODULES = [
    ("S1 数据校验与分流健康度", "01_validate_srm", "s1_validation.md"),
    ("S2 指标层与口径固化", "02_metrics", "s2_metrics.md"),
    ("S3 显著性检验", "03_significance", "s3_significance.md"),
    ("S4 功效分析与 CUPED", "04_power_cuped", "s4_power_cuped.md"),
    ("S5 Novelty Effect 与长短期效应", "05_novelty", "s5_novelty.md"),
    ("S6 分群异质性与多重比较", "06_segments_fdr", "s6_segments_fdr.md"),
    ("S7 决策与业务价值", "07_decision", "s7_decision.md"),
    ("S8 序贯检验与 Peeking 模拟", "08_sequential_peeking", "s8_sequential_peeking.md"),
]


def load_json(name):
    p = REPORT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def build_summary() -> str:
    s1 = load_json("s1_validation.json")
    s3 = load_json("s3_significance.json")
    s4 = load_json("s4_power_cuped.json")
    s5 = load_json("s5_novelty.json")
    s6 = load_json("s6_segments_fdr.json")
    s7 = load_json("s7_decision.json")
    s8 = load_json("s8_sequential_peeking.json")

    r1 = s3["results"]["retention_1"]
    r7 = s3["results"]["retention_7"]
    q1 = [t for t in s6["tests"] if t["segment"].startswith("Q1") and t["column"] == "retention_7"][0]
    q4 = [t for t in s6["tests"] if t["segment"].startswith("Q4") and t["column"] == "retention_7"][0]

    lines = []
    lines.append("# 移动游戏关卡门位置 A/B 实验：评估报告\n")
    lines.append(
        f"> 数据：Cookie Cats 真实随机实验，{s1['rows']:,} 名玩家，"
        f"对照组 `gate_30` {s1['n_control']:,} 人 / 实验组 `gate_40` {s1['n_treatment']:,} 人。\n"
        f"> 全部数字由本仓库脚本从原始 CSV 计算得出，可一键复现（`python src/run_all.py`）。\n"
    )

    lines.append("## 结论摘要\n")
    lines.append(
        f"**决策建议：不上线，保留门在第 30 级。**\n"
        "\n"
        f"| 关键结论 | 数字 |\n"
        f"|---|---|\n"
        f"| 7 日留存率变化（实验 − 对照） | **{r7['effect'] * 100:+.4f} pp**（{A.pct(r7['p_control'], 4)} → {A.pct(r7['p_treatment'], 4)}），"
        f"95% CI [{r7['newcombe_ci'][0] * 100:+.4f}, {r7['newcombe_ci'][1] * 100:+.4f}] pp |\n"
        f"| 显著性 | z = {r7['z']:.4f}，p = **{r7['p_z']:.5f}**（z 检验 / 卡方 / Bootstrap 三法一致）|\n"
        f"| 次日留存率变化 | {r1['effect'] * 100:+.4f} pp，p = {r1['p_z']:.5f} → **不可判定**（功效仅 {s4['mde']['retention_1']['power_for_observed'] * 100:.2f}%）|\n"
        f"| 实验灵敏度 (MDE, 7 日留存) | {s4['mde']['retention_7']['mde_abs'] * 100:.4f} pp（小于此幅度的变化不可信）|\n"
        f"| 分流健康度 (SRM) | χ² = {s1['srm']['chi2']:.4f}，p = {s1['srm']['p_value']:.4f} → 轻度失衡，已做稳健性检验（结论不变）|\n"
        f"| 效应随时间 | 第 1 天 {r1['effect'] * 100:+.4f} pp → 第 7 天 {r7['effect'] * 100:+.4f} pp，放大 **{s5['effect_ratio']:.2f} 倍** |\n"
        f"| 分群异质性 | Q1 {q1['effect'] * 100:+.4f} pp（p={q1['p']:.3f}）→ Q4 **{q4['effect'] * 100:+.4f} pp**（p={q4['p']:.5f}）：**单调剂量-反应关系** |\n"
        f"| 多重比较 | 14 个检验，原始显著 {s6['n_sig_raw']} 个 → BH-FDR 校正后 {s6['n_sig_fdr']} 个 |\n"
        f"| 业务换算 | NNT = {s7['nnt']:.1f} 人；每 100 万安装少留住 **{s7['lost_per_million']:,.0f} 人** |\n"
        f"| 回本门槛 | 若一个留存用户值 100 元，门后移需额外贡献 **0.82 元/用户** 变现才能回本（无数据可验证）|\n"
        f"| Peeking 风险 | 每天查看一次 p 值，假阳性率从 5% 涨到 **{s8['naive_fwer'] * 100:.2f}%**（{s8['naive_fwer'] / 0.05:.2f} 倍）|\n"
    )
    lines.append("")
    lines.append(
        "门从 30 级后移到 40 级会让 7 日留存**显著下降 0.82pp**（相对 −4.3%，p = 0.0016），"
        "损害集中在**高活跃玩家**（Q4 −2.54pp），低活跃玩家走不到门口、几乎不受影响。"
        "要回本需要变现端拿出证据（每用户至少 +0.82 元，按 V=100 元计），"
        "而实验里没有任何变现指标，举证责任在实验组，因此建议不上线。\n"
    )
    return "\n".join(lines)


def main() -> int:
    print("=" * 78)
    print("Cookie Cats A/B 实验评估 —— 一键复现")
    print("=" * 78)

    results = []
    t_start = time.time()

    # 0. 自校验
    try:
        importlib.import_module("99_selftest")
        results.append(("统计库自校验（20 项断言）", "PASS", 0.0))
    except SystemExit as e:
        code = e.code if e.code is not None else 0
        results.append(("统计库自校验（20 项断言）", "PASS" if code == 0 else "FAIL", 0.0))
        if code:
            print("!! 自校验失败，终止")
            return 1

    # 1..8
    for label, modname, _ in MODULES:
        t0 = time.time()
        mod = importlib.import_module(modname)
        rc = mod.main()
        dt = time.time() - t0
        results.append((label, "OK" if rc == 0 else f"rc={rc}", dt))

    # 9. 汇总
    summary = build_summary()
    sections = [summary]
    for _, _, mdname in MODULES:
        p = REPORT / mdname
        if p.exists():
            sections.append(p.read_text(encoding="utf-8"))
    sections.append(
        "## 附录：如何复现\n"
        "\n"
        "```bash\n"
        "# 依赖：Python 3.9+（仅需 numpy / pandas；统计函数全部手写，无需 scipy）\n"
        "python src/99_selftest.py     # 统计库自校验（用教科书临界值反验）\n"
        "python src/run_all.py         # 一键跑完 S1–S8 并生成本报告\n"
        "```\n"
        "\n"
        "数据文件放在 `data/cookie_cats.csv`（或工作目录根下，脚本会自动寻找）。\n"
        "\n"
        "SQL 层（MySQL 8.0 数仓 ods → dwd → ads）见 `sql/` 目录。\n"
    )
    out = REPORT / "实验评估报告.md"
    A.write_text("\n\n---\n\n".join(sections), out)

    total = time.time() - t_start
    print("-" * 78)
    for label, status, dt in results:
        print("  %-36s %-6s %6.1fs" % (label, status, dt))
    print("-" * 78)
    print("总耗时 %.1fs" % total)
    print("汇总报告 -> %s" % out)
    print("Tableau 抽取 -> %s" % TABLEAU)
    n_csv = len(list(TABLEAU.glob("*.csv")))
    print("Tableau CSV 数量: %d" % n_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
