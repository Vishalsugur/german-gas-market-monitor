"""
Static chart images for the README (docs/*.png).
Runs automatically at the end of ingest.py when matplotlib is installed, or on its own:
    python charts.py
"""
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA, DOCS = ROOT / "data", ROOT / "docs"

BLUE, ORANGE = "#2a78d6", "#eb6834"          # this year / last year
AVG, BAND = "#52514e", "#e4e3df"             # 5-year average / 5-year range
INK, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#ecebe8", "#c9c8c3"
NAMES = {"DE": "Germany", "EU": "EU"}


def _style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def storage_chart(s: pd.DataFrame):
    areas = [a for a in ("DE", "EU") if a in set(s["area"])]
    fig, axes = plt.subplots(1, len(areas), figsize=(6 * len(areas), 4.3), sharey=True, squeeze=False)
    for ax, area in zip(axes[0], areas):
        a = s[s["area"] == area].copy()
        a["gas_day"] = pd.to_datetime(a["gas_day"])
        latest = a["gas_day"].max()
        start = pd.Timestamp(year=latest.year if latest.month >= 4 else latest.year - 1, month=4, day=1)
        a = a[a["gas_day"] >= start]
        ax.fill_between(a["gas_day"], a["fill_5y_min"], a["fill_5y_max"], color=BAND, lw=0,
                        label="5-year range")
        ax.plot(a["gas_day"], a["fill_5y_avg"], color=AVG, lw=1.2, ls="--", label="5-year average")
        ax.plot(a["gas_day"], a["fill_last_year"], color=ORANGE, lw=1.5, label="Last year")
        ax.plot(a["gas_day"], a["fill_pct"], color=BLUE, lw=2.2, label="This year")
        x = a.iloc[-1]
        ax.annotate(f"{x.fill_pct:.1f}%", (x.gas_day, x.fill_pct), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=10, color=INK, fontweight="bold")
        ax.set_title(f"{NAMES[area]}: {x.fill_pct:.1f}% full, "
                     f"{x.fill_pct - x.fill_5y_avg:+.1f} pp vs 5-year average",
                     loc="left", fontsize=11, color=INK)
        ax.set_xlim(start, latest + pd.Timedelta(days=30))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
        ax.set_ylim(0, 100)
        _style(ax)
    axes[0][0].set_ylabel("Storage fill level (%)", color=MUTED)
    axes[0][0].legend(frameon=False, fontsize=9, loc="lower right")
    fig.text(0.01, 0.01, f"Source: GIE AGSI+. Latest gas day {latest:%d %b %Y}.", fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(DOCS / "storage_vs_5y.png", dpi=160)
    plt.close(fig)


def scenario_chart(sc: pd.DataFrame):
    areas = [a for a in ("DE", "EU") if a in set(sc["area"])]
    if not areas:
        return
    fig, axes = plt.subplots(1, len(areas), figsize=(6 * len(areas), 4.0), sharex=True, squeeze=False)
    for ax, area in zip(axes[0], areas):
        g = sc[sc["area"] == area].sort_values("analog_winter")
        y = range(len(g))
        ax.barh(list(y), g["end_march_fill_pct"], color=BLUE, height=0.6)
        for i, (_, r) in enumerate(g.iterrows()):
            tag = "  (highest draw)" if r.scenario == "Highest-draw winter" else "  (lowest draw)" if r.scenario == "Lowest-draw winter" else ""
            if str(r.storage_exhausted).lower() == "true":
                tag = "  runs out" + tag
            ax.text(r.end_march_fill_pct + 0.8, i, f"{r.end_march_fill_pct:.0f}%{tag}",
                    va="center", fontsize=9, color=INK)
        ax.set_yticks(list(y), [f"like {w}" for w in g["winter_label"]])
        now = g.iloc[0]
        ax.set_title(f"{NAMES[area]}: {now.fill_now_pct:.0f}% full today. Where it ends on 31 March",
                     loc="left", fontsize=11, color=INK)
        ax.set_xlim(0, max(100, g["end_march_fill_pct"].max() + 15))
        _style(ax)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.grid(axis="y", visible=False)
    axes[0][0].set_xlabel("Projected fill level on 31 March (%)", color=MUTED)
    fig.text(0.01, 0.01, "If the rest of this winter withdraws as much gas as each past winter did "
             "over the same dates. Source: GIE AGSI+.", fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(DOCS / "winter_scenarios.png", dpi=160)
    plt.close(fig)


def lng_chart(lng: pd.DataFrame):
    area = "DE" if (lng["area"] == "DE").any() else "EU"
    d = lng[lng["area"] == area].copy()
    if d.empty:
        return
    d["gas_day"] = pd.to_datetime(d["gas_day"])
    d = d[d["gas_day"] >= d["gas_day"].max() - pd.Timedelta(days=730)]
    fig, ax = plt.subplots(figsize=(12, 3.6))
    ax.plot(d["gas_day"], d["send_out_7d_gwh"], color=BLUE, lw=1.8)
    x = d.iloc[-1]
    ax.set_title(f"{NAMES[area]} LNG send-out, 7-day average: {x.send_out_7d_gwh:,.0f} GWh/d",
                 loc="left", fontsize=11, color=INK)
    ax.set_ylabel("GWh per day", color=MUTED)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.set_ylim(bottom=0)
    _style(ax)
    fig.text(0.01, 0.01, "Source: GIE ALSI.", fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(DOCS / "lng_sendout.png", dpi=160)
    plt.close(fig)


SUPPLY = [("norway_gwh", "Norway"), ("netherlands_gwh", "Netherlands"), ("belgium_gwh", "Belgium"),
          ("other_pipeline_gwh", "Other pipelines"), ("production_gwh", "Production"), ("lng_gwh", "LNG"),
          ("storage_withdrawal_gwh", "Storage withdrawal")]
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]


def supply_chart(bal: pd.DataFrame):
    b = bal.sort_values("gas_day").copy()
    b["gas_day"] = pd.to_datetime(b["gas_day"])
    cols = [c for c, _ in SUPPLY] + ["implied_demand_gwh"]
    r = b.set_index("gas_day")[cols].rolling(7, min_periods=1).mean()
    r = r[r.index >= r.index.max() - pd.Timedelta(days=365)]
    fig, ax = plt.subplots(figsize=(12, 4.6))
    ax.stackplot(r.index, *[r[c] for c, _ in SUPPLY], labels=[n for _, n in SUPPLY], colors=SERIES,
                 edgecolor="white", linewidth=0.3)
    ax.plot(r.index, r["implied_demand_gwh"], color=INK, lw=1.4, ls="--", label="Implied demand")
    last = r.iloc[-1]
    share = 100 * last["storage_withdrawal_gwh"] / last[[c for c, _ in SUPPLY]].sum()
    ax.set_title(f"German gas supply by source, 7-day average. Storage covers {share:.0f}% of supply now",
                 loc="left", fontsize=11, color=INK)
    ax.set_ylabel("GWh per day", color=MUTED)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.legend(frameon=False, fontsize=8, ncol=8, loc="upper left", bbox_to_anchor=(0, -0.08))
    _style(ax)
    fig.text(0.01, 0.01, "Sources: ENTSOG (pipelines, production, exports), GIE AGSI+ (storage), GIE ALSI (LNG).",
             fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(DOCS / "supply_mix.png", dpi=160)
    plt.close(fig)


def demand_weather_chart(bal: pd.DataFrame):
    b = bal.dropna(subset=["hdd", "implied_demand_gwh"]).copy()
    if len(b) < 30:
        return
    b["gas_day"] = pd.to_datetime(b["gas_day"])
    latest = b["gas_day"].max()
    winter_start = pd.Timestamp(year=latest.year if latest.month >= 10 else latest.year - 1, month=10, day=1)
    now = b["gas_day"] >= winter_start
    slope, intercept = np.polyfit(b["hdd"], b["implied_demand_gwh"], 1)
    r2 = np.corrcoef(b["hdd"], b["implied_demand_gwh"])[0, 1] ** 2
    fig, ax = plt.subplots(figsize=(7, 4.6))
    ax.scatter(b.loc[~now, "hdd"], b.loc[~now, "implied_demand_gwh"], s=10, color="#d9d8d3", label="Earlier days")
    ax.scatter(b.loc[now, "hdd"], b.loc[now, "implied_demand_gwh"], s=16, color=BLUE, label="This winter")
    xs = np.linspace(0, max(20, b["hdd"].max()), 50)
    ax.plot(xs, intercept + slope * xs, color=AVG, ls="--", lw=1.4, label=f"Fit: {slope:,.0f} GWh/d per HDD")
    ax.set_title(f"Demand vs heating degree days (R² {r2:.2f})", loc="left", fontsize=11, color=INK)
    ax.set_xlabel("Heating degree days (base 15 °C)", color=MUTED)
    ax.set_ylabel("Implied demand, GWh/d", color=MUTED)
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    _style(ax)
    fig.text(0.01, 0.01, "Sources: ENTSOG, GIE AGSI+/ALSI, Open-Meteo.", fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(DOCS / "demand_weather.png", dpi=160)
    plt.close(fig)


def main():
    DOCS.mkdir(exist_ok=True)
    storage_chart(pd.read_csv(DATA / "gold_storage_daily.csv"))
    scenario_chart(pd.read_csv(DATA / "gold_winter_scenarios.csv"))
    lng_chart(pd.read_csv(DATA / "gold_lng_daily.csv"))
    if (DATA / "gold_balance_daily.csv").exists():
        bal = pd.read_csv(DATA / "gold_balance_daily.csv")
        supply_chart(bal)
        demand_weather_chart(bal)
    print(f"Charts written to {DOCS}")


if __name__ == "__main__":
    main()
