"""
Germany Winter Gas Security Monitor - data pipeline
---------------------------------------------------
1. Pulls daily gas-storage (GIE AGSI+) and LNG-terminal (GIE ALSI) data for Germany and the EU
2. Pulls German cross-border pipeline flows (ENTSOG) and temperatures with a 14-day forecast (Open-Meteo)
3. Loads everything into SQLite and runs transform.sql + transform_market.sql (same SQL runs in Databricks)
4. Builds end-of-winter storage scenarios, a daily supply/demand balance and a demand-weather model
5. Writes an automated market note (LLM-written if ANTHROPIC_API_KEY is set, template otherwise)

Data sources: GIE AGSI+ / ALSI (attribution required), ENTSOG Transparency Platform, Open-Meteo.

Usage:
    pip install -r requirements.txt
    export GIE_API_KEY=your_key        # Windows PowerShell: $env:GIE_API_KEY="your_key"
    python ingest.py
"""
import os
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import market

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
START = "2018-01-01"  # enough history for a 5-year comparison band from 2023 onwards

SERIES = [  # (platform, area label, query parameters)
    ("agsi", "DE", {"country": "de"}),
    ("agsi", "EU", {"type": "eu"}),
    ("alsi", "DE", {"country": "de"}),
    ("alsi", "EU", {"type": "eu"}),
]

STORAGE_COLS = {
    "gasDayStart": "gas_day",
    "gasInStorage": "gas_in_storage_twh",
    "workingGasVolume": "wgv_twh",
    "full": "fill_pct",
    "injection": "injection_gwh",
    "withdrawal": "withdrawal_gwh",
    "netWithdrawal": "net_withdrawal_gwh",
    "consumption": "consumption_twh",
    "status": "status",
}

LNG_COLS = {
    "gasDayStart": "gas_day",
    "inventory.gwh": "inventory_gwh",
    "sendOut": "send_out_gwh",
    "dtmi.gwh": "dtmi_gwh",
    "dtrs": "dtrs_gwh",
    "status": "status",
}


# ---------------------------------------------------------------- extract
def fetch(platform: str, params: dict, api_key: str) -> list:
    """Download every page of a GIE time series (max 300 rows/page, limit 60 calls/min)."""
    url = f"https://{platform}.gie.eu/api"
    rows, page, last_page = [], 1, 1
    while page <= last_page:
        query = {**params, "from": START, "to": date.today().isoformat(),
                 "size": 300, "page": page}
        resp = requests.get(url, params=query, headers={"x-key": api_key}, timeout=60)
        if resp.status_code == 429:  # rate limited: GIE blocks for 60 s
            time.sleep(61)
            continue
        resp.raise_for_status()
        payload = resp.json()
        last_page = int(payload.get("last_page") or 1)
        data = payload.get("data", [])
        rows.extend(data if isinstance(data, list) else [data])
        page += 1
        time.sleep(1.1)
    return rows


# ---------------------------------------------------------------- clean
def tidy(rows: list, cols: dict, area: str) -> pd.DataFrame:
    """Flatten GIE JSON into a typed table with calendar keys (yr, md) for SQL."""
    for r in rows:  # GIE docs describe inventory/dtmi as arrays in places, objects in others
        for key in ("inventory", "dtmi"):
            if isinstance(r.get(key), list):
                r[key] = r[key][0] if r[key] else {}
    df = pd.json_normalize(rows) if rows else pd.DataFrame()
    df = df.rename(columns=cols).reindex(columns=list(cols.values()))
    for c in df.columns:
        if c not in ("gas_day", "status"):
            df[c] = pd.to_numeric(df[c], errors="coerce")  # numbers arrive as strings, '-' = missing
    df["gas_day"] = pd.to_datetime(df["gas_day"]).dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["gas_day"])
    df["yr"] = df["gas_day"].str[:4].astype(int)
    df["md"] = df["gas_day"].str[5:]
    df.insert(0, "area", area)
    return df.drop_duplicates(["area", "gas_day"]).sort_values("gas_day")


# ---------------------------------------------------------------- transform + outputs
def build(raw_storage: pd.DataFrame, raw_lng: pd.DataFrame,
          raw_flows: pd.DataFrame = None, raw_weather: pd.DataFrame = None) -> str:
    DATA.mkdir(exist_ok=True)
    raw_storage.to_csv(DATA / "raw_storage.csv", index=False)  # upload the raw_* files to Databricks
    raw_lng.to_csv(DATA / "raw_lng.csv", index=False)
    has_market = raw_flows is not None and not raw_flows.empty and raw_weather is not None and not raw_weather.empty
    if has_market:
        raw_flows.to_csv(DATA / "raw_flows.csv", index=False)
        raw_weather.to_csv(DATA / "raw_weather.csv", index=False)

    tables = ["gold_storage_daily", "gold_lng_daily", "gold_winter_scenarios"]
    with sqlite3.connect(DATA / "gas.db") as con:
        raw_storage.to_sql("raw_storage", con, if_exists="replace", index=False)
        raw_lng.to_sql("raw_lng", con, if_exists="replace", index=False)
        con.executescript((ROOT / "transform.sql").read_text())
        if has_market:
            raw_flows.to_sql("raw_flows", con, if_exists="replace", index=False)
            raw_weather.to_sql("raw_weather", con, if_exists="replace", index=False)
            con.executescript((ROOT / "transform_market.sql").read_text())
            tables += ["gold_balance_daily", "gold_weather_daily"]
        gold = {t: pd.read_sql(f"SELECT * FROM {t}", con) for t in tables}

    gold["gold_winter_scenarios"] = label_scenarios(gold["gold_winter_scenarios"])
    if has_market:
        gold["gold_balance_daily"] = trim_incomplete(gold["gold_balance_daily"])
    for name, df in gold.items():
        df.to_csv(DATA / f"{name}.csv", index=False)

    note = market_note(gold)
    (DATA / "market_note.md").write_text(note, encoding="utf-8")
    return note


def label_scenarios(sc: pd.DataFrame) -> pd.DataFrame:
    if sc.empty:
        return sc
    w = sc["analog_winter"].astype(int)
    sc["winter_label"] = w.astype(str) + "/" + (w + 1).astype(str).str[2:]
    sc["storage_exhausted"] = sc["end_march_fill_pct"] < 0
    sc["end_march_fill_pct"] = sc["end_march_fill_pct"].clip(lower=0)
    sc["end_march_twh"] = sc["end_march_twh"].clip(lower=0)
    sc["scenario"] = "Past winter"
    for _, g in sc.groupby("area"):  # rank by gas actually withdrawn, not by the (clipped) end level
        sc.loc[g["remaining_draw_twh"].idxmax(), "scenario"] = "Highest-draw winter"
        sc.loc[g["remaining_draw_twh"].idxmin(), "scenario"] = "Lowest-draw winter"
    return sc


def trim_incomplete(bal: pd.DataFrame) -> pd.DataFrame:
    """Drop trailing gas days whose pipeline flows are still only partly reported to ENTSOG."""
    bal = bal.sort_values("gas_day").reset_index(drop=True)
    pipes = bal["norway_gwh"] + bal["netherlands_gwh"] + bal["belgium_gwh"]
    while len(bal) > 8 and pipes.iloc[-1] < 0.6 * pipes.iloc[-8:-1].median():
        bal, pipes = bal.iloc[:-1], pipes.iloc[:-1]
    return bal


def demand_model(bal: pd.DataFrame, weather: pd.DataFrame):
    """Linear fit of implied demand on heating degree days, applied to the 14-day temperature forecast."""
    b = bal.dropna(subset=["implied_demand_gwh", "hdd"])
    if len(b) < 60:
        return None
    slope, intercept = np.polyfit(b["hdd"], b["implied_demand_gwh"], 1)
    r2 = float(np.corrcoef(b["hdd"], b["implied_demand_gwh"])[0, 1] ** 2)
    fc = weather[weather["is_forecast"] == 1].dropna(subset=["normal_hdd"])
    out = {"slope": slope, "intercept": intercept, "r2": r2, "n": len(b)}
    if not fc.empty:
        out.update(fc_days=len(fc), fc_temp=fc["temp_c"].mean(), normal_temp=fc["normal_temp_c"].mean(),
                   fc_demand=intercept + slope * fc["hdd"].mean(),
                   normal_demand=intercept + slope * fc["normal_hdd"].mean())
    return out


def _pp(value) -> str:
    return "n/a" if pd.isna(value) else f"{value:+.1f} pp"


def market_note(gold: dict) -> str:
    s = gold["gold_storage_daily"]
    lng = gold["gold_lng_daily"]
    sc = gold["gold_winter_scenarios"]
    facts = []
    for area, name in (("DE", "German"), ("EU", "EU")):
        a = s[s["area"] == area].sort_values("gas_day")
        if a.empty:
            continue
        x = a.iloc[-1]
        line = (f"{name} storage was {x.fill_pct:.1f}% full ({x.gas_in_storage_twh:.0f} TWh) on gas day "
                f"{x.gas_day}: {_pp(x.fill_pct - x.fill_5y_avg)} vs the 5-year average and "
                f"{_pp(x.fill_pct - x.fill_last_year)} vs the same day last year.")
        if pd.notna(x.fill_5y_min) and x.fill_pct < x.fill_5y_min:
            line += (f" That is below the lowest level for this date in any of the last five years "
                     f"({x.fill_5y_min:.1f}%).")
        facts.append(line)
        g = sc[sc["area"] == area] if not sc.empty else sc
        if not g.empty:
            lo, med, hi = (g.end_march_fill_pct.min(), g.end_march_fill_pct.median(),
                           g.end_march_fill_pct.max())
            line = (f"If the rest of this winter draws gas like one of the last {len(g)} winters, {name} "
                    f"storage ends March between {lo:.0f}% and {hi:.0f}% full (median {med:.0f}%).")
            if g.storage_exhausted.any():
                line += f" In {int(g.storage_exhausted.sum())} of those analogs storage would run empty first."
            facts.append(line)
    d = lng[lng["area"] == "DE"].dropna(subset=["send_out_7d_gwh"]).sort_values("gas_day")
    if not d.empty:
        y = d.iloc[-1]
        line = f"German LNG terminals sent out an average of {y.send_out_7d_gwh:,.0f} GWh/d over the last 7 days"
        if pd.notna(y.dtrs_gwh) and y.dtrs_gwh > 0:
            line += f", using {100 * y.send_out_7d_gwh / y.dtrs_gwh:.0f}% of their declared send-out capacity"
        facts.append(line + ".")
    if "gold_balance_daily" in gold and len(gold["gold_balance_daily"]) >= 14:
        facts += balance_facts(gold["gold_balance_daily"], gold["gold_weather_daily"])
    facts_text = "\n\n".join(facts)
    return llm_note(facts_text) or (
        f"# Gas market note\n\n{facts_text}\n\n_Data: GIE AGSI+ / ALSI, ENTSOG Transparency Platform, Open-Meteo._\n")


SOURCES = {"norway_gwh": "Norway", "netherlands_gwh": "the Netherlands", "belgium_gwh": "Belgium",
           "lng_gwh": "LNG", "storage_withdrawal_gwh": "storage", "other_pipeline_gwh": "other pipelines",
           "production_gwh": "domestic production"}


def balance_facts(bal: pd.DataFrame, weather: pd.DataFrame) -> list:
    bal = bal.sort_values("gas_day")
    last7 = bal.tail(7)
    end = last7["gas_day"].iloc[-1]
    prev_days = {str(int(d[:4]) - 1) + d[4:] for d in last7["gas_day"]}
    year_ago = bal[bal["gas_day"].isin(prev_days)]
    demand = last7["implied_demand_gwh"].mean()
    line = f"In the 7 days to {end}, German implied gas demand averaged {demand:,.0f} GWh/d"
    if len(year_ago) >= 5:
        line += f" ({100 * (demand / year_ago['implied_demand_gwh'].mean() - 1):+.0f}% vs the same week last year)"
    supply = last7[list(SOURCES)].mean()
    shares = (100 * supply / supply.sum()).sort_values(ascending=False)
    line += ". Supply came from " + ", ".join(f"{SOURCES[k]} {v:.0f}%" for k, v in shares.items() if v >= 1) + "."
    facts = [line]
    nor = last7["norway_gwh"].mean()
    line = f"Norwegian pipeline flows into Germany averaged {nor:,.0f} GWh/d"
    if len(year_ago) >= 5:
        line += f" ({100 * (nor / year_ago['norway_gwh'].mean() - 1):+.0f}% vs a year earlier)"
    facts.append(line + ".")
    m = demand_model(bal, weather)
    if m:
        line = (f"Each extra heating degree day adds about {m['slope']:,.0f} GWh/d of demand "
                f"(fit on {m['n']} days, R² {m['r2']:.2f}).")
        if "fc_temp" in m:
            diff = m["fc_temp"] - m["normal_temp"]
            line += (f" The next {m['fc_days']} days are forecast at {m['fc_temp']:.1f} °C on average, "
                     f"{abs(diff):.1f} °C {'colder' if diff < 0 else 'warmer'} than normal, pointing to demand of about "
                     f"{m['fc_demand']:,.0f} GWh/d ({100 * (m['fc_demand'] / m['normal_demand'] - 1):+.0f}% vs normal weather).")
        facts.append(line)
    return facts


def llm_note(facts: str):
    """Optional AI step: turn computed facts into an analyst-style note. Facts are kept for audit."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        msg = anthropic.Anthropic().messages.create(
            model="claude-sonnet-5-5",
            max_tokens=400,
            messages=[{"role": "user", "content":
                       "You are a European gas market analyst. Write a 120-word note for traders "
                       "on what these numbers mean for winter supply security. Use ONLY these facts "
                       "and add no new numbers.\n\n" + facts}],
        )
        return ("# Gas market note (AI-drafted from the computed facts below)\n\n"
                + msg.content[0].text.strip()
                + "\n\n---\n**Facts used:**\n\n" + facts
                + "\n\n_Data: GIE AGSI+ / ALSI (Gas Infrastructure Europe)._\n")
    except Exception as exc:  # never let the optional AI step break the pipeline
        print(f"LLM note skipped: {exc}")
        return None


def load_dotenv():
    """Read KEY=value lines from .env next to this script (keeps the key out of code and git)."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    load_dotenv()
    api_key = os.environ.get("GIE_API_KEY")
    if api_key in (None, "", "PASTE_YOUR_KEY_HERE"):
        api_key = None
    if not api_key:
        sys.exit("Set GIE_API_KEY first (free key from your account page on https://agsi.gie.eu).")
    storage, lng = [], []
    for platform, area, params in SERIES:
        print(f"Fetching {platform.upper()} {area} ...")
        rows = fetch(platform, params, api_key)
        print(f"  {len(rows)} rows")
        (storage if platform == "agsi" else lng).append(
            tidy(rows, STORAGE_COLS if platform == "agsi" else LNG_COLS, area))
    flows = weather = None
    try:
        print("Fetching ENTSOG pipeline flows (first run takes a few minutes, later runs only fetch new days) ...")
        flows = market.fetch_flows(DATA / "raw_flows.csv")
        print(f"  {len(flows)} rows")
        print("Fetching weather ...")
        weather = market.fetch_weather()
        print(f"  {len(weather)} days")
    except Exception as exc:  # storage/LNG outputs still get built if a market source is down
        print(f"Market data skipped: {exc}")
    note = build(pd.concat(storage, ignore_index=True), pd.concat(lng, ignore_index=True), flows, weather)
    print("\n" + note)
    try:
        import charts
        charts.main()
    except ImportError:
        print("Install matplotlib to also create the README chart images (docs/*.png).")


if __name__ == "__main__":
    main()
