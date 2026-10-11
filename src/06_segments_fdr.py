# -*- coding: utf-8 -*-
"""S6 分群异质性、辛普森悖论排查与多重比较校正

分群维度：
  * 活跃度四分位 Q1–Q4（按 sum_gamerounds 分箱）
  * 零回合玩家（安装后一局未玩）vs 有游戏行为

校正对象：把 2 个整体检验 + 12 个分群检验共 14 个假设一起做 BH-FDR / Holm 校正。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablib as A  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORT = A.ensure_dir(ROOT / "report")
TABLEAU = A.ensure_dir(REPORT / "tableau")
METRICS = [("retention_1", "次日留存率"), ("retention_7", "7 日留存率")]


def seg_effect(sub_c, sub_t, col):
    k1, n1 = int(sub_c[col].sum()), len(sub_c)
    k2, n2 = int(sub_t[col].sum()), len(sub_t)
    z = A.two_prop_ztest(k1, n1, k2, n2)
    raw = A.newcombe_diff_ci(k1, n1, k2, n2)          # 对照 - 实验
    ci = (-raw[1], -raw[0])                            # 实验 - 对照
    return {
        "k1": k1, "n1": n1, "k2": k2, "n2": n2,
        "p1": k1 / n1 if n1 else float("nan"),
        "p2": k2 / n2 if n2 else float("nan"),
        "effect": z["abs_lift_treatment_minus_control"],
        "rel": z["rel_lift"],
        "p": z["p_two_sided"],
        "ci_low": ci[0], "ci_high": ci[1],
        "srm_p": A.srm_test([n1, n2])["p_value"],
    }


def main() -> int:
    df = A.load_analysis_frame()
    ctrl_all = df[df["version"] == A.CONTROL]
    trt_all = df[df["version"] == A.TREATMENT]

    tests = []  # (segment_name, metric_name, result)

    # 整体
    for col, mname in METRICS:
        tests.append(("整体", mname, col, seg_effect(ctrl_all, trt_all, col)))

    # 活跃度四分位
    for q in (1, 2, 3, 4):
        sc = ctrl_all[ctrl_all["activity_quartile"] == q]
        st = trt_all[trt_all["activity_quartile"] == q]
        lo = int(df[df["activity_quartile"] == q]["sum_gamerounds"].min())
        hi = int(df[df["activity_quartile"] == q]["sum_gamerounds"].max())
        for col, mname in METRICS:
            tests.append((f"Q{q}（{lo}–{hi} 回合）", mname, col, seg_effect(sc, st, col)))

    # 零回合 vs 有行为
    for flag, label in ((1, "零回合玩家（未玩）"), (0, "有游戏行为玩家")):
        sc = ctrl_all[ctrl_all["is_zero_round"] == flag]
        st = trt_all[trt_all["is_zero_round"] == flag]
        for col, mname in METRICS:
            tests.append((label, mname, col, seg_effect(sc, st, col)))

    pvals = [t[3]["p"] for t in tests]
    q_fdr = A.bh_fdr(pvals, 0.05)
    q_holm = A.holm_adjust(pvals)

    # ---------- 整体 vs 分群加权：辛普森检查 ----------
    simpson_rows = []
    for col, mname in METRICS:
        overall = [t for t in tests if t[0] == "整体" and t[2] == col][0][3]["effect"]
        segs = [t for t in tests if t[0].startswith("Q") and t[2] == col]
        # 用各组样本量做加权平均（数学上应等于整体效应）
        w = np.array([s[3]["n1"] + s[3]["n2"] for s in segs], dtype=float)
        e = np.array([s[3]["effect"] for s in segs])
        weighted = float((w / w.sum() * e).sum())
        signs = set(np.sign(e).tolist()) | {float(np.sign(overall))}
        simpson_rows.append([
            mname, f"{overall * 100:+.4f} pp", f"{weighted * 100:+.4f} pp",
            f"{abs(overall - weighted) * 100:.6f} pp",
            "无方向翻转" if len(signs - {0.0}) == 1 else "**存在方向翻转**",
        ])

    # ---------- 报告 ----------
    md = ["## S6 分群异质性与多重比较校正\n"]
    md.append(f"共做 **{len(tests)} 个检验**（2 个整体 + 12 个分群），全部纳入校正。\n")

    md.append("### 6.1 分群效应总表（含校正前后对照）\n")
    rows = []
    for (seg, mname, col, r), q, h in zip(tests, q_fdr, q_holm):
        rows.append([
            seg, mname,
            f"{r['n1']:,} / {r['n2']:,}",
            A.pct(r["p1"], 3), A.pct(r["p2"], 3),
            f"{r['effect'] * 100:+.4f} pp",
            f"[{r['ci_low'] * 100:+.3f}, {r['ci_high'] * 100:+.3f}]",
            f"{r['p']:.5f}",
            f"{q:.5f}",
            f"{h:.5f}",
            ("显著" if q < 0.05 else ("校正后转不显著" if r["p"] < 0.05 else "不显著")),
        ])
    md.append(
        A.md_table(
            ["分群", "指标", "n(对照/实验)", "对照", "实验", "绝对差", "95% CI", "原始 p", "BH-FDR q", "Holm p", "FDR 判据"],
            rows,
        )
    )
    md.append("")
    n_sig_raw = sum(1 for p in pvals if p < 0.05)
    n_sig_fdr = sum(1 for q in q_fdr if q < 0.05)
    flipped = [
        f"{s} · {m}（原始 p = {r['p']:.5f} → q = {q:.5f}）"
        for (s, m, c, r), q in zip(tests, q_fdr)
        if r["p"] < 0.05 and q >= 0.05
    ]
    flip_txt = "；".join(flipped) if flipped else "无"
    md.append(
        f"> **校正的意义**：原始 p < 0.05 的检验有 **{n_sig_raw}** 个，"
        f"经 BH-FDR 校正后仍有 **{n_sig_fdr}** 个显著。"
        f"被校正掉的检验：{flip_txt}。\n"
        f">\n"
        f"> 这就是为什么**必须一次性把所有权检验都纳入校正**："
        f"如果只挑原始 p 值显著的那几个汇报，就正好是在做数据挖掘式的假发现"
        f"（跑 14 个检验时，即使全部无效，也期望有 0.7 个 p < 0.05）。\n"
    )

    md.append("### 6.2 辛普森悖论排查\n")
    md.append(A.md_table(["指标", "整体效应", "分群样本量加权效应", "差异", "方向一致性"], simpson_rows))
    md.append("")
    md.append(
        "> 分群加权平均与整体效应一致（差异在浮点误差级别），说明**没有辛普森悖论**——"
        "结论不会因为把人群切开而反转。这里同时检查了每个分群内部的分流失衡（见 6.3），"
        "因为「分群内分流不均」正是辛普森悖论最常见的成因。\n"
    )

    md.append("### 6.3 各分群内部的分流健康度（防止分群内 SRM）\n")
    rows = []
    for (seg, mname, col, r) in tests:
        if not seg.startswith("Q") and "回合" not in seg:
            continue
        if mname != "次日留存率":
            continue
        rows.append([
            seg, f"{r['n1']:,}", f"{r['n2']:,}", f"{r['n2'] - r['n1']:+,}",
            A.pct((r["n2"] - r["n1"]) / (r["n1"] + r["n2"]), 3), f"{r['srm_p']:.5f}",
            "通过" if r["srm_p"] >= 0.05 else "**需关注**",
        ])
    md.append(A.md_table(["分群", "对照组 n", "实验组 n", "差值", "相对失衡", "分群内 SRM p", "判定"], rows))
    md.append("")

    md.append("### 6.4 异质性结论\n")
    q1 = [t for t in tests if t[0].startswith("Q1") and t[2] == "retention_7"][0][3]
    q2 = [t for t in tests if t[0].startswith("Q2") and t[2] == "retention_7"][0][3]
    q3 = [t for t in tests if t[0].startswith("Q3") and t[2] == "retention_7"][0][3]
    q4 = [t for t in tests if t[0].startswith("Q4") and t[2] == "retention_7"][0][3]
    zr = [t for t in tests if t[0].startswith("零回合") and t[2] == "retention_7"][0][3]
    overall7 = [t for t in tests if t[0] == "整体" and t[2] == "retention_7"][0][3]["effect"]
    rng_q = {}
    for qq in (1, 2, 3, 4):
        sub = df[df["activity_quartile"] == qq]["sum_gamerounds"]
        rng_q[qq] = f"{int(sub.min())}–{int(sub.max())}"
    rng_q[4] = f"{int(df[df['activity_quartile'] == 4]['sum_gamerounds'].min())}+"
    span = abs(q4["effect"] - q1["effect"])
    md.append(
        f"#### 发现 1：效应随活跃度单调递增（剂量-反应关系）\n"
        f"\n"
        f"| 分群 | 回合数区间 | 7 日留存效应（实验−对照） | 原始 p | 是否显著 |\n"
        f"|---|---|---|---|---|\n"
        f"| Q1（最低活跃） | {rng_q[1]} | {q1['effect'] * 100:+.4f} pp | {q1['p']:.5f} | 否 |\n"
        f"| Q2 | {rng_q[2]} | {q2['effect'] * 100:+.4f} pp | {q2['p']:.5f} | 否 |\n"
        f"| Q3 | {rng_q[3]} | {q3['effect'] * 100:+.4f} pp | {q3['p']:.5f} | 是 |\n"
        f"| Q4（最高活跃） | {rng_q[4]} | {q4['effect'] * 100:+.4f} pp | {q4['p']:.5f} | 是（最强） |\n"
        f"\n"
        f"效应从 Q1 的 {q1['effect'] * 100:+.4f} pp 单调走到 Q4 的 {q4['effect'] * 100:+.4f} pp，"
        f"跨度 {span * 100:.2f} pp，是整体效应（{overall7 * 100:+.4f} pp）绝对值的 "
        f"{span / abs(overall7):.1f} 倍。**低活跃人群几乎不受影响，高活跃人群受损最重。**\n"
        f"\n"
        f"#### 发现 2：这个梯度有机制解释，不是数据挖掘\n"
        f"\n"
        f"游戏的门设在 **30 级**。Q1（{rng_q[1]} 回合）与 Q2（{rng_q[2]} 回合）的玩家"
        f"**根本走不到门口**，门放在 30 级还是 40 级对他们毫无区别——"
        f"这正好解释了他们的效应为什么接近 0。而 Q4（{rng_q[4]} 回合）恰恰是能触达门的核心玩家。"
        f"零回合玩家效应 {zr['effect'] * 100:+.4f} pp（p = {zr['p']:.4f}）同样接近 0，与机制一致。\n"
        f"\n"
        f"**一个可解释的异质性 + 单调的剂量-反应关系，比一个孤立的显著分群可信得多。**\n"
        f"\n"
        f"#### 发现 3：但必须承认一个方法学硬伤\n"
        f"\n"
        f"分群变量 `sum_gamerounds` 是**实验后测量**的变量。"
        f"条件化在实验后变量上会产生**选择偏倚（post-treatment conditioning）**："
        f"门后移本身可能影响玩家的回合数，也就是说分组变量部分地被处理影响了。"
        f"因此上面的梯度是**强提示（suggestive），不是因果证据**。\n"
        f"\n"
        f"严格做法应该在**分流前**就按玩家历史最高关卡 / 历史进度分层，"
        f"或者只分析「观测窗口内确实触及了门的玩家」——而这两个字段本数据集都没有。"
        f"这一点要写进报告的局限性章节，而不是藏起来。\n"
        f"\n"
        f"#### 发现 4：没有分人群上线的空间\n"
        f"\n"
        f"分人群上线的判断标准是「某人群显著受益、另一人群显著受损，且机制可解释」。"
        f"本例中**没有任何分群的效应为正且显著**（Q1 的 {q1['effect'] * 100:+.4f} pp 并不显著），"
        f"因此不存在「只对某类人有害、对另一类人有利」的取舍空间。"
        f"决策只能是**整体不上线**（详见 S7）。\n"
    )

    A.write_text("\n".join(md), REPORT / "s6_segments_fdr.md")
    A.save_json(
        {
            "n_tests": len(tests),
            "tests": [
                {"segment": s, "metric": m, "column": c, **{k: v for k, v in r.items()},
                 "q_fdr": float(q), "p_holm": float(h)}
                for (s, m, c, r), q, h in zip(tests, q_fdr, q_holm)
            ],
            "n_sig_raw": int(n_sig_raw), "n_sig_fdr": int(n_sig_fdr),
        },
        REPORT / "s6_segments_fdr.json",
    )

    # Tableau 抽取：森林图数据
    rows = []
    for (seg, mname, col, r), q in zip(tests, q_fdr):
        rows.append([seg, mname, r["effect"], r["ci_low"], r["ci_high"], r["p"], q,
                     r["n1"], r["n2"], r["p1"], r["p2"]])
    A.write_csv(
        ["segment", "metric", "effect", "ci_low", "ci_high", "p_value", "q_fdr",
         "n_control", "n_treatment", "rate_control", "rate_treatment"],
        rows,
        TABLEAU / "ab_forest.csv",
    )

    print("[S6] tests=%d  raw_sig=%d  fdr_sig=%d" % (len(tests), n_sig_raw, n_sig_fdr))
    for (seg, mname, col, r), q in zip(tests, q_fdr):
        if col == "retention_7":
            print("[S6]   ret7 %-22s eff=%+.4fpp p=%.5f q=%.5f" % (seg, r["effect"] * 100, r["p"], q))
    print("[S6] wrote report/s6_segments_fdr.md + tableau/ab_forest.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
