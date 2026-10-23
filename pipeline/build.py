"""Saatlik arşivi günceller ve dashboard'un okuduğu docs/data.json dosyasını üretir.

Kullanım:
    python -m pipeline.build            # eksik ayları ve son 2 ayı çek, JSON üret
    python -m pipeline.build --no-fetch # sadece mevcut arşivden JSON üret
"""

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from pipeline.energy_charts import TZ, fetch_month

ROOT = Path(__file__).resolve().parent.parent
HOURLY_DIR = ROOT / "data" / "hourly"
OUT = ROOT / "docs" / "data.json"
START = "2015-01"


def months_to_fetch(existing, today, start=START):
    """Arşivde olmayan aylar + her zaman son iki ay (veri sonradan düzeltilebiliyor)."""
    current = pd.Period(today, "M")
    all_months = pd.period_range(start, current, freq="M")
    recent = {current, current - 1}
    return [m for m in all_months if str(m) not in existing or m in recent]


def update_archive(today=None):
    HOURLY_DIR.mkdir(parents=True, exist_ok=True)
    today = today or pd.Timestamp.now(tz=TZ)
    existing = {p.stem for p in HOURLY_DIR.glob("*.csv")}
    for month in months_to_fetch(existing, today.tz_localize(None)):
        print(f"çekiliyor: {month}")
        df = fetch_month(str(month))
        if df.empty:
            continue
        df.to_csv(HOURLY_DIR / f"{month}.csv", index_label="time_utc", float_format="%.1f")
        time.sleep(1)  # API'ye nazik davran


def load_archive():
    files = sorted(HOURLY_DIR.glob("*.csv"))
    if not files:
        raise SystemExit(f"{HOURLY_DIR} boş; önce --no-fetch olmadan çalıştırın.")
    df = pd.concat(pd.read_csv(f, index_col="time_utc", parse_dates=True) for f in files)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(TZ)
    return df[~df.index.duplicated(keep="last")].sort_index()


def _round(values, digits=1):
    return [None if pd.isna(v) else round(float(v), digits) for v in values]


def build_profiles(hourly):
    """Yıl × ay için ortalama saatlik profiller (GW): tüketim, güneş, rüzgâr, net yük."""
    df = pd.DataFrame({
        "load": hourly["load"],
        "solar": hourly["solar"],
        "wind": hourly[["wind_onshore", "wind_offshore"]].sum(axis=1, min_count=1),
    }) / 1000
    df["net"] = df["load"] - df["solar"] - df["wind"]
    df = df.dropna(subset=["load", "solar", "wind"])

    grouped = df.groupby([df.index.year, df.index.month, df.index.hour]).mean()
    out = {}
    for (year, month), g in grouped.groupby(level=[0, 1]):
        g = g.droplevel([0, 1]).reindex(range(24))
        out.setdefault(str(year), {})[str(month)] = {c: _round(g[c]) for c in g.columns}
    return out


def build_daily(hourly):
    """Günlük toplamlar (GWh), fiyat özetleri ve rüzgâr+güneşin 'normale' oranı."""
    wind = hourly[["wind_onshore", "wind_offshore"]].sum(axis=1, min_count=1)
    df = pd.DataFrame({
        "load": hourly["load"],
        "wind": wind,
        "solar": hourly["solar"],
        "price": hourly["price"],
    })
    day = df.index.floor("D")
    agg = df.groupby(day).agg(
        load=("load", "sum"), wind=("wind", "sum"), solar=("solar", "sum"),
        hours=("wind", "count"),
        price_avg=("price", "mean"), price_min=("price", "min"), price_max=("price", "max"),
        neg_hours=("price", lambda p: int((p < 0).sum())),
    )
    # Yarım günleri (bugün, eksik veri) at; yaz/kış saati günleri 23/25 saattir.
    agg = agg[agg["hours"] >= 23].drop(columns="hours")
    agg[["load", "wind", "solar"]] /= 1000  # MWh -> GWh

    # Kurulu güç yıllar içinde çok arttığı için mutlak üretim yerine geçmiş 365 günün
    # ortalamasına oranı kullanılıyor: 0.3 = "normal bir günün %30'u kadar rüzgâr+güneş".
    vre = agg["wind"] + agg["solar"]
    baseline = vre.rolling("365D", min_periods=180).mean()
    agg["vre_ratio"] = vre / baseline

    return {
        "date": [d.strftime("%Y-%m-%d") for d in agg.index],
        "load": _round(agg["load"]),
        "wind": _round(agg["wind"]),
        "solar": _round(agg["solar"]),
        "price_avg": _round(agg["price_avg"]),
        "price_min": _round(agg["price_min"]),
        "price_max": _round(agg["price_max"]),
        "neg_hours": [int(v) for v in agg["neg_hours"]],
        "vre_ratio": _round(agg["vre_ratio"], 3),
    }


def build(hourly):
    return {
        "generated_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%MZ"),
        "last_data": hourly.dropna(subset=["load"]).index.max().strftime("%Y-%m-%d %H:%M"),
        "source": "Energy-Charts.info (Fraunhofer ISE), CC BY 4.0",
        "profiles": build_profiles(hourly),
        "daily": build_daily(hourly),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args()
    if not args.no_fetch:
        update_archive()
    data = build(load_archive())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False))
    print(f"yazıldı: {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
