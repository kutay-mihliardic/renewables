import numpy as np
import pandas as pd
import pytest

from pipeline.build import build, build_daily, months_to_fetch
from pipeline.energy_charts import TZ, parse_power, to_hourly


def _payload(n=8):
    secs = [1704067200 + 900 * i for i in range(n)]  # 2024-01-01 00:00 UTC, 15 dk
    return {
        "unix_seconds": secs,
        "production_types": [
            {"name": "Residual load", "data": [1.0] * n},
            {"name": "Load (incl. self-consumption)", "data": [50000.0] * n},
            {"name": "Solar", "data": [0.0] * n},
            {"name": "Wind onshore", "data": [float(i) for i in range(n)]},
            {"name": "Wind offshore", "data": [100.0] * n},
        ],
    }


def test_parse_power_picks_load_not_residual():
    df = parse_power(_payload())
    assert list(df.columns) == ["load", "solar", "wind_onshore", "wind_offshore"]
    assert (df["load"] == 50000).all()


def test_parse_power_missing_series_names_available():
    payload = _payload()
    payload["production_types"] = [p for p in payload["production_types"] if p["name"] != "Solar"]
    with pytest.raises(KeyError, match="Wind onshore"):
        parse_power(payload)


def test_to_hourly_averages_quarter_hours():
    hourly = to_hourly(parse_power(_payload(8)))
    assert len(hourly) == 2
    assert hourly["wind_onshore"].tolist() == [1.5, 5.5]


def test_months_to_fetch_backfills_and_refreshes_recent():
    months = months_to_fetch({"2024-01", "2024-02", "2024-03"}, pd.Timestamp("2024-04-10"), start="2024-01")
    assert [str(m) for m in months] == ["2024-03", "2024-04"]


def _synthetic_hourly(days=400):
    idx = pd.date_range("2023-01-01", periods=24 * days, freq="h", tz=TZ)
    hour = idx.hour.to_numpy()
    solar = np.clip(np.sin((hour - 6) / 12 * np.pi), 0, None) * 30000
    wind = np.full(len(idx), 15000.0)
    wind[(idx >= "2023-12-01") & (idx < "2023-12-04")] = 500.0  # yapay dunkelflaute
    price = np.where(solar > 25000, -5.0, 80.0)
    return pd.DataFrame({
        "load": 55000.0, "solar": solar, "wind_onshore": wind, "wind_offshore": 0.0, "price": price,
    }, index=idx)


def test_daily_flags_low_renewable_days_and_negative_hours():
    daily = pd.DataFrame(build_daily(_synthetic_hourly())).set_index("date")
    assert daily.loc["2023-12-02", "vre_ratio"] < 0.6
    assert daily.loc["2023-11-20", "vre_ratio"] == pytest.approx(1, abs=0.02)
    assert daily.loc["2023-06-01", "neg_hours"] > 0
    assert daily.loc["2023-06-01", "load"] == pytest.approx(55000 * 24 / 1000)


def test_build_drops_partial_day_and_has_24h_profiles():
    hourly = _synthetic_hourly().iloc[:-5]
    data = build(hourly)
    assert data["daily"]["date"][-1] != hourly.index[-1].strftime("%Y-%m-%d")
    profile = data["profiles"]["2023"]["6"]
    assert len(profile["net"]) == 24
    assert profile["net"][12] < profile["net"][0]  # öğle çukuru


def test_rate_limit_waits_for_retry_after():
    from pipeline.energy_charts import _wait_seconds

    class R:
        def __init__(self, code, headers):
            self.status_code, self.headers = code, headers

    assert _wait_seconds(R(429, {"Retry-After": "90"}), 0) == 90
    assert _wait_seconds(R(429, {}), 1) == 60
    assert _wait_seconds(None, 0) == 2


def test_update_archive_keeps_fetched_months_when_api_fails(tmp_path, monkeypatch):
    import requests

    import pipeline.build as b

    calls = []

    def fake_fetch(month):
        calls.append(month)
        if month == "2024-03":
            raise requests.HTTPError("429 Too Many Requests")
        idx = pd.date_range(f"{month}-01", periods=2, freq="h", tz="UTC")
        return pd.DataFrame({"load": [1.0, 2.0]}, index=idx)

    monkeypatch.setattr(b, "HOURLY_DIR", tmp_path)
    monkeypatch.setattr(b, "START", "2024-01")
    monkeypatch.setattr(b, "fetch_month", fake_fetch)
    monkeypatch.setattr(b, "months_to_fetch", lambda existing, today: [pd.Period(m, "M") for m in ["2024-01", "2024-02", "2024-03", "2024-04"]])

    assert b.update_archive(pd.Timestamp("2024-04-10", tz=TZ), pause=0) is False
    assert sorted(p.stem for p in tmp_path.glob("*.csv")) == ["2024-01", "2024-02"]
    assert calls[-1] == "2024-03"
