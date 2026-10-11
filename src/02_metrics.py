# -*- coding: utf-8 -*-
"""S2 指标层（dwd / ads）与 Tableau 抽取

对应 SQL 层：
  dwd_ab_user            -> 玩家级明细（含派生字段）
  ads_ab_overall         -> 分组汇总指标
  ads_ab_activity_quartile -> 分组 × 活跃度四分位
  ads_ab_srm             -> SRM 数据（S1 已产出）
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


def overall_table(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for ver in (A.CONTROL, A.TREATMENT):
        d = df[df["version"] == ver]
        gr = d["sum_gamerounds"]
        out.append(
            {
                "version": ver,
                "n_users": len(d),
                "n_ret1": int(d["retention_1"].sum()),
                "ret1_rate": d["retention_1"].mean(),
                "n_ret7": int(d["retention_7"].sum()),
                "ret7_rate": d["retention_7"].mean(),
                "gamerounds_mean": gr.mean(),
                "gamerounds_median": gr.median(),
                "gamerounds_p95": gr.quantile(0.95),
                "zero_round_users": int(d["is_zero_round"].sum()),
                "zero_round_rate": d["is_zero_round"].mean(),
            }
        )
    return pd.DataFrame(out)


def quartile_table(df: pd.DataFrame) -> pd.DataFrame:
    g = (
        df.groupby(["version", "activity_quartile"], as_index=False)
        .agg(
            n_users=("userid", "size"),
            n_ret1=("retention_1", "sum"),
            ret1_rate=("retention_1", "mean"),
            n_ret7=("retention_7", "sum"),
            ret7_rate=("retention_7", "mean"),
            gamerounds_min=("sum_gamerounds", "min"),
            gamerounds_max=("sum_gamerounds", "max"),
        )
        .sort_values(["activity_quartile", "version"])
    )
    return g


def main() -> int:
    src = A.find_data_file()
    df = A.load_analysis_frame(src)

    ov = overall_table(df)
    qt = quartile_table(df)

    md = ["## S2 指标层与口径固化\n"]
    md.append(
        "### 2.1 指标口径定义\n"
    )
    md.append(
        A.md_table(
            ["指标", "定义", "分母", "层级"],
            [
                ["次日留存率 `ret1_rate`", "`retention_1 = 1` 的人数 / 分组总人数", "**全部进组玩家**", "主指标"],
                ["7 日留存率 `ret7_rate`", "`retention_7 = 1` 的人数 / 分组总人数", "**全部进组玩家**", "主指标（决策依据）"],
                ["平均回合数 `gamerounds_mean`", "`sum_gamerounds` 均值", "全部进组玩家", "护栏/探索指标"],
                ["零回合玩家占比", "`sum_gamerounds = 0` 占比", "全部进组玩家", "护栏指标（体验信号）"],
            ],
        )
    )
    md.append("")
    md.append(
        "> **口径要点**：留存率的分母必须是**全部进组玩家**，绝不能只统计“活跃玩家”。"
        "如果分母被实验影响（例如实验组更早流失，活跃玩家分母被筛掉），"
        "就会得到“留存提升了”的假象——这是 A/B 分析里最典型的分母陷阱（辛普森悖论的一种）。\n"
    )

    md.append("### 2.2 `ads_ab_overall`：分组汇总\n")
    rows = []
    for _, r in ov.iterrows():
        rows.append(
            [
                r["version"],
                f"{int(r['n_users']):,}",
                f"{int(r['n_ret1']):,}",
                A.pct(r["ret1_rate"], 4),
                f"{int(r['n_ret7']):,}",
                A.pct(r["ret7_rate"], 4),
                f"{r['gamerounds_mean']:.2f}",
                f"{r['gamerounds_median']:.0f}",
                f"{r['gamerounds_p95']:.0f}",
                f"{int(r['zero_round_users']):,}（{A.pct(r['zero_round_rate'], 2)}）",
            ]
        )
    md.append(
        A.md_table(
            ["分组", "用户数", "ret1 人数", "次日留存率", "ret7 人数", "7日留存率", "平均回合", "中位回合", "p95 回合", "零回合玩家"],
            rows,
        )
    )
    md.append("")

    md.append("### 2.3 `ads_ab_activity_quartile`：分组 × 活跃度四分位\n")
    rows = []
    for _, r in qt.iterrows():
        rows.append(
            [
                r["version"],
                f"Q{int(r['activity_quartile'])}",
                f"{int(r['gamerounds_min']):,}–{int(r['gamerounds_max']):,}",
                f"{int(r['n_users']):,}",
                A.pct(r["ret1_rate"], 3),
                A.pct(r["ret7_rate"], 3),
            ]
        )
    md.append(A.md_table(["分组", "活跃度分位", "回合数区间", "用户数", "次日留存率", "7日留存率"], rows))
    md.append("")

    md.append("### 2.4 SQL 层对应关系\n")
    md.append(
        A.md_table(
            ["产出", "SQL 层对象", "说明"],
            [
                ["玩家级明细（内存）", "`dwd_ab_user`", "去重 + 留存布尔转 0/1 + 活跃度四分位 + p95 缩尾"],
                ["`overall_table()`", "`ads_ab_overall`", "分组汇总，分子分母均保留"],
                ["`quartile_table()`", "`ads_ab_activity_quartile`", "供分群分析与看板使用"],
                ["Tableau 抽取 csv", "`04_tableau_extract.sql`", "三个结果集导出为 csv"],
            ],
        )
    )
    md.append("")

    A.write_text("\n".join(md), REPORT / "s2_metrics.md")

    # Tableau 抽取
    ov.to_csv(TABLEAU / "ab_overall.csv", index=False, encoding="utf-8-sig")
    qt.to_csv(TABLEAU / "ab_quartile.csv", index=False, encoding="utf-8-sig")

    A.save_json(
        {
            "overall": ov.to_dict(orient="records"),
            "quartile": qt.to_dict(orient="records"),
            "gamerounds_cap_p95": df.attrs.get("gamerounds_cap"),
        },
        REPORT / "s2_metrics.json",
    )

    print("[S2] ret1: %s=%.4f%% %s=%.4f%%" % (A.CONTROL, ov.loc[0, "ret1_rate"] * 100, A.TREATMENT, ov.loc[1, "ret1_rate"] * 100))
    print("[S2] ret7: %s=%.4f%% %s=%.4f%%" % (A.CONTROL, ov.loc[0, "ret7_rate"] * 100, A.TREATMENT, ov.loc[1, "ret7_rate"] * 100))
    print("[S2] mean gamerounds: %s=%.3f %s=%.3f" % (A.CONTROL, ov.loc[0, "gamerounds_mean"], A.TREATMENT, ov.loc[1, "gamerounds_mean"]))
    print("[S2] wrote report/s2_metrics.md + tableau/ab_overall.csv + tableau/ab_quartile.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
