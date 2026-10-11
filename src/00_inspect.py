# -*- coding: utf-8 -*-
"""S0 数据探查：先看清真实数据长什么样（只输出 ASCII，避免控制台乱码）"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [
    ROOT / "data" / "cookie_cats.csv",
    ROOT.parent / "cookie_cats.csv",
    ROOT / "data" / "cookie_cats_ab.csv",
]


def find_data():
    for p in CANDIDATES:
        if p.exists():
            return p
    hits = sorted(ROOT.parent.glob("*.csv")) + sorted((ROOT / "data").glob("*.csv"))
    if hits:
        return hits[0]
    raise SystemExit("no csv found")


path = find_data()
df = pd.read_csv(path)
print("file            :", path)
print("shape           :", df.shape)
print("columns         :", list(df.columns))
print("dtypes          :", {c: str(t) for c, t in df.dtypes.items()})
print("nulls           :", df.isna().sum().to_dict())
print("dup userid      :", int(df["userid"].duplicated().sum()) if "userid" in df.columns else "n/a")

vc = df["version"].value_counts().to_dict()
print("version counts  :", vc)
tot = sum(vc.values())
print("version ratio   :", {k: f"{v / tot:.6f}" for k, v in vc.items()})

print("retention_1 raw :", df["retention_1"].value_counts(dropna=False).to_dict())
print("retention_7 raw :", df["retention_7"].value_counts(dropna=False).to_dict())

if pd.api.types.is_numeric_dtype(df["sum_gamerounds"]):
    s = df["sum_gamerounds"]
    print(
        "gamerounds      : min=%s p50=%s p95=%s p99.9=%s max=%s zeros=%s"
        % (s.min(), s.median(), s.quantile(0.95), s.quantile(0.999), s.max(), int((s == 0).sum()))
    )

summary = {
    "file": str(path),
    "rows": int(df.shape[0]),
    "columns": list(df.columns),
    "version_counts": {k: int(v) for k, v in vc.items()},
    "nulls": {k: int(v) for k, v in df.isna().sum().items()},
}
(ROOT / "report").mkdir(exist_ok=True)
(ROOT / "report" / "s0_inspect.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
print("saved -> report/s0_inspect.json")
