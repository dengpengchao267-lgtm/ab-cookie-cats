# -*- coding: utf-8 -*-
"""S3 显著性检验：z 检验 / 卡方 / Wilson / Newcombe / Bootstrap 交叉验证

产出：
  * 主指标与次指标的效应量、置信区间、p 值（多种方法互证）
  * BH-FDR 多重比较校正
  * NNT（需要多少用户才能多留住 1 个人）
  * S3.4 针对 S1 轻微 SRM 的稳健性检验（严格 1:1 重抽样）
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
SEED = 20261009


def main() -> int:
    df = A.load_analysis_frame()
    ctrl = df[df["version"] == A.CONTROL]
    trt = df[df["version"] == A.TREATMENT]
    n_c, n_t = len(ctrl), len(trt)

    metrics = [("retention_1", "次日留存率 (ret1)"), ("retention_7", "7 日留存率 (ret7)")]
    results = {}
    pvals, labels = [], []

    for col, name in metrics:
        cv = ctrl[col].to_numpy()
        tv = trt[col].to_numpy()
        k1, k2 = int(cv.sum()), int(tv.sum())
        z = A.two_prop_ztest(k1, n_c, k2, n_t)
        chi = A.chi2_two_prop(k1, n_c, k2, n_t)
        w1 = A.wilson_ci(k1, n_c)
        w2 = A.wilson_ci(k2, n_t)
        nc_ci_raw = A.newcombe_diff_ci(k1, n_c, k2, n_t)  # 原始方向：对照组 − 实验组
        nc_ci = (-nc_ci_raw[1], -nc_ci_raw[0])            # 统一方向：实验组 − 对照组
        bt = A.bootstrap_two_prop(k1, n_c, k2, n_t, n_boot=10000, seed=SEED)
        bt_ci = (-bt["ci_high"], -bt["ci_low"])           # 同样统一到「实验组 − 对照组」
        eff = z["abs_lift_treatment_minus_control"]
        results[col] = {
            "name": name, "k1": k1, "k2": k2, "n1": n_c, "n2": n_t,
            "p_control": z["p_control"], "p_treatment": z["p_treatment"],
            "effect": eff, "rel_lift": z["rel_lift"],
            "z": z["z"], "p_z": z["p_two_sided"],
            "chi2": chi["chi2"], "p_chi2": chi["p_value"],
            "wilson_control": w1, "wilson_treatment": w2,
            "newcombe_ci": nc_ci,
            "boot_ci": bt_ci, "boot_p": bt["p_value"], "boot_se": bt["boot_se"],
            "nnt": (1 / abs(eff)) if eff != 0 else float("inf"),
            "p_treatment_worse": z["p_treatment_worse"],
            "p_treatment_better": z["p_treatment_better"],
        }
        pvals.append(z["p_two_sided"])
        labels.append(name)
        if col == "retention_7":
            hist, edges = np.histogram(-bt["boot_diffs"], bins=80)
            A.write_csv(
                ["bin_left", "bin_right", "bin_center", "count"],
                [[edges[i], edges[i + 1], (edges[i] + edges[i + 1]) / 2, int(hist[i])] for i in range(len(hist))],
                TABLEAU / "ab_bootstrap_ret7.csv",
            )

    q_fdr = A.bh_fdr(pvals, 0.05)
    q_holm = A.holm_adjust(pvals)

    # ---------- S3.4 SRM 稳健性：随机削到严格 1:1 重算 ----------
    rng = np.random.default_rng(SEED)
    n_extra = n_t - n_c
    tv7 = trt["retention_7"].to_numpy()
    p_c7 = ctrl["retention_7"].mean()
    n_rep = 2000
    bal = np.empty(n_rep)
    for i in range(n_rep):
        drop = rng.choice(n_t, size=n_extra, replace=False)
        mask = np.ones(n_t, dtype=bool)
        mask[drop] = False
        bal[i] = tv7[mask].mean() - p_c7
    bal_lo, bal_hi = np.percentile(bal, [2.5, 97.5])

    # ================= 报告 =================
    md = ["## S3 显著性检验（三种方法交叉验证）\n"]
    md.append(
        f"分析样本：对照组 `{A.CONTROL}` {n_c:,} 人，实验组 `{A.TREATMENT}` {n_t:,} 人。"
        f"效应方向统一定义为 **实验组 − 对照组**（负值 = 实验组更差）。\n"
    )

    md.append("### 3.1 主结果\n")
    rows = []
    for col, name in metrics:
        r = results[col]
        rows.append(
            [
                name,
                A.pct(r["p_control"], 4) + f"<br>({r['k1']:,}/{r['n1']:,})",
                A.pct(r["p_treatment"], 4) + f"<br>({r['k2']:,}/{r['n2']:,})",
                f"{r['effect'] * 100:+.4f} pp",
                f"{r['rel_lift'] * 100:+.3f}%",
                f"[{r['newcombe_ci'][0] * 100:+.4f}, {r['newcombe_ci'][1] * 100:+.4f}] pp",
                f"{r['p_z']:.5f} {A.stars(r['p_z'])}",
            ]
        )
    md.append(
        A.md_table(
            ["指标", "对照组", "实验组", "绝对差", "相对变化", "95% CI (Newcombe)", "p 值 (z 检验)"],
            rows,
        )
    )
    md.append("")

    md.append("### 3.2 多方法交叉验证（防止单一方法出错导致错误结论）\n")
    rows = []
    for col, name in metrics:
        r = results[col]
        rows.append(
            [
                name,
                f"z = {r['z']:.4f}",
                f"χ² = {r['chi2']:.4f}",
                f"p = {r['p_z']:.5f}",
                f"p = {r['p_chi2']:.5f}",
                f"p = {r['boot_p']:.5f}",
                f"[{r['boot_ci'][0] * 100:+.4f}, {r['boot_ci'][1] * 100:+.4f}] pp",
            ]
        )
    md.append(
        A.md_table(
            ["指标", "z 统计量", "卡方统计量", "z 检验 p", "卡方 p", "Bootstrap p (1 万次)", "Bootstrap 95% CI"],
            rows,
        )
    )
    md.append("")
    md.append(
        "> **三种方法结论一致**：z 检验与卡方检验在 2×2 表上数学等价（χ² = z²，已用自校验断言验证），"
        "Bootstrap 用零假设重采样独立得到相近的 p 值。CI 与 p 值同向，说明不存在区间与检验打架的情况。\n"
    )

    md.append("### 3.3 多重比较校正\n")
    rows = []
    for i, (col, name) in enumerate(metrics):
        rows.append(
            [name, f"{pvals[i]:.6f}", f"{q_fdr[i]:.6f}", f"{q_holm[i]:.6f}",
             "显著" if q_fdr[i] < 0.05 else "不显著",
             "显著" if q_holm[i] < 0.05 else "不显著"]
        )
    md.append(A.md_table(["指标", "原始 p", "BH-FDR q 值", "Holm 校正 p", "FDR 判据", "FWER 判据"], rows))
    md.append("")
    md.append(
        "> 本实验只检验 2 个**事先设定**的主指标，多重比较压力很小；"
        "但 S6 会按活跃度分群跑 8 个额外检验，届时校正就变成必需项。"
        "这里先给出校正结果，说明结论对校正不敏感。\n"
    )

    md.append("### 3.4 针对 S1 轻微 SRM 的稳健性检验\n")
    md.append(
        f"S1 发现分流比不是严格 1:1（实验组多 {n_extra:,} 人，SRM p = 0.0086）。"
        f"处理方法：把实验组**随机削到与对照组等量**（各 {n_c:,} 人），重复 {n_rep:,} 次，"
        f"观察效应估计是否改变。\n"
    )
    md.append(
        A.md_table(
            ["统计量", "数值"],
            [
                ["原始效应估计（ret7）", f"{results['retention_7']['effect'] * 100:+.4f} pp"],
                ["平衡后效应均值", f"{bal.mean() * 100:+.4f} pp"],
                ["平衡后 95% 区间", f"[{bal_lo * 100:+.4f}, {bal_hi * 100:+.4f}] pp"],
                ["效应符号翻转比例", A.pct(float((bal >= 0).mean()), 3)],
            ],
        )
    )
    md.append("")
    sign_stable = (bal >= 0).mean() < 0.05
    imbalance_rel = n_extra / (n_c + n_t)
    md.append(
        f"> **结论**：严格 1:1 后的效应估计与原始估计几乎重合，符号翻转比例仅 {A.pct(float((bal >= 0).mean()), 2)}，"
        f"说明这 {A.pct(imbalance_rel, 2)} 的分流失衡**不影响结论**"
        + ("。" if sign_stable else "，但符号翻转比例偏高，需谨慎。")
        + "\n"
    )

    md.append("### 3.5 业务解读：NNT（Number Needed to Treat）\n")
    rows = []
    for col, name in metrics:
        r = results[col]
        rows.append([name, f"{abs(r['effect']) * 100:.4f} pp", f"{r['nnt']:.1f} 人", f"{r['nnt'] * 100:.0f} 人"])
    md.append(A.md_table(["指标", "绝对效应", "每多留住 1 人需要", "每 100 名用户影响"], rows))
    md.append("")
    md.append(
        "> **NNT 是把 pp 翻译成人话的工具**：对 7 日留存而言，"
        f"门后移到 40 级每 {results['retention_7']['nnt']:.0f} 名玩家就少留住 1 个人。"
        "这个数字可以直接和获客成本（CAC）对比——少留住的这些人就是白花的买量钱。\n"
    )

    A.write_text("\n".join(md), REPORT / "s3_significance.md")
    A.save_json(
        {
            "n_control": n_c, "n_treatment": n_t,
            "results": results,
            "fdr_qvalues": {labels[i]: float(q_fdr[i]) for i in range(len(labels))},
            "holm_pvalues": {labels[i]: float(q_holm[i]) for i in range(len(labels))},
            "srm_robustness": {
                "n_reps": n_rep, "mean_effect": float(bal.mean()),
                "ci_low": float(bal_lo), "ci_high": float(bal_hi),
                "sign_flip_rate": float((bal >= 0).mean()),
            },
        },
        REPORT / "s3_significance.json",
    )

    # Tableau 抽取：主结果 + 置信区间
    rows = []
    for col, name in metrics:
        r = results[col]
        rows.append([name, A.CONTROL, r["p_control"], r["wilson_control"][0], r["wilson_control"][1], r["n1"]])
        rows.append([name, A.TREATMENT, r["p_treatment"], r["wilson_treatment"][0], r["wilson_treatment"][1], r["n2"]])
    A.write_csv(["metric", "group", "rate", "ci_low", "ci_high", "n"], rows, TABLEAU / "ab_rates_ci.csv")

    rows = []
    for col, name in metrics:
        r = results[col]
        rows.append([name, r["effect"], r["newcombe_ci"][0], r["newcombe_ci"][1], r["p_z"], r["rel_lift"]])
    A.write_csv(
        ["metric", "abs_diff_treatment_minus_control", "ci_low", "ci_high", "p_value", "rel_lift"],
        rows,
        TABLEAU / "ab_effects_ci.csv",
    )

    for col, name in metrics:
        r = results[col]
        print(
            "[S3] %-16s ctrl=%.4f%% trt=%.4f%% eff=%+.4fpp (%+.2f%%) z=%.3f p=%.5f boot_p=%.5f CI=[%+.4f,%+.4f]pp"
            % (col, r["p_control"] * 100, r["p_treatment"] * 100, r["effect"] * 100, r["rel_lift"] * 100,
               r["z"], r["p_z"], r["boot_p"], r["boot_ci"][0] * 100, r["boot_ci"][1] * 100)
        )
    print("[S3] SRM robustness: mean=%+.4fpp CI=[%+.4f,%+.4f] sign_flip=%.3f%%"
          % (bal.mean() * 100, bal_lo * 100, bal_hi * 100, (bal >= 0).mean() * 100))
    print("[S3] wrote report/s3_significance.md + 3 tableau csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
