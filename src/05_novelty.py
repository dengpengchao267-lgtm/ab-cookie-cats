# -*- coding: utf-8 -*-
"""S5 Novelty effect 与长短期效应

本数据集没有日级明细，只有两个时间点（第 1 天留存 / 第 7 天留存）和一个 14 天累计行为量，
所以这里用「效应随时间是否放大」+「分布层面是否被厚尾驱动」两条线索来判断短期信号的可信度。
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


def main() -> int:
    df = A.load_analysis_frame()
    ctrl = df[df["version"] == A.CONTROL]
    trt = df[df["version"] == A.TREATMENT]
    n_c, n_t = len(ctrl), len(trt)

    # ---------- 5.1 效应随时间的变化 ----------
    e1 = A.two_prop_ztest(int(ctrl["retention_1"].sum()), n_c, int(trt["retention_1"].sum()), n_t)
    e7 = A.two_prop_ztest(int(ctrl["retention_7"].sum()), n_c, int(trt["retention_7"].sum()), n_t)
    eff1 = e1["abs_lift_treatment_minus_control"]
    eff7 = e7["abs_lift_treatment_minus_control"]
    ratio = eff7 / eff1 if eff1 != 0 else float("nan")

    # ---------- 5.2 回合数分布对比 ----------
    gc, gt = ctrl["sum_gamerounds"], trt["sum_gamerounds"]
    qs = [0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 0.999]
    qrows = []
    for q in qs:
        vc, vt = gc.quantile(q), gt.quantile(q)
        qrows.append([f"p{q * 100:g}", f"{vc:,.0f}", f"{vt:,.0f}", f"{vt - vc:+,.0f}"])

    cap = float(df["sum_gamerounds"].quantile(0.95))
    wc = gc.clip(upper=cap)
    wt = gt.clip(upper=cap)
    welch_cap = A.welch_ttest(wc.to_numpy(), wt.to_numpy())
    welch_raw = A.welch_ttest(gc.to_numpy(), gt.to_numpy())
    mw = A.mannwhitney_u(gc.to_numpy(), gt.to_numpy())
    log_c = np.log1p(gc.to_numpy())
    log_t = np.log1p(gt.to_numpy())
    welch_log = A.welch_ttest(log_c, log_t)
    bm = A.bootstrap_mean_diff(gc.to_numpy(), gt.to_numpy(), n_boot=4000, seed=20261009)

    # 零回合玩家（安装后一局未玩）
    zr = A.two_prop_ztest(int(ctrl["is_zero_round"].sum()), n_c, int(trt["is_zero_round"].sum()), n_t)

    # 剔除 top 0.1% 后再看均值
    thr = float(df["sum_gamerounds"].quantile(0.999))
    tc = gc[gc <= thr]
    tt = gt[gt <= thr]
    welch_trim = A.welch_ttest(tc.to_numpy(), tt.to_numpy())

    # ---------- 报告 ----------
    md = ["## S5 Novelty Effect 与长短期效应\n"]
    md.append(
        "### 5.1 效应随时间是否放大？（两个可得时间点）\n"
    )
    md.append(
        A.md_table(
            ["时间点", "对照组", "实验组", "绝对差（实验−对照）", "相对变化", "p 值"],
            [
                ["第 1 天留存 (ret1)", A.pct(e1["p_control"], 4), A.pct(e1["p_treatment"], 4),
                 f"{eff1 * 100:+.4f} pp", f"{e1['rel_lift'] * 100:+.3f}%", f"{e1['p_two_sided']:.5f} {A.stars(e1['p_two_sided'])}"],
                ["第 7 天留存 (ret7)", A.pct(e7["p_control"], 4), A.pct(e7["p_treatment"], 4),
                 f"{eff7 * 100:+.4f} pp", f"{e7['rel_lift'] * 100:+.3f}%", f"{e7['p_two_sided']:.5f} {A.stars(e7['p_two_sided'])}"],
            ],
        )
    )
    md.append("")
    md.append(
        f"> **关键观察**：负面效应随时间**放大**而不是衰减——"
        f"第 1 天 {eff1 * 100:+.4f} pp（不显著、不可判定），第 7 天 {eff7 * 100:+.4f} pp（显著），"
        f"效应量放大了 {ratio:.2f} 倍。\n"
        f">\n"
        f"> 这与经典的 **novelty effect（新奇效应）方向相反**：novelty effect 指的是"
        f"新方案在头几天因为新鲜感表现虚高、随后回落，即早期指标**高估**效果。"
        f"本例是早期指标**低估损害**——如果只看第 1 天数据，会得出"
        f"「实验组略差但不显著、没什么问题」的错误结论，而这个损害到第 7 天才暴露出来。\n"
        f">\n"
        f"> **业务含义**：留存类实验**绝不能用 1–2 天的早期信号做上线决策**，"
        f"观测窗口必须覆盖业务关心的长期节点（这里至少 7 天）。\n"
    )

    md.append("### 5.2 活跃度（sum_gamerounds）分布对比\n")
    md.append(A.md_table(["分位点", "对照组", "实验组", "差值"], qrows))
    md.append("")
    rows = [
        ["均值（原始）", f"{gc.mean():.3f}", f"{gt.mean():.3f}", f"{welch_raw['mean_diff']:+.3f}",
         f"{welch_raw['p_value']:.4f}", "重尾，均值不稳"],
        [f"均值（缩尾到 p95={cap:.0f}）", f"{wc.mean():.3f}", f"{wt.mean():.3f}", f"{welch_cap['mean_diff']:+.3f}",
         f"{welch_cap['p_value']:.4f}", "抗离群值"],
        ["均值（对数 log1p）", f"{log_c.mean():.3f}", f"{log_t.mean():.3f}", f"{welch_log['mean_diff']:+.3f}",
         f"{welch_log['p_value']:.4f}", "比例化解释"],
        [f"均值（剔除 top 0.1% > {thr:,.0f}）", f"{tc.mean():.3f}", f"{tt.mean():.3f}", f"{welch_trim['mean_diff']:+.3f}",
         f"{welch_trim['p_value']:.4f}", "证明厚尾的影响"],
        ["中位数", f"{gc.median():.0f}", f"{gt.median():.0f}", f"{gt.median() - gc.median():+.0f}",
         f"{mw['p_value']:.4f}（Mann-Whitney）", "非参数"],
        ["零回合玩家占比", A.pct(ctrl["is_zero_round"].mean(), 4), A.pct(trt["is_zero_round"].mean(), 4),
         f"{zr['abs_lift_treatment_minus_control'] * 100:+.4f} pp", f"{zr['p_two_sided']:.5f}", "安装后一局未玩"],
    ]
    md.append(A.md_table(["统计量", "对照组", "实验组", "差值", "p 值", "说明"], rows))
    md.append("")
    sig_list = [welch_raw["p_value"], welch_cap["p_value"], welch_log["p_value"], welch_trim["p_value"], mw["p_value"]]
    all_ns = all(p >= 0.05 for p in sig_list)
    sig_txt = "全部不显著" if all_ns else "部分显著"
    md.append(
        f"> **结论：活跃度差异{sig_txt}，但点估计方向一致。**\n"
        f">\n"
        f"> 原始均值 p = {welch_raw['p_value']:.4f}、缩尾均值 p = {welch_cap['p_value']:.4f}、"
        f"对数均值 p = {welch_log['p_value']:.4f}、剔除 top 0.1% 后 p = {welch_trim['p_value']:.4f}，"
        f"均未达到 0.05；唯一逼近临界的是非参数 Mann-Whitney（p = {mw['p_value']:.4f}，"
        f"优势概率 P(对照 > 实验) = {mw['prob_control_greater']:.4f}）。"
        f"所有点估计方向都是「对照组略高」，但没有一项构成显著证据。\n"
        f">\n"
        f"> **这个「不显著」本身是有业务含义的发现**：门后移造成的损害集中在**留存**"
        f"（玩家还回不回来），而**不是活跃强度**（回来之后玩多久）。"
        f"受影响的玩家不是「玩得少了」，而是「不回来了」——"
        f"前者可以靠运营活动拉回，后者是真实流失，两者的处置手段完全不同。\n"
        f">\n"
        f"> **关于那条 49,854 回合的极端记录**：它把原始均值抬高、也把方差撑得极大，"
        f"导致均值类检验的功效很低，所以「均值不显著」不能简单读成「两组一样」。"
        f"缩尾、对数、非参数三种处理方式给出同向结果，说明结论**不是被单个异常用户驱动**；"
        f"但同样**没有足够证据宣称活跃度存在差异**，结论是证据不足。\n"
        f">\n"
        f"> 均值差（对照组 − 实验组）Bootstrap 95% 区间 = [{bm['ci_low']:+.3f}, {bm['ci_high']:+.3f}] 回合，"
        f"区间宽度跨越 0，与上面的判断一致。\n"
    )

    md.append("### 5.3 S5 结论与局限\n")
    md.append(
        f"- **短期指标低估损害**：第 1 天不显著、第 7 天显著，效应量放大 {ratio:.2f} 倍；"
        f"若按第 1 天决策会放行一个有害方案。\n"
        f"- **活跃度方向一致但不显著**：实验组在原始均值、缩尾均值、对数均值、"
        f"非参数检验上点估计都更低，但没有任何一项达到显著水平（最低的是 Mann-Whitney p = {mw['p_value']:.4f}）。"
        f"因此可以说「损害体现在留存上」，但不能说「实验组玩得更少」。\n"
        f"- **局限（必须说明）**：本数据集只有 14 天累计行为量，没有日级明细，"
        f"因此无法画出真正的留存曲线或逐日活跃曲线，只能用两个时间点近似「效应衰减/放大」趋势。"
        f"若要做严格的 novelty effect 检测，需要**日级活跃与留存明细**，"
        f"并用「实验期 vs 实验后观测期」对照或 CUSUM 类方法。\n"
    )

    A.write_text("\n".join(md), REPORT / "s5_novelty.md")
    A.save_json(
        {
            "effect_ret1": eff1, "effect_ret7": eff7, "effect_ratio": ratio,
            "welch_raw": {k: v for k, v in welch_raw.items()},
            "welch_winsorized": {k: v for k, v in welch_cap.items()},
            "welch_log": {k: v for k, v in welch_log.items()},
            "welch_trimmed": {k: v for k, v in welch_trim.items()},
            "mannwhitney": mw,
            "zero_round": {"rate_control": float(ctrl["is_zero_round"].mean()),
                           "rate_treatment": float(trt["is_zero_round"].mean()),
                           "p_value": zr["p_two_sided"]},
            "bootstrap_mean_diff_ci": [bm["ci_low"], bm["ci_high"]],
            "gamerounds_cap_p95": cap,
        },
        REPORT / "s5_novelty.json",
    )

    # Tableau 抽取：分位点曲线
    rows = []
    for q in (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 0.999):
        rows.append([A.CONTROL, q, gc.quantile(q)])
        rows.append([A.TREATMENT, q, gt.quantile(q)])
    A.write_csv(["group", "quantile", "gamerounds"], rows, TABLEAU / "ab_gamerounds_quantiles.csv")

    rows = [
        ["mean_raw", A.CONTROL, gc.mean(), welch_raw["p_value"]],
        ["mean_raw", A.TREATMENT, gt.mean(), welch_raw["p_value"]],
        ["mean_winsorized_p95", A.CONTROL, wc.mean(), welch_cap["p_value"]],
        ["mean_winsorized_p95", A.TREATMENT, wt.mean(), welch_cap["p_value"]],
        ["median", A.CONTROL, gc.median(), mw["p_value"]],
        ["median", A.TREATMENT, gt.median(), mw["p_value"]],
    ]
    A.write_csv(["statistic", "group", "value", "p_value"], rows, TABLEAU / "ab_gamerounds_stats.csv")

    print("[S5] effect ret1=%+.4fpp (p=%.4f)  ret7=%+.4fpp (p=%.5f)  ratio=%.2f"
          % (eff1 * 100, e1["p_two_sided"], eff7 * 100, e7["p_two_sided"], ratio))
    print("[S5] gamerounds mean raw %s=%.3f %s=%.3f p=%.4f | winsorized p=%.4f | MW p=%.5f | zero-round p=%.4f"
          % (A.CONTROL, gc.mean(), A.TREATMENT, gt.mean(), welch_raw["p_value"],
             welch_cap["p_value"], mw["p_value"], zr["p_two_sided"]))
    print("[S5] bootstrap mean-diff CI = [%+.3f, %+.3f]" % (bm["ci_low"], bm["ci_high"]))
    print("[S5] wrote report/s5_novelty.md + 2 tableau csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
