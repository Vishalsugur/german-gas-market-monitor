"""
Market fundamentals beyond storage: cross-border pipeline flows (ENTSOG) and weather (Open-Meteo).
Both sources are public and need no API key.

ENTSOG notes (checked against the live API):
- /aggregatedData?countryKey=DE gives daily physical flows per German TSO, grouped by neighbouring system
  ("Netherlands", "BeLux", "Production", ...), direction entry/exit, in kWh/d.
- Norwegian gas lands at Dornum and Emden. ENTSOG files those flows under a generic "Transmission" bucket,
  and several German TSOs report the same physical flow (e.g. Dornum is reported by OGE and GUD with the same
  value). Summing them would roughly double-count Norway, so Norway is taken from point-level data and the
  known duplicate pairs are counted once.
"""
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

ENTSOG = "https://transparency.entsog.eu/api/v1"
FLOW_START = "2025-01-01"

# German TSO entry points on the Norwegian pipelines (Europipe I/II, Norpipe), from ENTSOG /interconnections
# (fromCountryKey=NO, toCountryKey=DE). Pairs report the same physical flow; ITP-00080 is a separate flow.
NORWAY_PAIRS = [("ITP-00126", "ITP-00188"),   # Dornum (EPT1 & EPT2): OGE and GUD
                ("ITP-00081", "ITP-00105"),   # Emden (EPT1): GUD and Thyssengas
                ("ITP-00075", "ITP-00086")]   # Emden (NPT): Thyssengas and GUD
NORWAY_SINGLE = ["ITP-00080"]                 # Emden (EPT1): OGE
NORWAY_POINTS = [p for pair in NORWAY_PAIRS for p in pair] + NORWAY_SINGLE

# ENTSOG adjacent-system labels -> source names used on the dashboard. Storage and LNG come from GIE instead,
# and "Transmission" (Norway + internal German links) is replaced by the point-level Norway series.
NEIGHBOURS = {
    "Netherlands": "Netherlands", "BeLux": "Belgium", "Joint Bal Zone DK/SE": "Denmark",
    "Poland H-gas": "Poland", "TGPS (YAMAL)": "Poland", "Czech": "Czechia",
    "Austria": "Austria", "Switzerland": "Switzerland", "Production": "Production",
}

CITIES = {  # five large demand centres as a simple national temperature proxy
    "Berlin": (52.52, 13.41), "Hamburg": (53.55, 9.99), "Munich": (48.14, 11.58),
    "Duesseldorf": (51.23, 6.78), "Frankfurt": (50.11, 8.68),
}
WEATHER_START = "2015-01-01"  # ten years before the dashboard window, for "normal" temperatures


# ---------------------------------------------------------------- ENTSOG
def _entsog(endpoint: str, params: dict) -> dict:
    query = "&".join(f"{k}={quote(str(v), safe=',-')}" for k, v in params.items())
    url = f"{ENTSOG}/{endpoint}?{query}"
    for attempt in range(4):
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException:
            pass
        time.sleep(5 * (attempt + 1))  # ENTSOG asks for back-off on retries
    raise RuntimeError(f"ENTSOG request failed: {url}")


def _months(start: date, end: date):
    cur = start.replace(day=1)
    while cur <= end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield max(cur, start), min(nxt - timedelta(days=1), end)
        cur = nxt


def _norway(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["gas_day", "source", "direction", "gwh"])
    df = pd.DataFrame({"gas_day": [r["periodFrom"][:10] for r in rows],
                       "point": [r["pointKey"] for r in rows],
                       "gwh": pd.to_numeric([r.get("value") for r in rows], errors="coerce") / 1e6})
    wide = df.pivot_table(index="gas_day", columns="point", values="gwh", aggfunc="sum").reindex(columns=NORWAY_POINTS)
    total = wide[NORWAY_SINGLE].sum(axis=1, min_count=1).fillna(0)
    for a, b in NORWAY_PAIRS:
        x, y = wide[a], wide[b]
        same = (x - y).abs() <= 0.05 * pd.concat([x, y], axis=1).max(axis=1)
        # identical values = one flow reported twice -> count once; clearly different values -> two flows
        total += pd.concat([x, y], axis=1).max(axis=1).where(same | x.isna() | y.isna(), x + y).fillna(0)
    return pd.DataFrame({"gas_day": total.index, "source": "Norway", "direction": "entry", "gwh": total.values})


def _neighbours(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["gas_day", "source", "direction", "gwh"])
    df = pd.DataFrame({"gas_day": [r["periodFrom"][:10] for r in rows],
                       "label": [r.get("adjacentSystemsLabel") for r in rows],
                       "direction": [r.get("directionKey") for r in rows],
                       "gwh": pd.to_numeric([r.get("value") for r in rows], errors="coerce") / 1e6})
    df["source"] = df["label"].map(NEIGHBOURS)
    df = df.dropna(subset=["source"])
    return df.groupby(["gas_day", "source", "direction"], as_index=False)["gwh"].sum(min_count=1)


def fetch_flows(cache: Path) -> pd.DataFrame:
    """Daily German cross-border flows by source (GWh/d). Re-uses the cached file and refetches only recent months."""
    today = date.today()
    start = date.fromisoformat(FLOW_START)
    old = pd.DataFrame()
    if cache.exists():
        old = pd.read_csv(cache)
        if not old.empty:  # refetch the last ~10 days too, since ENTSOG revises provisional values
            start = max(start, (date.fromisoformat(old["gas_day"].max()) - timedelta(days=10)).replace(day=1))
    parts = []
    for m_start, m_end in _months(start, today):
        print(f"  ENTSOG {m_start:%Y-%m} ...")
        base = {"from": m_start.isoformat(), "to": m_end.isoformat(), "indicator": "Physical Flow",
                "periodType": "day", "timezone": "CET", "limit": -1}
        agg = _entsog("aggregatedData", {"countryKey": "DE", **base}).get("aggregatedData", [])
        nor = _entsog("operationalDatas", {"pointKey": ",".join(NORWAY_POINTS), "directionKey": "entry", **base})
        parts += [_neighbours(agg), _norway(nor.get("operationalDatas", []))]
        time.sleep(1)
    new = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if not old.empty:
        old = old[old["gas_day"] < start.isoformat()]
    flows = pd.concat([old, new], ignore_index=True).sort_values(["gas_day", "source", "direction"])
    return flows[["gas_day", "source", "direction", "gwh"]].reset_index(drop=True)


# ---------------------------------------------------------------- weather
def _city_mean(payload) -> pd.Series:
    """Open-Meteo returns a list for several locations: average the daily mean temperature across them."""
    items = payload if isinstance(payload, list) else [payload]
    frames = [pd.Series(i["daily"]["temperature_2m_mean"], index=i["daily"]["time"], dtype="float") for i in items]
    return pd.concat(frames, axis=1).mean(axis=1, skipna=True)


def fetch_weather() -> pd.DataFrame:
    """Daily mean temperature for Germany (5-city average): history since 2015 plus a 14-day forecast."""
    lat = ",".join(str(v[0]) for v in CITIES.values())
    lon = ",".join(str(v[1]) for v in CITIES.values())
    common = {"latitude": lat, "longitude": lon, "daily": "temperature_2m_mean", "timezone": "Europe/Berlin"}
    print("  Open-Meteo history ...")
    hist = requests.get("https://archive-api.open-meteo.com/v1/archive", timeout=120,
                        params={**common, "start_date": WEATHER_START,
                                "end_date": (date.today() - timedelta(days=1)).isoformat()})
    hist.raise_for_status()
    print("  Open-Meteo forecast ...")
    fc = requests.get("https://api.open-meteo.com/v1/forecast", timeout=60,
                      params={**common, "past_days": 14, "forecast_days": 14})
    fc.raise_for_status()
    h, f = _city_mean(hist.json()).dropna(), _city_mean(fc.json()).dropna()
    temp = pd.concat([h, f[~f.index.isin(h.index)]]).sort_index()  # archive first, forecast model for recent/future days
    today = date.today().isoformat()
    out = pd.DataFrame({"date": temp.index, "temp_c": temp.round(2).values})
    out["is_forecast"] = (out["date"] >= today).astype(int)
    out["yr"] = out["date"].str[:4].astype(int)
    out["md"] = out["date"].str[5:]
    return out
