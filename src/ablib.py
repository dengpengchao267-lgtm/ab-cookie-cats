# -*- coding: utf-8 -*-
"""ablib —— 本项目的自研统计工具库

运行环境里没有 scipy / statsmodels（已实测确认），所以全部手写实现。
这样做的好处是每个公式都可追溯。

公式出处：
  * 正则化不完全 Gamma 函数 gser / gcf ......... Numerical Recipes 3rd ed. §6.2
  * 正则化不完全 Beta 函数 betacf / betai ...... Numerical Recipes 3rd ed. §6.4
  * Wilson score interval ...................... Wilson (1927)
  * Newcombe 差值置信区间 (method 10) .......... Newcombe (1998), Stat. in Medicine
  * 两比例 z 检验 / 卡方检验 .................... 标准教科书（erfc 实现，尾部精度好）
  * Bootstrap 假设检验 ......................... Efron & Tibshirani (1993)
  * CUPED 方差缩减 ............................. Deng, Xu, Kohavi & Walker (2013), WSDM
  * BH-FDR / Holm 多重比较校正 ................. Benjamini & Hochberg (1995) / Holm (1979)
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ND = NormalDist()

# ============================================================================
# 一、基础分布函数
# ============================================================================


def normal_cdf(x: float) -> float:
    return ND.cdf(x)


def normal_ppf(p: float) -> float:
    return ND.inv_cdf(p)


def z_crit(alpha: float = 0.05, two_sided: bool = True) -> float:
    return normal_ppf(1 - alpha / 2) if two_sided else normal_ppf(1 - alpha)


def two_sided_normal_p(z: float) -> float:
    """双侧正态 p 值。用 erfc 而不是 2*(1-Phi)，因为后者在 |z|>6 时会因浮点相减丢精度。"""
    return math.erfc(abs(z) / math.sqrt(2.0))


def one_sided_normal_p(z: float, direction: str = "greater") -> float:
    """direction='greater' 表示 H1: 实验组 > 对照组。"""
    if direction == "greater":
        return 0.5 * math.erfc(z / math.sqrt(2.0))
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def _gser(a: float, x: float) -> float:
    """下不完全 Gamma 函数 P(a,x) 的级数展开（x < a+1 时收敛快）。"""
    itmax, eps = 500, 3e-14
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(itmax):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * eps:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    """上不完全 Gamma 函数 Q(a,x) 的连分式展开（x >= a+1 时收敛快）。"""
    itmax, eps, fpmin = 500, 3e-14, 1e-300
    b = x + 1.0 - a
    c = 1.0 / fpmin
    d = 1.0 / b
    h = d
    for i in range(1, itmax + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < fpmin:
            d = fpmin
        c = b + an / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def gammq(a: float, x: float) -> float:
    """上不完全 Gamma 函数 Q(a,x) = 1 - P(a,x)。"""
    if x < 0 or a <= 0:
        raise ValueError("gammq 参数非法")
    if x == 0:
        return 1.0
    return 1.0 - _gser(a, x) if x < a + 1.0 else _gcf(a, x)


def chi2_sf(x: float, df: float) -> float:
    """卡方分布上尾概率 P(X > x)，自由度 df。"""
    if x <= 0:
        return 1.0
    return gammq(df / 2.0, x / 2.0)


def _betacf(a: float, b: float, x: float) -> float:
    itmax, eps, fpmin = 500, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betai(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数 I_x(a,b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf_two_sided(t: float, df: float) -> float:
    """Student-t 双侧 p 值：P(|T_df| > |t|) = I_{df/(df+t^2)}(df/2, 1/2)。"""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    return betai(df / 2.0, 0.5, x)


# ============================================================================
# 二、比例类指标检验
# ============================================================================


def two_prop_ztest(k1: int, n1: int, k2: int, n2: int) -> dict:
    """两比例 z 检验。组 1 = 对照组，组 2 = 实验组；diff = p1 - p2。

    合并方差（pooled）用于检验，因为 H0 假定两组同一总体比例。
    """
    p1, p2 = k1 / n1, k2 / n2
    diff = p1 - p2
    p_pool = (k1 + k2) / (n1 + n2)
    se_pool = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    z = diff / se_pool if se_pool > 0 else 0.0
    se_unpooled = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    return {
        "p_control": p1,
        "p_treatment": p2,
        "abs_diff": diff,                       # 绝对差（实验组 - 对照组取反见下）
        "abs_lift_treatment_minus_control": p2 - p1,
        "rel_lift": (p2 - p1) / p1 if p1 > 0 else float("nan"),
        "z": z,
        "se_pooled": se_pool,
        "se_unpooled": se_unpooled,
        "p_two_sided": two_sided_normal_p(z),
        "p_treatment_better": one_sided_normal_p(z, "less"),   # H1: p2 > p1
        "p_treatment_worse": one_sided_normal_p(z, "greater"),  # H1: p2 < p1
    }


def chi2_two_prop(k1: int, n1: int, k2: int, n2: int) -> dict:
    """2x2 列联表卡方检验（无连续性校正，与 z 检验等价：chi2 = z^2）。"""
    table = np.array([[k1, n1 - k1], [k2, n2 - k2]], dtype=float)
    total = table.sum()
    row = table.sum(axis=1, keepdims=True)
    col = table.sum(axis=0, keepdims=True)
    expected = row @ col / total
    chi2 = float(((table - expected) ** 2 / expected).sum())
    return {"chi2": chi2, "df": 1, "p_value": chi2_sf(chi2, 1)}


def srm_test(counts, expected_ratio=None) -> dict:
    """Sample Ratio Mismatch 检验：观测分组人数是否符合预期比例（默认 1:1）。

    用卡方拟合优度检验。业界惯例：p < 0.001 视为严重 SRM（实验作废）；
    0.001 <= p < 0.05 视为轻度失衡，需要进一步检查协变量平衡性。
    """
    counts = np.asarray(counts, dtype=float)
    total = counts.sum()
    if expected_ratio is None:
        expected = np.full(len(counts), total / len(counts))
    else:
        ratio = np.asarray(expected_ratio, dtype=float)
        expected = total * ratio / ratio.sum()
    chi2 = float(((counts - expected) ** 2 / expected).sum())
    df = len(counts) - 1
    p = chi2_sf(chi2, df)
    return {
        "observed": counts.astype(int).tolist(),
        "expected": expected.tolist(),
        "chi2": chi2,
        "df": df,
        "p_value": p,
        "observed_ratio": (counts / total).tolist(),
        "verdict": "severe_srm" if p < 0.001 else ("mild_srm" if p < 0.05 else "ok"),
    }


def wilson_ci(k: int, n: int, alpha: float = 0.05) -> tuple:
    """单臂 Wilson score 置信区间（比 Wald 区间在极端比例/小样本下稳健）。"""
    if n == 0:
        return (float("nan"), float("nan"))
    z = z_crit(alpha)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def newcombe_diff_ci(k1: int, n1: int, k2: int, n2: int, alpha: float = 0.05) -> tuple:
    """两比例之差的 Newcombe method 10 混合 score 区间（对照组 - 实验组）。"""
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson_ci(k1, n1, alpha)
    l2, u2 = wilson_ci(k2, n2, alpha)
    diff = p1 - p2
    lo = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return (lo, hi)


def bootstrap_two_prop(
    k1: int, n1: int, k2: int, n2: int, n_boot: int = 10000, alpha: float = 0.05, seed: int = 42
) -> dict:
    """Bootstrap 检验与置信区间（二项等价形式）。

    说明：对 0/1 数据做「个体级有放回重采样」，其和服从 Binomial(n, p_hat)，
    因此用二项抽样实现与原意完全等价，且内存友好（n=45k 时朴素索引矩阵需要数 GB）。
      * 未约束 bootstrap（用各自 p_hat）-> 差值置信区间
      * 零假设 bootstrap（用合并 p_pool）-> 检验 p 值
    """
    rng = np.random.default_rng(seed)
    p1, p2 = k1 / n1, k2 / n2
    obs = p1 - p2

    d = rng.binomial(n1, p1, n_boot) / n1 - rng.binomial(n2, p2, n_boot) / n2
    lo, hi = np.percentile(d, [100 * alpha / 2, 100 * (1 - alpha / 2)])

    p_pool = (k1 + k2) / (n1 + n2)
    d_null = rng.binomial(n1, p_pool, n_boot) / n1 - rng.binomial(n2, p_pool, n_boot) / n2
    p_value = float((np.abs(d_null) >= abs(obs)).mean())

    return {
        "n_boot": n_boot,
        "observed_diff": obs,
        "boot_mean": float(d.mean()),
        "boot_se": float(d.std(ddof=1)),
        "ci_low": float(lo),
        "ci_high": float(hi),
        "p_value": p_value,
        "boot_diffs": d,  # 留给画图/落盘用
    }


# ============================================================================
# 三、连续型指标检验
# ============================================================================


def welch_ttest(a, b, alpha: float = 0.05) -> dict:
    """Welch t 检验（不假定方差齐性）。组 1 = 对照组，组 2 = 实验组。"""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na, nb = len(a), len(b)
    ma, mb = a.mean(), b.mean()
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(va / na + vb / nb)
    t = (ma - mb) / se if se > 0 else 0.0
    df = (va / na + vb / nb) ** 2 / (
        (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)
    )
    p = t_sf_two_sided(t, df)
    tc = t_crit(df, alpha)
    return {
        "mean_control": float(ma),
        "mean_treatment": float(mb),
        "mean_diff": float(ma - mb),
        "se": se,
        "t": t,
        "df": df,
        "p_value": p,
        "ci_low": float(ma - mb - tc * se),
        "ci_high": float(ma - mb + tc * se),
    }


def t_crit(df: float, alpha: float = 0.05) -> float:
    """t 分布双侧临界值：用二分法反解 t_sf_two_sided。"""
    lo, hi = 0.0, 100.0
    target = alpha
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_sf_two_sided(mid, df) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def mannwhitney_u(a, b) -> dict:
    """Mann-Whitney U 检验（正态近似 + 结校正），适合厚尾分布。"""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na, nb = len(a), len(b)
    allv = np.concatenate([a, b])
    ranks = pd.Series(allv).rank(method="average").to_numpy()
    ra = ranks[:na].sum()
    u1 = ra - na * (na + 1) / 2
    u2 = na * nb - u1
    u = min(u1, u2)
    mu = na * nb / 2
    # 结校正
    _, counts = np.unique(allv, return_counts=True)
    tie = (counts ** 3 - counts).sum()
    n = na + nb
    sigma = math.sqrt(na * nb / 12 * ((n + 1) - tie / (n * (n - 1))))
    z = (u - mu) / sigma if sigma > 0 else 0.0
    return {
        "u": float(u),
        "u1_control": float(u1),
        "z": float(z),
        "p_value": two_sided_normal_p(z),
        "prob_control_greater": float(u1 / (na * nb)),  # 优势概率 P(A>B)
    }


def bootstrap_mean_diff(a, b, n_boot: int = 4000, alpha: float = 0.05, seed: int = 42, chunk: int = 250) -> dict:
    """厚尾连续变量的均值差 Bootstrap 置信区间（分块执行，控制内存）。"""
    rng = np.random.default_rng(seed)
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na, nb = len(a), len(b)
    diffs = np.empty(n_boot, dtype=float)
    done = 0
    while done < n_boot:
        m = min(chunk, n_boot - done)
        ia = rng.integers(0, na, size=(m, na))
        ib = rng.integers(0, nb, size=(m, nb))
        diffs[done : done + m] = a[ia].mean(axis=1) - b[ib].mean(axis=1)
        done += m
    lo, hi = np.percentile(diffs, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {
        "observed_diff": float(a.mean() - b.mean()),
        "boot_mean": float(diffs.mean()),
        "boot_se": float(diffs.std(ddof=1)),
        "ci_low": float(lo),
        "ci_high": float(hi),
        "n_boot": n_boot,
        "boot_diffs": diffs,
    }


# ============================================================================
# 四、功效分析
# ============================================================================


def mde_abs(p: float, n_per_arm: int, alpha: float = 0.05, power: float = 0.8) -> float:
    """最小可检测效应（绝对，两比例、等样本量、正态近似）。"""
    za, zb = z_crit(alpha), normal_ppf(power)
    return (za + zb) * math.sqrt(2 * p * (1 - p) / n_per_arm)


def n_required(p: float, delta: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """检测绝对提升 delta 所需的每组样本量。"""
    za, zb = z_crit(alpha), normal_ppf(power)
    return math.ceil((za + zb) ** 2 * 2 * p * (1 - p) / delta ** 2)


def achieved_power(p: float, delta: float, n_per_arm: int, alpha: float = 0.05) -> float:
    """给定样本量与真实效应，达到的检验功效。"""
    za = z_crit(alpha)
    se = math.sqrt(2 * p * (1 - p) / n_per_arm)
    return 1 - normal_cdf(za - abs(delta) / se)


# ============================================================================
# 五、多重比较校正
# ============================================================================


def bh_fdr(pvals, alpha: float = 0.05) -> np.ndarray:
    """Benjamini-Hochberg FDR 校正，返回 q 值（单调化后的校正 p 值）。"""
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    order = np.argsort(p)
    ranked = p[order]
    q = ranked * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    q = np.clip(q, 0.0, 1.0)
    out = np.empty(m)
    out[order] = q
    return out


def holm_adjust(pvals) -> np.ndarray:
    """Holm-Bonferroni 逐步校正（控制 FWER）。"""
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    order = np.argsort(p)
    ranked = p[order]
    adj = ranked * (m - np.arange(m))
    adj = np.maximum.accumulate(adj)
    adj = np.clip(adj, 0.0, 1.0)
    out = np.empty(m)
    out[order] = adj
    return out


# ============================================================================
# 六、CUPED
# ============================================================================


def cuped_adjust(y, x) -> dict:
    """CUPED 方差缩减：Y_adj = Y - theta * (X - E[X])，theta = Cov(X,Y)/Var(X)。

    方差缩减比例 = rho^2（X 与 Y 的相关系数平方），等效样本量倍数 = 1/(1-rho^2)。
    """
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    cov = np.cov(x, y, ddof=1)
    theta = cov[0, 1] / cov[0, 0] if cov[0, 0] > 0 else 0.0
    y_adj = y - theta * (x - x.mean())
    rho = float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 and y.std() > 0 else 0.0
    var_reduction = rho ** 2
    return {
        "theta": float(theta),
        "rho": rho,
        "var_reduction": float(var_reduction),
        "sample_multiplier": float(1 / (1 - var_reduction)) if var_reduction < 1 else float("inf"),
        "y_adj": y_adj,
        "var_raw": float(y.var(ddof=1)),
        "var_adj": float(y_adj.var(ddof=1)),
    }


# ============================================================================
# 七、数据加载与 IO
# ============================================================================

CONTROL, TREATMENT = "gate_30", "gate_40"


def find_data_file(root: Path | None = None) -> Path:
    """在项目 data/ 与工作目录根下寻找 cookie_cats 数据文件。"""
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    candidates = [root / "data" / "cookie_cats.csv", root.parent / "cookie_cats.csv"]
    candidates += sorted((root / "data").glob("*.csv"))
    candidates += sorted(root.parent.glob("*cookie*.csv"))
    candidates += sorted(root.parent.glob("*cookie*.xlsx"))
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("未找到 cookie_cats 数据文件")


def load_cookie_cats(path: Path | None = None) -> pd.DataFrame:
    """读入并规范化数据：统一列名、布尔化留存字段、派生基础字段。"""
    path = Path(path) if path else find_data_file()
    df = pd.read_excel(path) if path.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(path)

    rename = {
        "user_id": "userid",
        "user_id ": "userid",
        "group": "version",
        "arm": "version",
        "variant": "version",
        "sum_gamerounds ": "sum_gamerounds",
        "game_rounds": "sum_gamerounds",
        "retention1": "retention_1",
        "retention7": "retention_7",
        "retention_1d": "retention_1",
        "retention_7d": "retention_7",
    }
    df = df.rename(columns={c: rename.get(c.strip(), c.strip()) for c in df.columns})

    for col in ("retention_1", "retention_7"):
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})
        else:
            df[col] = df[col].astype(bool)
        df[col] = df[col].astype(int)

    df["version"] = df["version"].astype(str).str.strip()
    df["sum_gamerounds"] = pd.to_numeric(df["sum_gamerounds"], errors="coerce").astype("int64")

    # 派生字段（与 SQL 层 dwd_ab_user 保持一致）
    df["is_control"] = (df["version"] == CONTROL).astype(int)
    df["is_treatment"] = (df["version"] == TREATMENT).astype(int)
    df["is_zero_round"] = (df["sum_gamerounds"] == 0).astype(int)
    df["activity_quartile"] = pd.qcut(
        df["sum_gamerounds"].rank(method="first"), 4, labels=[1, 2, 3, 4]
    ).astype(int)
    cap = float(df["sum_gamerounds"].quantile(0.95))
    df["gamerounds_capped"] = df["sum_gamerounds"].clip(upper=cap)
    df.attrs["source_file"] = str(path)
    df.attrs["gamerounds_cap"] = cap
    return df


def load_analysis_frame(path: Path | None = None) -> pd.DataFrame:
    """排序好的分析用宽表（对照组在前，实验组在后），供各模块共用。"""
    df = load_cookie_cats(path)
    df = df.sort_values(["is_treatment", "userid"]).reset_index(drop=True)
    return df


def ensure_dir(p) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_csv(columns, rows, path):
    path = Path(path)
    ensure_dir(path.parent)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def md_table(columns, rows) -> str:
    out = ["| " + " | ".join(str(c) for c in columns) + " |"]
    out.append("|" + "|".join(["---"] * len(columns)) + "|")
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    return "\n".join(out)


def save_json(obj, path):
    path = Path(path)
    ensure_dir(path.parent)

    def default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.bool_,)):
            return bool(o)
        raise TypeError(str(type(o)))

    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=default), encoding="utf-8")
    return path


def write_text(text, path):
    path = Path(path)
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")
    return path


def pct(x, digits: int = 2) -> str:
    return f"{x * 100:.{digits}f}%"


def pp(x, digits: int = 2) -> str:
    """把比例转成百分点数字。"""
    return f"{x * 100:+.{digits}f}pp"


def stars(p: float) -> str:
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "n.s."


def get_plt():
    """可选绘图：本运行环境没有 matplotlib，装了才画图，否则优雅跳过。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        return plt
    except Exception:
        return None
