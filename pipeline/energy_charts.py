"""Energy-Charts (Fraunhofer ISE) public API'sinden Almanya verisi çeker.

Veri lisansı: CC BY 4.0, kaynak: https://energy-charts.info
"""

import time

import pandas as pd
import requests

API = "https://api.energy-charts.info"
TZ = "Europe/Berlin"

# Kolon adı -> production_types içindeki ad (küçük harfle, önek eşleşmesi).
# "load" önekiyle "Load (incl. self-consumption)" gibi adlar da yakalanır,
# "Residual load" ise yakalanmaz.
SERIES = {
    "load": "load",
    "solar": "solar",
    "wind_onshore": "wind onshore",
    "wind_offshore": "wind offshore",
}

# Almanya Ekim 2018'de DE-AT-LU teklif bölgesinden ayrıldı.
BZN_SPLIT = pd.Timestamp("2018-10-01", tz=TZ)


def _wait_seconds(response, attempt):
    """429'da sunucunun istediği kadar (Retry-After), yoksa giderek artan süre bekle."""
    if response is not None and response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "")
        return int(retry_after) if retry_after.isdigit() else 30 * 2 ** attempt
    return 2 ** (attempt + 1)


def _get(path, params, retries=5):
    for attempt in range(retries):
        try:
            r = requests.get(f"{API}/{path}", params=params, timeout=120)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            time.sleep(_wait_seconds(getattr(e, "response", None), attempt))


def _find_series(production_types, prefix):
    names = [p["name"] for p in production_types]
    for p in production_types:
        if p["name"].lower() == prefix:
            return p["data"]
    for p in production_types:
        if p["name"].lower().startswith(prefix):
            return p["data"]
    raise KeyError(f"'{prefix}' bulunamadı; API'deki seriler: {names}")


def parse_power(payload):
    """public_power yanıtını UTC indeksli bir DataFrame'e (MW) çevirir."""
    idx = pd.to_datetime(payload["unix_seconds"], unit="s", utc=True)
    cols = {}
    for col, prefix in SERIES.items():
        try:
            cols[col] = _find_series(payload["production_types"], prefix)
        except KeyError:
            if col == "wind_offshore":  # bazı dönemlerde ayrı seri olmayabilir
                cols[col] = [None] * len(idx)
            else:
                raise
    return pd.DataFrame(cols, index=idx, dtype="float64")


def parse_price(payload):
    idx = pd.to_datetime(payload["unix_seconds"], unit="s", utc=True)
    return pd.Series(payload["price"], index=idx, name="price", dtype="float64")


def to_hourly(df):
    """15 dakikalık (veya saatlik) veriyi saatlik ortalamaya indirir. MW ortalaması = MWh."""
    return df.resample("1h").mean()


def fetch_month(month_start):
    """Bir takvim ayının (Berlin saatiyle) saatlik verisini döndürür.

    API'nin start/end yorumundan bağımsız olmak için her iki yana birer gün pay
    bırakılıp sonuç ay sınırlarına kırpılır.
    """
    start = pd.Timestamp(month_start, tz=TZ)
    end = start + pd.offsets.MonthBegin(1)
    params = {
        "start": (start - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "end": (end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
    }

    power = to_hourly(parse_power(_get("public_power", {"country": "de", **params})))

    bzn = "DE-LU" if start >= BZN_SPLIT else "DE-AT-LU"
    try:
        price = to_hourly(parse_price(_get("price", {"bzn": bzn, **params})).to_frame())
        hourly = power.join(price, how="left")
    except requests.HTTPError as e:
        # Fiyatı olmayan dönemde boş bırak; istek sınırı (429) ise ayı hiç yazma, sonra tekrar denensin.
        if e.response is not None and e.response.status_code == 429:
            raise
        hourly = power.assign(price=float("nan"))
    except KeyError:
        hourly = power.assign(price=float("nan"))

    mask = (hourly.index >= start.tz_convert("UTC")) & (hourly.index < end.tz_convert("UTC"))
    return hourly[mask]
