# -*- coding: utf-8 -*-
"""S8 序贯检验与 Peeking 问题（Monte Carlo 模拟）

两个问题：
  1. 「每天看一眼 p 值，显著就停」到底会把假阳性率抬高到多少？
  2. 正确的序贯设计该用什么边界？（Lan-DeMets O'Brien-Fleming 形状 + 模拟标定）

方法说明：
  在 H0 下，标准化检验统计量序列 Z_k 依分布收敛到「布朗运动在 t_k 处的值 / sqrt(t_k)」，
  即 Z_k = W(t_k)/sqrt(t_k)，其协方差结构 Cov(Z_j, Z_k) = sqrt(t_j/t_k)。
  因此这里直接模拟布朗运动路径来生成 Z 序列（这是群序列设计的标准做法），
  而不是在每个查看点独立抽二项分布——后者会低估各查看点之间的相关性。
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

K = 14           # 观测期内计划查看 14 次（每天一次）
B = 20000        # 模拟路径数
SEED = 20261009
ALPHA = 0.05


def main() -> int:
    df = A.load_analysis_frame()
    ctrl = df[df["version"] == A.CONTROL]
    trt = df[df["version"] == A.TREATMENT]
    n_c, n_t = len(ctrl), len(trt)
    n_arm = min(n_c, n_t)

    p0 = float(df["retention_7"].mean())
    obs = float(trt["retention_7"].mean() - ctrl["retention_7"].mean())
    se_final = (2 * p0 * (1 - p0) / n_arm) ** 0.5
    theta_obs = obs / se_final                 # 漂移参数（观测效应对应的最终 z）
    mde = A.mde_abs(p0, n_arm)
    theta_mde = -mde / se_final                # 按 MDE 量级设定的备择效应

    tk = np.arange(1, K + 1) / K
    nk = np.round(n_arm * tk).astype(int)
    z_a2 = A.z_crit(ALPHA)
    shape = z_a2 / np.sqrt(tk)                 # O'Brien-Fleming 边界形状

    # ---------- 模拟布朗运动路径 ----------
    rng = np.random.default_rng(SEED)
    dt = np.diff(np.concatenate(([0.0], tk)))
    steps = rng.normal(size=(B, K)) * np.sqrt(dt)
    W0 = np.cumsum(steps, axis=1)
    Z_H0 = W0 / np.sqrt(tk)                                  # H0（无漂移）
    Z_obs = (W0 + theta_obs * tk) / np.sqrt(tk)              # H1：真实效应 = 观测值
    Z_mde = (W0 + theta_mde * tk) / np.sqrt(tk)              # H1：真实效应 = MDE 量级

    def reject_rate(c, Z):
        return float((np.abs(Z) > c * shape).any(axis=1).mean())

    naive_fwer = float((np.abs(Z_H0) > z_a2).any(axis=1).mean())

    # 二分法标定 c，使 FWER = 0.05
    lo, hi = 0.3, 3.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if reject_rate(mid, Z_H0) > ALPHA:
            lo = mid
        else:
            hi = mid
    c_hat = (lo + hi) / 2
    bounds = c_hat * shape
    final_crit = float(bounds[-1])

    # ---------- 各查看点的 alpha 消耗 ----------
    cross_h0 = np.abs(Z_H0) > bounds
    cum_alpha = cross_h0.any(axis=1)
    cum_alpha_by_look = []
    seen = np.zeros(B, dtype=bool)
    for k in range(K):
        seen = seen | cross_h0[:, k]
        cum_alpha_by_look.append(float(seen.mean()))
    nominal_alpha = np.array([2 * (1 - A.normal_cdf(float(b))) for b in bounds])

    # ---------- 功效与停止时间 ----------
    def power_and_stop(Z):
        cross = np.abs(Z) > bounds
        power = float(cross.any(axis=1).mean())
        first = np.where(cross.any(axis=1), cross.argmax(axis=1) + 1, 0)
        stopped = first[first > 0]
        return power, first, (float(stopped.mean()) if len(stopped) else float("nan"))

    power_obs, first_obs, asn_obs = power_and_stop(Z_obs)
    power_mde, first_mde, asn_mde = power_and_stop(Z_mde)
    fixed_power_obs = A.achieved_power(p0, abs(obs), n_arm)
    fixed_power_mde = A.achieved_power(p0, mde, n_arm)

    real_z = abs(obs) / se_final

    # ---------- 报告 ----------
    md = ["## S8 序贯检验与 Peeking 问题\n"]
    md.append(
        f"设计设定：观测期内最多查看 {K} 次（每天一次），每次累计样本量按时间等比增长；"
        f"模拟 {B:,} 条实验路径。\n"
    )

    md.append("### 8.1 Peeking 到底会把假阳性率抬高多少？\n")
    md.append(
        A.md_table(
            ["设计", "名义 α", "实际假阳性率（FWER）", "膨胀倍数"],
            [
                ["固定样本（只看一次，最终结果）", "5%", "5.00%（定义值）", "1.00×"],
                [f"每天看一次、显著就停（共 {K} 次）", "5%", f"**{naive_fwer * 100:.2f}%**",
                 f"**{naive_fwer / 0.05:.2f}×**"],
                [f"O'Brien-Fleming 序贯边界（共 {K} 次）", "5%", f"{reject_rate(c_hat, Z_H0) * 100:.2f}%", "1.00×"],
            ],
        )
    )
    md.append("")
    md.append(
        f"> **这就是 A/B 平台反复强调「不要中途偷看 p 值」的原因**："
        f"如果你每天看一次、看到 p < 0.05 就宣布胜利，"
        f"在方案完全无效的情况下，你有 **{naive_fwer * 100:.1f}%** 的概率至少误报一次显著——"
        f"而不是你以为的 5%。"
        f"对每天都有大量实验的团队来说，这意味着源源不断的假阳性上线。\n"
    )

    md.append("### 8.2 正确的序贯设计：O'Brien-Fleming 型边界\n")
    md.append(
        f"采用 Lan-DeMets O'Brien-Fleming 消耗函数形状 `w_k = z_(α/2) / sqrt(t_k)`（早期严格、后期放松），"
        f"用 {B:,} 条模拟路径标定缩放系数 **c = {c_hat:.4f}**，"
        f"使整体假阳性率恰好控制在 5%。\n"
    )
    rows = []
    for k in range(K):
        inc_alpha = cum_alpha_by_look[k] - (cum_alpha_by_look[k - 1] if k > 0 else 0.0)
        rows.append([
            f"{k + 1}", f"{tk[k]:.4f}", f"{nk[k]:,}", f"{bounds[k]:.4f}",
            f"{nominal_alpha[k] * 100:.6f}%", f"{inc_alpha * 100:.4f}%", f"{cum_alpha_by_look[k] * 100:.4f}%",
        ])
    md.append(
        A.md_table(
            ["查看次数 k", "信息比例 t_k", "累计样本/组", "边界 |Z| 阈值",
             "该点名义 α", "本次新增消耗 α", "累计消耗 α"],
            rows,
        )
    )
    md.append("")
    md.append(
        f"> 前几次查看的边界很严（第 1 次要看 |Z| > {bounds[0]:.2f}，"
        f"对应名义 α = {nominal_alpha[0] * 100:.6f}%），越接近实验结束边界越松"
        f"（最后一次 |Z| > {final_crit:.4f}，非常接近固定样本的 1.96）。"
        f"这正是 O'Brien-Fleming 的设计哲学：**早停的门槛极高，把大部分 α 留给最终结论**。\n"
    )

    md.append("### 8.3 序贯设计的代价与收益\n")
    md.append(
        A.md_table(
            ["场景", "固定样本设计功效", "序贯设计功效", "平均停止查看次数", "结论"],
            [
                [f"真实效应 = 观测值（{obs * 100:+.4f} pp，θ = {theta_obs:.3f}）",
                 f"{fixed_power_obs * 100:.2f}%", f"{power_obs * 100:.2f}%",
                 f"{asn_obs:.2f} / {K}", "几乎无功效损失"],
                [f"真实效应 = MDE（{mde * 100:.4f} pp，θ = {theta_mde:.3f}）",
                 f"{fixed_power_mde * 100:.2f}%", f"{power_mde * 100:.2f}%",
                 f"{asn_mde:.2f} / {K}", "边缘效应下单次就能定论"],
            ],
        )
    )
    md.append("")
    md.append(
        f"> **结论**：在效应较大时（观测效应量级），序贯设计几乎不损失功效"
        f"（{power_obs * 100:.2f}% vs {fixed_power_obs * 100:.2f}%），"
        f"但换来了「可以提前结束」的选项；"
        f"而在效应只是勉强达到 MDE 时，效应对停止时间敏感，"
        f"平均需要在第 {asn_mde:.1f} 次查看附近才能定论——**这正说明 MDE 附近的实验本质上就是不确定的**。\n"
    )

    md.append("### 8.4 回到本次实验：如果按序贯设计判，结论会变吗？\n")
    md.append(
        A.md_table(
            ["量", "数值", "说明"],
            [
                ["实际最终 |Z|（7 日留存）", f"{real_z:.4f}", "由 S3 的 z 检验得出"],
                ["序贯设计最终边界", f"{final_crit:.4f}", f"= c × 1.96 = {c_hat:.4f} × {z_a2:.4f}"],
                ["固定样本边界", f"{z_a2:.4f}", "常规做法"],
                ["判定", f"**{'拒绝 H0，结论不变' if real_z > final_crit else '无法拒绝 H0，结论会变'}**",
                 f"|Z| {'>' if real_z > final_crit else '≤'} 边界"],
            ],
        )
    )
    md.append("")
    md.append(
        f"> **本次实验的最终 |Z| = {real_z:.4f}，高于序贯边界 {final_crit:.4f}，"
        f"因此即使采用更严格的序贯设计，结论依然是「实验组显著更差」——"
        f"这说明 S7 的不上线结论对监控方式不敏感。**"
        f"（注意：这里只能检验「最终结论是否改变」，"
        f"因为数据集只提供了 14 天的汇总数据，没有提供每日的中间快照。）\n"
    )

    md.append("### 8.5 给团队的实验监控规范（可落地）\n")
    md.append(
        f"1. **不要每天看 p 值**。本次模拟显示，每天看一次会把假阳性率从 5% 推到 {naive_fwer * 100:.1f}%。\n"
        f"2. **如果要中途监控，就用序贯边界**：本报告的边界表可以直接抄进监控看板"
        f"（第 1–{K} 次查看分别要求 |Z| > {', '.join(f'{b:.2f}' for b in bounds[:5])} …）。\n"
        f"3. **实操上更简单的方案**：把查看次数压到 **3–5 次**（例如第 3、7、14 天），"
        f"查看次数越少，边界越接近 1.96，也越容易被业务方理解和执行。\n"
        f"4. **监控护栏指标而不是主指标**：主指标只在预设查看点看；"
        f"但崩溃率、投诉率、支付失败率这类**护栏指标应当实时监控并允许立即叫停**——"
        f"护栏指标的早停不受多重比较问题约束，因为它的目的是止损而不是证明收益。\n"
        f"5. **写进实验规范**：实验开始前必须写清「主指标、MDE、样本量、观测窗口、查看次数与边界」，"
        f"避免事后挑结果。\n"
    )

    A.write_text("\n".join(md), REPORT / "s8_sequential_peeking.md")
    A.save_json(
        {
            "K": K, "B": B, "alpha": ALPHA,
            "naive_fwer": naive_fwer,
            "calibrated_scale_c": c_hat,
            "boundaries": bounds.tolist(),
            "final_critical_value": final_crit,
            "nominal_alpha_per_look": nominal_alpha.tolist(),
            "cumulative_alpha_spent": cum_alpha_by_look,
            "power_observed_effect": {"sequential": power_obs, "fixed": fixed_power_obs, "asn": asn_obs},
            "power_mde_effect": {"sequential": power_mde, "fixed": fixed_power_mde, "asn": asn_mde},
            "real_final_z": real_z,
            "conclusion_unchanged": bool(real_z > final_crit),
        },
        REPORT / "s8_sequential_peeking.json",
    )

    # Tableau 抽取
    rows = []
    for k in range(K):
        inc = cum_alpha_by_look[k] - (cum_alpha_by_look[k - 1] if k > 0 else 0.0)
        rows.append([k + 1, tk[k], nk[k], bounds[k], nominal_alpha[k], inc, cum_alpha_by_look[k], z_a2])
    A.write_csv(
        ["look", "info_fraction", "n_per_arm", "obf_boundary", "nominal_alpha", "alpha_spent_increment",
         "alpha_spent_cumulative", "fixed_boundary"],
        rows,
        TABLEAU / "ab_sequential_boundary.csv",
    )

    rows = []
    for k in range(1, K + 1):
        rows.append([k, float((np.abs(Z_H0[:, :k]) > z_a2).any(axis=1).mean()),
                     cum_alpha_by_look[k - 1]])
    A.write_csv(
        ["n_looks", "naive_fwer", "obf_fwer"], rows, TABLEAU / "ab_peeking_inflation.csv"
    )

    print("[S8] naive FWER with %d daily looks = %.4f (%.2fx nominal)" % (K, naive_fwer, naive_fwer / 0.05))
    print("[S8] calibrated OBF scale c=%.4f  final boundary=%.4f  FWER=%.4f"
          % (c_hat, final_crit, reject_rate(c_hat, Z_H0)))
    print("[S8] boundaries: " + ", ".join("%.2f" % b for b in bounds[:6]) + " ... " + "%.2f" % bounds[-1])
    print("[S8] power(obs effect): sequential=%.4f fixed=%.4f asn=%.2f" % (power_obs, fixed_power_obs, asn_obs))
    print("[S8] power(MDE effect): sequential=%.4f fixed=%.4f asn=%.2f" % (power_mde, fixed_power_mde, asn_mde))
    print("[S8] real final |z|=%.4f vs sequential boundary=%.4f -> conclusion unchanged=%s"
          % (real_z, final_crit, real_z > final_crit))
    print("[S8] wrote report/s8_sequential_peeking.md + 2 tableau csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
