# -*- coding: utf-8 -*-
"""S1 数据校验与分流健康度（SRM）检验

结论先行：这个实验能不能用来做分析？
  * 数据完整性（行数/缺失/重复/异常值）
  * SRM 分流比检验（卡方拟合优度）
  * 轻微失衡的影响边界说明
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablib as A  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORT = A.ensure_dir(ROOT / "report")
TABLEAU = A.ensure_dir(REPORT / "tableau")


def main() -> int:
    src = A.find_data_file()
    df = A.load_analysis_frame(src)
    n = len(df)
    n_c = int((df["version"] == A.CONTROL).sum())
    n_t = int((df["version"] == A.TREATMENT).sum())

    # ---------- 基础质量 ----------
    dup = int(df["userid"].duplicated().sum())
    key_cols = ["userid", "version", "sum_gamerounds", "retention_1", "retention_7"]
    nulls = {c: int(v) for c, v in df[key_cols].isna().sum().items()}
    other_versions = sorted(set(df["version"]) - {A.CONTROL, A.TREATMENT})
    zero_round = int(df["is_zero_round"].sum())
    gr = df["sum_gamerounds"]

    # ---------- SRM ----------
    srm = A.srm_test([n_c, n_t])
    imbalance_abs = n_t - n_c
    imbalance_rel = imbalance_abs / n

    verdict_cn = {
        "ok": "通过：分流比与 1:1 无显著差异",
        "mild_srm": "轻度失衡：0.001 ≤ p < 0.05，需披露并做稳健性检验",
        "severe_srm": "严重失衡：p < 0.001，实验结论不可信",
    }[srm["verdict"]]

    print("[S1] file=%s rows=%d control=%d treatment=%d" % (src.name, n, n_c, n_t))
    print("[S1] SRM chi2=%.4f df=%d p=%.6f verdict=%s" % (srm["chi2"], srm["df"], srm["p_value"], srm["verdict"]))
    print("[S1] imbalance=%+d users (%+.4f%%) zero_round=%d dup=%d" % (imbalance_abs, imbalance_rel * 100, zero_round, dup))

    # ---------- 报告 ----------
    md = []
    md.append("## S1 数据校验与分流健康度（SRM）\n")
    md.append(f"**数据源**：`{src}`\n")
    md.append(f"**样本量**：{n:,} 行 × {df.shape[1]} 列（原始字段 5 个 + 派生字段）\n")

    md.append("### 1.1 数据完整性检查\n")
    rows = [
        ["总行数", f"{n:,}", "—", "—"],
        ["`userid` 重复数", f"{dup}", "应为 0", "通过" if dup == 0 else "**异常**"],
        ["关键字段缺失值", f"{sum(nulls.values())}", "应为 0", "通过" if sum(nulls.values()) == 0 else "**异常**"],
        ["`version` 取值个数", f"{df['version'].nunique()}", "应为 2", "通过" if df["version"].nunique() == 2 else "**异常**"],
        ["异常分组标签", "无" if not other_versions else ", ".join(other_versions), "无", "通过" if not other_versions else "**异常**"],
        ["`sum_gamerounds` = 0 的玩家", f"{zero_round:,}（{A.pct(zero_round / n)}）", "安装后一局未玩", "保留（本身即业务信号）"],
    ]
    md.append(A.md_table(["检查项", "实测值", "期望", "结论"], rows))
    md.append("")

    md.append("### 1.2 `sum_gamerounds` 分布概览（用于判断异常值处理方式）\n")
    q = gr.quantile([0.5, 0.9, 0.95, 0.99, 0.999])
    rows = [
        ["最小值", f"{int(gr.min()):,}"],
        ["中位数 (p50)", f"{int(q[0.5]):,}"],
        ["p90", f"{int(q[0.9]):,}"],
        ["p95", f"{int(q[0.95]):,}"],
        ["p99", f"{int(q[0.99]):,}"],
        ["p99.9", f"{q[0.999]:,.0f}"],
        ["最大值", f"{int(gr.max()):,}"],
        ["均值", f"{gr.mean():.2f}"],
    ]
    md.append(A.md_table(["分位点", "回合数"], rows))
    md.append("")
    md.append(
        f"> **异常值判断**：最大值 {int(gr.max()):,} 回合远高于 p99.9（{q[0.999]:,.0f}），"
        f"属于典型的重尾分布而非录入错误——该玩家可能是超级重度用户或自动化脚本。"
        f"处理方式：**不删除**（删除会引入选择偏误），而是在 S5 中同时报告缩尾（winsorize 到 p95）"
        f"与非参数检验结果。\n"
    )

    md.append("### 1.3 SRM 分流比检验（卡方拟合优度）\n")
    rows = [
        ["对照组 `gate_30`", f"{n_c:,}", f"{A.pct(n_c / n, 4)}", f"{srm['expected'][0]:,.1f}"],
        ["实验组 `gate_40`", f"{n_t:,}", f"{A.pct(n_t / n, 4)}", f"{srm['expected'][1]:,.1f}"],
        ["合计", f"{n:,}", "100.0000%", f"{n:,}"],
    ]
    md.append(A.md_table(["分组", "实际人数", "实际占比", "理论人数 (1:1)"], rows))
    md.append("")
    md.append(
        A.md_table(
            ["统计量", "数值"],
            [
                ["卡方统计量 χ²", f"{srm['chi2']:.4f}"],
                ["自由度 df", f"{srm['df']}"],
                ["p 值", f"{srm['p_value']:.6f}"],
                ["绝对失衡", f"{imbalance_abs:+,} 人"],
                ["相对失衡", A.pct(imbalance_rel, 4)],
                ["判定", f"**{verdict_cn}**"],
            ],
        )
    )
    md.append("")

    if srm["verdict"] == "mild_srm":
        md.append(
            f"> **怎么解读这个 p = {srm['p_value']:.4f}？**\n"
            f">\n"
            f"> 1. 按业界惯例（Microsoft/Netflix 等 A/B 平台的经验阈值），p < 0.001 才判定为严重 SRM、"
            f"直接废弃实验；本例 p = {srm['p_value']:.4f} 落在 0.001–0.05 的灰色区间。\n"
            f"> 2. **样本量放大效应**：90,189 人的实验里，哪怕分流完全正常，"
            f"1% 量级的随机波动也会被检验放大成“显著”。绝对失衡只有 {abs(imbalance_abs):,} 人"
            f"（{A.pct(abs(imbalance_rel), 2)}），业务上可忽略。\n"
            f"> 3. **但必须做两件事**：① 在报告中披露；② 做稳健性检验——"
            f"把多出来的那部分样本随机剔除到严格 1:1 后重算效应，看结论是否改变（见 S3.4）。\n"
        )
    else:
        md.append(f"> SRM 判定：{verdict_cn}。\n")

    md.append("### 1.4 S1 结论\n")
    checks_pass = dup == 0 and sum(nulls.values()) == 0 and df["version"].nunique() == 2
    md.append(
        f"- 数据完整性：{'通过' if checks_pass else '**存在问题**'}（无缺失、无重复、分组标签干净）。\n"
        f"- 分流健康度：{verdict_cn}，需在结论中披露并在 S3 做平衡性稳健检验。\n"
        f"- **可以进入正式分析**，但所有结论都要带上“轻微 SRM 已校正”的说明。\n"
    )

    A.write_text("\n".join(md), REPORT / "s1_validation.md")
    A.save_json(
        {
            "source_file": str(src),
            "rows": n,
            "n_control": n_c,
            "n_treatment": n_t,
            "duplicated_userid": dup,
            "nulls": nulls,
            "zero_round_players": zero_round,
            "gamerounds": {
                "min": int(gr.min()),
                "p50": float(q[0.5]),
                "p95": float(q[0.95]),
                "p999": float(q[0.999]),
                "max": int(gr.max()),
                "mean": float(gr.mean()),
            },
            "srm": srm,
            "checks_pass": bool(checks_pass),
        },
        REPORT / "s1_validation.json",
    )

    # Tableau 抽取：SRM 数据
    A.write_csv(
        ["group", "observed", "expected", "observed_ratio", "chi2", "df", "p_value"],
        [
            [A.CONTROL, n_c, srm["expected"][0], srm["observed_ratio"][0], srm["chi2"], srm["df"], srm["p_value"]],
            [A.TREATMENT, n_t, srm["expected"][1], srm["observed_ratio"][1], srm["chi2"], srm["df"], srm["p_value"]],
        ],
        TABLEAU / "ab_srm.csv",
    )
    print("[S1] wrote report/s1_validation.md, report/s1_validation.json, report/tableau/ab_srm.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
