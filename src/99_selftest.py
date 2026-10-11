# -*- coding: utf-8 -*-
"""99_selftest.py —— 对自研统计库做已知值反验

没有 scipy 的情况下，手写分布函数必须自证正确，否则后面所有 p 值都不可信。
这里用教科书临界值做断言：卡方、t 分布、Wilson 区间、FDR 校正。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablib as A  # noqa: E402

FAILED = []


def check(name, got, want, tol=1e-3):
    ok = abs(got - want) <= tol
    print(f"[{'PASS' if ok else 'FAIL'}] {name:<48} got={got:.6f} want={want:.6f}")
    if not ok:
        FAILED.append(name)


print("=" * 78)
print("1) chi-square upper tail  chi2_sf(x, df)")
print("=" * 78)
check("chi2_sf(3.841459, df=1)  -> 0.05", A.chi2_sf(3.841459, 1), 0.05)
check("chi2_sf(6.634897, df=1)  -> 0.01", A.chi2_sf(6.634897, 1), 0.01)
check("chi2_sf(10.827566, df=1) -> 0.001", A.chi2_sf(10.827566, 1), 0.001)
check("chi2_sf(5.991465, df=2)  -> 0.05", A.chi2_sf(5.991465, 2), 0.05)
check("chi2_sf(0.0, df=1)       -> 1.0", A.chi2_sf(0.0, 1), 1.0)

print()
print("=" * 78)
print("2) Student-t two-sided p     t_sf_two_sided(t, df)")
print("=" * 78)
check("t=2.228139, df=10  -> 0.05", A.t_sf_two_sided(2.228139, 10), 0.05)
check("t=1.960000, df=1e7 -> 0.05", A.t_sf_two_sided(1.96, 1e7), 0.05)
check("t=3.169273, df=10  -> 0.01", A.t_sf_two_sided(3.169273, 10), 0.01)
check("t=0.0,      df=10  -> 1.0", A.t_sf_two_sided(0.0, 10), 1.0, tol=1e-9)
print(f"     t_crit(df=10, 0.05) = {A.t_crit(10, 0.05):.6f} (want 2.228139)")

print()
print("=" * 78)
print("3) normal tail (erfc 精度)  two_sided_normal_p(z)")
print("=" * 78)
check("z=1.959964 -> 0.05", A.two_sided_normal_p(1.959964), 0.05)
check("z=2.575829 -> 0.01", A.two_sided_normal_p(2.575829), 0.01)
check("z=0.0      -> 1.0", A.two_sided_normal_p(0.0), 1.0, tol=1e-12)
print(f"     z=8.0 -> p={A.two_sided_normal_p(8.0):.3e} (no underflow, erfc works)")

print()
print("=" * 78)
print("4) Wilson / Newcombe intervals")
print("=" * 78)
lo, hi = A.wilson_ci(50, 100)
check("wilson_ci(50,100) lower -> 0.403832", lo, 0.403832)
check("wilson_ci(50,100) upper -> 0.596168", hi, 0.596168)
lo0, hi0 = A.wilson_ci(0, 50)
check("wilson_ci(0,50) lower   -> 0.0", lo0, 0.0)
print(f"     wilson_ci(0,50) = [{lo0:.6f}, {hi0:.6f}]  (Wald 区间在这里会算出负数，故用 Wilson)")
dlo, dhi = A.newcombe_diff_ci(500, 1000, 500, 1000)
print(f"     newcombe identical arms = [{dlo:.6f}, {dhi:.6f}] (should straddle 0)")

print()
print("=" * 78)
print("5) z-test vs chi-square equivalence (chi2 = z^2)")
print("=" * 78)
z = A.two_prop_ztest(4482, 10000, 3982, 10000)
c = A.chi2_two_prop(4482, 10000, 3982, 10000)
print(f"     z={z['z']:.6f}  z^2={z['z']**2:.6f}  chi2={c['chi2']:.6f}")
check("z^2 == chi2", z["z"] ** 2, c["chi2"], tol=1e-9)
check("p_ztest == p_chi2", z["p_two_sided"], c["p_value"], tol=1e-12)

print()
print("=" * 78)
print("6) BH-FDR vs known example (Benjamini-Hochberg 1995 Table 1 style)")
print("=" * 78)
p = [0.0001, 0.0004, 0.0019, 0.0095, 0.0201, 0.0278, 0.0298, 0.0344, 0.0459, 0.6000]
q = A.bh_fdr(p, 0.05)
print("     p :", " ".join(f"{x:.4f}" for x in p))
print("     q :", " ".join(f"{x:.4f}" for x in q))
q_manual = [min(1.0, x * 10 / (i + 1)) for i, x in enumerate(p)]
print("     hand-computed raw q (before monotonicity):", " ".join(f"{x:.4f}" for x in q_manual))
check("bh_fdr monotone non-decreasing", float(np.all(np.diff(q) >= -1e-12)), 1.0, tol=1e-9)
check("bh_fdr max == min(1, max raw q)", float(q.max()), min(1.0, max(q_manual)), tol=1e-9)

print()
print("=" * 78)
print("7) bootstrap vs analytic z-test on simulated data (should agree)")
print("=" * 78)
rng = np.random.default_rng(7)
k1, n1 = 4500, 10000
p_true = 0.45
k2 = int(rng.binomial(n1, p_true - 0.02))
zt = A.two_prop_ztest(k1, n1, k2, n1)
bt = A.bootstrap_two_prop(k1, n1, k2, n1, n_boot=20000, seed=11)
print(f"     analytic p={zt['p_two_sided']:.4f}   bootstrap p={bt['p_value']:.4f}")
print(f"     analytic diff={zt['abs_diff']:+.5f}  boot CI=[{bt['ci_low']:+.5f}, {bt['ci_high']:+.5f}]")
check("bootstrap p within 0.01 of analytic", bt["p_value"], zt["p_two_sided"], tol=0.01)

print()
print("=" * 78)
print("8) CUPED identity check: if X is unrelated to Y, rho~0 -> no variance reduction")
print("=" * 78)
n = 20000
x = rng.normal(size=n)
y = 0.6 * x + rng.normal(size=n)
r = A.cuped_adjust(y, x)
print(f"     rho={r['rho']:.4f}  var_reduction={r['var_reduction']:.4f}  theta={r['theta']:.4f} (true 0.6)")
check("CUPED rho^2 == var_reduction", r["var_reduction"], r["rho"] ** 2, tol=1e-12)
check("CUPED var_adj == var_raw*(1-rho^2)", r["var_adj"] / r["var_raw"], 1 - r["var_reduction"], tol=1e-9)

print()
print("=" * 78)
print("9) MDE / sample size sanity")
print("=" * 78)
print(f"     mde_abs(p=0.19, n=45000) = {A.mde_abs(0.19, 45000)*100:.3f} pp")
print(f"     n_required(p=0.19, delta=0.01) = {A.n_required(0.19, 0.01):,} per arm")
print(f"     achieved_power(p=0.19, delta=0.01, n=45000) = {A.achieved_power(0.19, 0.01, 45000):.4f}")
check(
    "n_required gives ~80% power",
    A.achieved_power(0.19, 0.01, A.n_required(0.19, 0.01)),
    0.8,
    tol=0.005,
)

print()
print("=" * 78)
print(f"RESULT: {'ALL PASS' if not FAILED else 'FAILURES -> ' + ', '.join(FAILED)}")
print("=" * 78)
sys.exit(1 if FAILED else 0)
