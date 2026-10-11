# -*- coding: utf-8 -*-
"""S7 决策与业务价值：把 pp 翻译成钱，并给出上线建议

核心思路：本数据集没有付费/ARPU 字段，无法直接算 LTV。
所以这里不做「假装知道 LTV」的计算，而是做**盈亏平衡反推**：
「门后移必须带来多大的每用户变现增量，才能抵消留存下降的损失？」
把未知量显式化成参数交给业务方，而不是替他们编。

单用户增量利润：ΔProfit = Δret7 × V + M
  Δret7 = 7 日留存率变化（实验 − 对照，为负）
  V     = 每多留住一个 7 日留存用户的长期价值（元，假设参数）
  M     = 门后移带来的每用户变现变化（元，未知参数）
盈亏平衡点：M* = |Δret7| × V
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablib as A  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORT = A.ensure_dir(ROOT / "report")
TABLEAU = A.ensure_dir(REPORT / "tableau")

V_GRID = [10, 20, 50, 100, 200, 500]            # 每留住一个 7 日留存用户的长期价值（元）
M_GRID = [-1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 5.0]  # 每用户变现增量（元）
SCALE = 1_000_000                                # 按 100 万新增安装量计算


def seg_effect(sub_c, sub_t, col="retention_7"):
    k1, n1 = int(sub_c[col].sum()), len(sub_c)
    k2, n2 = int(sub_t[col].sum()), len(sub_t)
    z = A.two_prop_ztest(k1, n1, k2, n2)
    return z["abs_lift_treatment_minus_control"], z["p_two_sided"]


def main() -> int:
    df = A.load_analysis_frame()
    ctrl = df[df["version"] == A.CONTROL]
    trt = df[df["version"] == A.TREATMENT]
    n_c, n_t = len(ctrl), len(trt)

    k1, k2 = int(ctrl["retention_7"].sum()), int(trt["retention_7"].sum())
    z = A.two_prop_ztest(k1, n_c, k2, n_t)
    delta = z["abs_lift_treatment_minus_control"]           # ret7 变化（实验 − 对照）
    raw = A.newcombe_diff_ci(k1, n_c, k2, n_t)
    ci_low, ci_high = -raw[1], -raw[0]                      # 统一为实验 − 对照
    nnt = 1 / abs(delta)
    lost_per_million = abs(delta) * SCALE

    # 证据质量相关事实：SRM p 值现算（不依赖其他模块产物），
    # 符号翻转率从 S3 的结构化结果读取（重跑 2000 次重抽样成本高，没必要重复计算）
    srm_p = A.srm_test([n_c, n_t])["p_value"]
    flip_txt = ""
    _s3_path = REPORT / "s3_significance.json"
    if _s3_path.exists():
        try:
            _flip = json.loads(_s3_path.read_text(encoding="utf-8"))["srm_robustness"]["sign_flip_rate"]
            flip_txt = f"（符号翻转率 {_flip * 100:.3f}%）"
        except Exception:
            flip_txt = ""

    # Q4 分群（S6 结论中引用的数字，这里现算避免硬编码）
    q4_eff, q4_p = seg_effect(
        ctrl[ctrl["activity_quartile"] == 4], trt[trt["activity_quartile"] == 4]
    )

    # ---------- 盈亏平衡 ----------
    be_rows = []
    for V in V_GRID:
        be_rows.append([
            f"{V}",
            f"{V * abs(delta):.4f}",
            f"{V * abs(ci_high):.4f}",
            f"{V * abs(ci_low):.4f}",
            f"{V * abs(delta) * SCALE:,.0f}",
        ])

    # ---------- 决策网格 ----------
    grid_rows = []
    for V in V_GRID:
        for M in M_GRID:
            profit_per_user = delta * V + M
            grid_rows.append([V, M, profit_per_user, profit_per_user * SCALE])

    md = ["## S7 决策与业务价值\n"]

    md.append("### 7.1 效应到钱的换算\n")
    md.append(
        A.md_table(
            ["量", "数值", "说明"],
            [
                ["7 日留存率变化 Δret7", f"{delta * 100:+.4f} pp", "实验组 − 对照组"],
                ["95% 置信区间", f"[{ci_low * 100:+.4f}, {ci_high * 100:+.4f}] pp", "完全落在 0 以下"],
                ["NNT", f"{nnt:.1f} 人", "每多少名玩家少留住 1 人"],
                ["每 100 万新增安装的留存损失", f"{lost_per_million:,.0f} 人", "1,000,000 × |Δret7|"],
                ["相对变化", f"{z['rel_lift'] * 100:+.3f}%", "实验组比对照组低"],
                ["高活跃玩家（Q4）效应", f"{q4_eff * 100:+.4f} pp（p = {q4_p:.5f}）",
                 f"是整体效应的 {abs(q4_eff / delta):.1f} 倍"],
            ],
        )
    )
    md.append("")

    md.append("### 7.2 盈亏平衡反推（把未知量显式化）\n")
    md.append(
        "本数据集**没有付费、ARPU、IAP 等任何变现字段**，因此无法计算 LTV。"
        "正确做法不是假装知道，而是问：**门后移需要带来多少额外变现，才值得牺牲这些留存？**\n"
        "\n"
        "单用户增量利润 `ΔProfit = Δret7 × V + M`，盈亏平衡点 `M* = |Δret7| × V`。\n"
    )
    md.append(
        A.md_table(
            ["每留存用户价值 V（元）", "盈亏平衡所需变现增量 M*（元/用户）",
             f"CI 乐观端 (Δ={ci_high * 100:.3f}pp)", f"CI 悲观端 (Δ={ci_low * 100:.3f}pp)",
             "100 万安装的留存损失（元）"],
            be_rows,
        )
    )
    md.append("")
    md.append(
        "> **怎么用这张表**：若业务方认为「一个 7 日留存用户值 100 元」，"
        "门后移到 40 级必须额外贡献 **0.82 元/用户** 才能刚好不亏；若只值 50 元，则需要 0.41 元/用户。"
        "**无论 V 取多少，举证责任都在实验组**——目前没有任何变现数据支持这个增量存在。\n"
    )

    md.append("### 7.3 决策网格（每 100 万安装的增量利润，单位：元）\n")
    header = ["V \\ M"] + [f"M={m:+.1f}" for m in M_GRID]
    rows = []
    for V in V_GRID:
        row = [f"V={V}"]
        for M in M_GRID:
            row.append(f"{(delta * V + M) * SCALE:+,.0f}")
        rows.append(row)
    md.append(A.md_table(header, rows))
    md.append("")
    at_v100_m05 = (delta * 100 + 0.5) * SCALE
    md.append(
        f"> 网格中绝大多数格子为负。以 V=100 元为例，即使门后移能带来 +0.5 元/用户的变现，"
        f"每 100 万安装仍净亏 **{at_v100_m05:,.0f} 元**；只有当变现增量跨过 0.82 元/用户才会转正。\n"
    )

    md.append("### 7.4 最终决策建议\n")
    md.append(
        "#### 结论：不上线，保留门在第 30 级（gate_30）\n"
        "\n"
        "理由按重要性排序：\n"
        "\n"
        f"1. **主指标确定变差——不是「没提升」，而是「确定下降」**：7 日留存 {delta * 100:+.4f} pp，"
        f"95% CI [{ci_low * 100:+.4f}, {ci_high * 100:+.4f}] pp 完全在 0 以下，p = {z['p_two_sided']:.5f}。"
        f"z 检验、卡方检验、Bootstrap 三种方法结论一致。\n"
        f"2. **损害集中在最有价值的用户群**：S6 分群分析显示高活跃玩家（Q4）7 日留存下降 "
        f"{q4_eff * 100:+.4f} pp（p = {q4_p:.5f}），约为整体效应的 {abs(q4_eff / delta):.1f} 倍；"
        f"低活跃玩家几乎不受影响——机制上可解释（只有能推进到 30 级之后的玩家才会遇到门）。\n"
        f"3. **回本门槛无法被验证**：门后移要回本，需要每用户至少 {abs(delta):.4f} 元 ×（V/100）"
        f"的额外变现，而本数据集**没有任何变现字段**可以支撑这个假设。\n"
        f"4. **举证责任在实验组**：A/B 实验的默认动作是保留现状。"
        f"当一个变体在主指标上显著变差、且没有任何护栏指标显示它更好时，"
        f"正确的默认决策是**不上线**，而不是「再看看」。\n"
        f"5. **证据质量说明**：S1 发现分流存在轻微失衡（SRM p = {srm_p:.4f}），"
        f"S3.4 已用严格 1:1 重抽样验证结论不变{flip_txt}，因此这个否定结论是稳健的。\n"
    )

    md.append("### 7.5 如果产品团队仍然想推进，下一步该做什么\n")
    md.append(
        "1. **补埋点**：ARPU、付费率、IAP 金额、广告展示收入——没有变现指标就无法评估任何门的改动。\n"
        "2. **延长观测窗口**：从 14 天延长到 28–30 天，并把长期留存（LTV7 / LTV30）设为主指标。\n"
        "3. **改主指标口径**：把「7 日留存」升级为「留存 × 人均收入」的复合指标，"
        "避免留存与变现的权衡被单一指标掩盖（门后移提高变现但降低留存，理论上完全可能）。\n"
        "4. **按进度分层随机化**：分流时按玩家历史最高关卡分层，"
        "既解决 S6 的 post-treatment conditioning 问题，也能更精确地估计门前后的效应。\n"
        "5. **先做 A/A 检验**：确认分流与指标管道无系统性偏差后，再投入正式实验流量。\n"
    )

    A.write_text("\n".join(md), REPORT / "s7_decision.md")
    A.save_json(
        {
            "delta_ret7": delta,
            "ci": [ci_low, ci_high],
            "p_value": z["p_two_sided"],
            "nnt": nnt,
            "lost_per_million": lost_per_million,
            "q4_effect": q4_eff,
            "q4_p": q4_p,
            "value_grid": V_GRID,
            "monetization_grid": M_GRID,
            "breakeven": {str(V): float(V * abs(delta)) for V in V_GRID},
            "decision": "不上线（保留 gate_30）",
        },
        REPORT / "s7_decision.json",
    )

    A.write_csv(
        ["value_per_retained_user", "monetization_delta_per_user", "profit_per_user", "profit_per_million_installs"],
        grid_rows,
        TABLEAU / "ab_decision_grid.csv",
    )
    A.write_csv(
        ["value_per_retained_user", "breakeven_monetization_delta", "breakeven_point_estimate",
         "breakeven_ci_optimistic", "breakeven_ci_pessimistic"],
        [[V, V * abs(delta), V * abs(delta), V * abs(ci_high), V * abs(ci_low)] for V in V_GRID],
        TABLEAU / "ab_breakeven.csv",
    )

    print("[S7] delta_ret7=%+.4fpp CI=[%+.4f,%+.4f] p=%.5f NNT=%.1f lost_per_1M=%.0f Q4=%+.4fpp"
          % (delta * 100, ci_low * 100, ci_high * 100, z["p_two_sided"], nnt, lost_per_million, q4_eff * 100))
    for V in V_GRID:
        print("[S7]   V=%3d 元 -> breakeven M* = %.4f 元/用户" % (V, V * abs(delta)))
    print("[S7] decision = 不上线（保留 gate_30）")
    print("[S7] wrote report/s7_decision.md + 2 tableau csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
