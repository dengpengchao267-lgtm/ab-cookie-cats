# -*- coding: utf-8 -*-
"""S4 功效分析与 CUPED 方差缩减

分析内容：
  1. 这个实验的灵敏度够吗？（MDE / 所需样本量 / 已达到的功效）
  2. 如果重做，能不能用 CUPED 把方差压下来？——含「协变量能不能用」的偏误判断

CUPED 成立有两个前提：
  (a) 协变量必须是**实验前**（pre-treatment）变量；
  (b) 协变量与指标相关（ρ 越大，方差缩减 ρ² 越大）。
本数据集只有实验期内字段，因此这里做的是**方法演示 + 误用后果的量化**，不作为正式结论。
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
    n_arm = min(n_c, n_t)

    metrics = [("retention_1", "次日留存率 (ret1)"), ("retention_7", "7 日留存率 (ret7)")]

    # ---------- 4.1 MDE 与已达到的功效 ----------
    mde_rows, power_rows = [], []
    mde_store = {}
    for col, name in metrics:
        p_base = float(df[col].mean())
        mde = A.mde_abs(p_base, n_arm)
        obs = float(trt[col].mean() - ctrl[col].mean())
        mde_store[col] = {"baseline": p_base, "mde_abs": mde, "observed": obs,
                          "power_for_observed": A.achieved_power(p_base, abs(obs), n_arm)}
        mde_rows.append([name, A.pct(p_base, 4), f"{n_arm:,}", f"{mde * 100:.4f} pp", f"{mde / p_base * 100:.3f}%"])
        power_rows.append([name, f"{obs * 100:+.4f} pp", f"{A.achieved_power(p_base, abs(obs), n_arm) * 100:.2f}%"])

    # ---------- 4.2 样本量需求 ----------
    need_rows = []
    for col, name in metrics:
        p_base = float(df[col].mean())
        for label, delta in [("绝对 +0.5 pp", 0.005), ("绝对 +1.0 pp", 0.010), ("绝对 +1.5 pp", 0.015)]:
            need = A.n_required(p_base, delta)
            need_rows.append([name, label, f"{need:,}", f"{need * 2:,}",
                              "现有样本足够" if n_arm >= need else "现有样本不足"])
        for rel in (0.02, 0.05):
            delta = p_base * rel
            need = A.n_required(p_base, delta)
            need_rows.append([name, f"相对 +{rel * 100:.0f}%（{delta * 100:.3f} pp）", f"{need:,}", f"{need * 2:,}",
                              "现有样本足够" if n_arm >= need else "现有样本不足"])

    # ---------- 4.3 CUPED ----------
    y = df["retention_7"].to_numpy(dtype=float)
    y1 = df["retention_1"].to_numpy(dtype=float)
    gr = df["sum_gamerounds"].to_numpy(dtype=float)
    gr_log = np.log1p(gr)
    arm_t = df["is_treatment"].to_numpy(dtype=bool)
    raw_effect = float(y[arm_t].mean() - y[~arm_t].mean())

    def build(vid, cov_name, x, cov_test, note):
        r = A.cuped_adjust(y, x)
        adj = r["y_adj"]
        w = A.welch_ttest(adj[~arm_t], adj[arm_t])
        adj_effect = float(w["mean_treatment"] - w["mean_control"])
        return {
            "id": vid, "covariate": cov_name, "theta": r["theta"], "rho": r["rho"],
            "var_reduction": r["var_reduction"], "multiplier": r["sample_multiplier"],
            "raw_effect": raw_effect, "adj_effect": adj_effect,
            "attenuation": (abs(raw_effect) - abs(adj_effect)) / abs(raw_effect),
            "adj_p": w["p_value"], "cov_p": cov_test, "note": note,
            "is_pre_treatment": False,
        }

    cov1_p = A.two_prop_ztest(int(ctrl["retention_1"].sum()), n_c, int(trt["retention_1"].sum()), n_t)["p_two_sided"]
    cov2_t = A.welch_ttest(gr_log[~arm_t], gr_log[arm_t])
    variants = [
        build("V1", "retention_1（第 1 天留存）", y1, cov1_p, "实验期内第 1 天测得"),
        build("V2", "log1p(sum_gamerounds)（14 天总回合）", gr_log, cov2_t["p_value"], "实验期内 14 天累计测得"),
    ]
    v1, v2 = variants

    # ---------- 报告 ----------
    md = ["## S4 功效分析与 CUPED 方差缩减\n"]

    md.append("### 4.1 这个实验能测出多大的效应？（MDE）\n")
    md.append(A.md_table(["指标", "基线留存率", "每组样本量", "最小可检测效应 MDE", "相对基线"], mde_rows))
    md.append("")
    md.append(A.md_table(["指标", "本次实测效应", "检出该效应所需的功效 (power)"], power_rows))
    md.append("")
    m = mde_store["retention_7"]
    m1 = mde_store["retention_1"]
    md.append(
        f"> 在 α=0.05、power=0.8 的标准设定下，这次实验只能可靠捕捉 7 日留存 "
        f"{m['mde_abs'] * 100:.3f} pp 以上的变化（相对 {m['mde_abs'] / m['baseline'] * 100:.2f}%）。"
        f"实测效应 {m['observed'] * 100:+.4f} pp 已超过这条线，与 S3 的显著结论自洽。\n"
        f">\n"
        f"> 次日留存的实测效应是 {m1['observed'] * 100:+.4f} pp，"
        f"而现有样本检出对应效应的功效只有 **{m1['power_for_observed'] * 100:.2f}%**。"
        f"所以「ret1 不显著」的正确解读是**不可判定（inconclusive）**，"
        f"即样本不足以判定它有没有影响，而**不是**「两组次日留存没有差异」。\n"
    )

    md.append("### 4.2 要检测指定效应需要多少样本？\n")
    md.append(A.md_table(["指标", "目标效应", "每组需样本", "总需样本", "与现有样本比较"], need_rows))
    md.append("")
    # 样本量规划的文案必须从计算结果生成，不能写死（写死会在换数据时变成错误陈述）
    p7 = mde_store["retention_7"]["baseline"]
    need_rel2 = A.n_required(p7, p7 * 0.02)
    need_rel5 = A.n_required(p7, p7 * 0.05)
    md.append(
        f"> **给下一次实验的设计建议**：7 日留存相对提升 2%（{p7 * 0.02 * 100:.3f} pp）需要每组 "
        f"**{need_rel2:,}** 人（总量 {need_rel2 * 2:,}），是本次样本量的 **{need_rel2 / n_arm:.1f} 倍**，"
        f"远超现有规模；相对 5% 的提升只需每组 {need_rel5:,} 人，现有样本就够。"
        f"所以设计实验时必须先问业务方「值得上线的最小提升是多少」，再倒推样本量与实验周期——"
        f"而不是先跑两周再看结果。\n"
    )

    md.append("### 4.3 CUPED 方差缩减（方法演示 + 误用后果量化）\n")
    rows = []
    for v in variants:
        rows.append([
            v["id"], v["covariate"], f"{v['rho']:.4f}", f"{v['var_reduction'] * 100:.2f}%",
            f"×{v['multiplier']:.4f}", "否（实验期内）", f"{v['cov_p']:.5f}",
        ])
    md.append(
        A.md_table(
            ["方案", "协变量 X", "相关系数 ρ", "方差缩减 ρ²", "等效样本量", "是实验前变量？", "两组协变量差异 p"],
            rows,
        )
    )
    md.append("")
    md.append("#### 调整前后的效应估计对比\n")
    rows = []
    for v in variants:
        rows.append([
            v["id"], f"{v['raw_effect'] * 100:+.4f} pp", f"{v['adj_effect'] * 100:+.4f} pp",
            f"{v['attenuation'] * 100:.2f}%", f"{v['adj_p']:.5f}",
        ])
    md.append(A.md_table(["方案", "未调整效应 (ret7)", "CUPED 调整后效应", "效应被吃掉比例", "调整后 p 值"], rows))
    md.append("")
    md.append(
        f"**观察到的规律**：原始效应 {raw_effect * 100:+.4f} pp → V1 调整后 {v1['adj_effect'] * 100:+.4f} pp"
        f"（被吃掉 {v1['attenuation'] * 100:.1f}%）→ V2 调整后 {v2['adj_effect'] * 100:+.4f} pp"
        f"（被吃掉 {v2['attenuation'] * 100:.1f}%）。\n"
        f"\n"
        f"V2 的 ρ 是 V1 的 {v2['rho'] / v1['rho']:.2f} 倍，效应被吃掉的比例是 V1 的 "
        f"{v2['attenuation'] / v1['attenuation']:.2f} 倍——**两者几乎同比例放大**。"
        f"这不是巧合：协变量越贴近结果（ρ 越高），它携带的处理效应越多，"
        f"调整时被当成噪声扣掉的部分也越多。\n"
    )

    md.append("#### CUPED 的有效性判断\n")
    md.append(
        f"CUPED 成立需要一个硬前提：**协变量必须是实验前（pre-treatment）变量**。"
        f"本数据集的协变量都落在实验期内（V1 是第 1 天、V2 是 14 天累计），"
        f"因此**两个方案都不满足前提**，只能作为方法演示，不能作为正式结论。\n"
        f"\n"
        f"- **V1（ρ = {v1['rho']:.3f}，方差缩减 {v1['var_reduction'] * 100:.1f}%）**："
        f"次日留存与 7 日留存高度相关，压缩方差的效果真实存在；"
        f"但两组次日留存差异 p = {v1['cov_p']:.4f}，虽然没到 0.05 显著，方向上实验组更低，"
        f"说明这个协变量可能已经带上了部分处理效应。\n"
        f"- **V2（ρ = {v2['rho']:.3f}，方差缩减 {v2['var_reduction'] * 100:.1f}%）**："
        f"方差缩减看起来漂亮得多（等效样本量 ×{v2['multiplier']:.2f}），"
        f"但 14 天总回合数本身就是实验结果的一部分，用它做 CUPED 属于**方法误用**——"
        f"上表的效应衰减数据就是证据。\n"
        f"\n"
        f"> **小结**：CUPED 能降方差，但用实验后变量做协变量"
        f"**会系统性低估效应，而且低估幅度随 ρ² 同步放大**。"
        f"所以真实 A/B 平台必须在分流前就记录好 pre-period 协变量。\n"
    )

    md.append("#### 给下一版实验的改进清单\n")
    md.append(
        "1. **分流前记录协变量**：实验前 7 / 14 日活跃天数、历史会话数、历史付费额、注册渠道。\n"
        "2. **用这些变量做 CUPED**：经验上 ρ 落在 0.2–0.5，方差缩减 4%–25%，"
        "等效样本量 ×1.04–×1.33，等于不花额外流量就把 MDE 压低 2%–13%。\n"
        "3. **先做 A/A 检验**：正式实验前用 1%–5% 流量跑一次 A/A，"
        "确认分流均匀、指标无系统性偏差，再放量。\n"
        "4. **固定观测窗口**：留存类指标至少观测 7 天，避免用 1–2 天的早期信号做长周期决策。\n"
    )

    A.write_text("\n".join(md), REPORT / "s4_power_cuped.md")
    A.save_json(
        {
            "n_arm": n_arm,
            "mde": mde_store,
            "cuped_variants": variants,
            "cuped_requirement": "CUPED 要求协变量为实验前变量；本数据集不满足，仅作方法演示",
        },
        REPORT / "s4_power_cuped.json",
    )

    # Tableau 抽取
    rows = []
    for col, name in metrics:
        p_base = float(df[col].mean())
        for n_test in (10000, 20000, 30000, 44700, 60000, 90000, 150000, 250000, 400000):
            rows.append([name, n_test, A.mde_abs(p_base, n_test)])
    A.write_csv(["metric", "n_per_arm", "mde_abs"], rows, TABLEAU / "ab_mde_curve.csv")

    rows = [
        [v["id"], v["covariate"], v["rho"], v["var_reduction"], v["multiplier"],
         v["raw_effect"], v["adj_effect"], v["attenuation"]]
        for v in variants
    ]
    A.write_csv(
        ["variant", "covariate", "rho", "var_reduction", "sample_multiplier", "raw_effect", "adj_effect", "attenuation"],
        rows,
        TABLEAU / "ab_cuped.csv",
    )

    for col, name in metrics:
        s = mde_store[col]
        print("[S4] %-16s baseline=%.4f%% MDE=%.4fpp observed=%+.4fpp power=%.2f%%"
              % (col, s["baseline"] * 100, s["mde_abs"] * 100, s["observed"] * 100, s["power_for_observed"] * 100))
    for v in variants:
        print("[S4] CUPED %s rho=%.4f var_red=%.2f%% x%.4f cov_p=%.5f raw=%+.4fpp adj=%+.4fpp eaten=%.1f%%"
              % (v["id"], v["rho"], v["var_reduction"] * 100, v["multiplier"], v["cov_p"],
                 v["raw_effect"] * 100, v["adj_effect"] * 100, v["attenuation"] * 100))
    print("[S4] wrote report/s4_power_cuped.md + 2 tableau csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
