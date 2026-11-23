# German Gas Market Monitor 2026/27

**Question: will German gas storage last the winter, and what drives it?**
An automated Python + SQL pipeline that combines four public sources. It builds a daily German gas balance covering supply by source, implied demand, storage and weather, and feeds a market analytics dashboard:
- **Storage:** GIE AGSI+
- **LNG terminals:** GIE ALSI
- **Cross-border pipeline flows:** ENTSOG
- **Temperatures with a 14-day forecast:** Open-Meteo

**[Live dashboard](https://claude.ai/artifact/YGh86bD4oUmVyN4r9Qnem5)**

## Key findings (gas day 22 Nov 2026)
- **German storage is at a 5-year low.** It is 56.5% full (140 TWh), 30.1 pp below the 5-year average for this date and below the lowest level of any of the last five years (69.7%). It is 14.7 pp below the same day last year.
- **The EU is in the same position, but less severe.** It is 68.9% full, 17.5 pp below its 5-year average and also below its 5-year low (71.9%).
- **End-of-March outlook.** Suppose the rest of this winter withdraws as much gas as one of the last 8 winters did. German storage then ends March between 0% and 30% full (median 17%). **2 of 8 patterns (2020/21, 2024/25) empty German storage before 31 March.** For the EU the range is 7–31% (median 26%).
- **LNG terminals have spare capacity.** German terminals sent out 338 GWh/d over the last 7 days, only 34% of their declared send-out capacity. There is room for more imports if cargoes are available.

## Charts
![Supply mix](docs/supply_mix.png)
![Demand vs weather](docs/demand_weather.png)
![Storage vs 5-year range](docs/storage_vs_5y.png)
![End-of-March scenarios](docs/winter_scenarios.png)
![LNG send-out](docs/lng_sendout.png)

## How it works
```
GIE AGSI+ / ALSI API     ENTSOG API             Open-Meteo API
        │  ingest.py + market.py (Python: paginated, incremental extract; Norway de-duplication)
        ▼
raw_storage · raw_lng · raw_flows · raw_weather
        │  transform.sql + transform_market.sql (SQL: raw → silver → gold; SQLite locally, Databricks-portable)
        ▼
gold_storage_daily · gold_lng_daily · gold_winter_scenarios · gold_balance_daily · gold_weather_daily
        │  demand–weather regression, market note, charts
        ▼
        │
        ▼
Dashboard (web) / Power BI or Tableau (see POWERBI_STEPS.md)
```
**Automation:** a GitHub Actions job runs the pipeline daily at 21:15 CET, after GIE publishes at 19:30 CET.
It commits the refreshed data and charts.

**Validation:** the pipeline's output matches published figures. It shows EU storage peaking at 74.96% on 27 Oct 2026, the EU at 71.23% on 11 Nov 2026 (a record low for that date), and Germany at 61.0% on 1 Nov 2026.

## Method
| Metric | Definition |
|---|---|
| 5-year band | Fill level on the same calendar day in each of the previous 5 years (avg / min / max) |
| End-of-March scenarios | Today's stock minus what each past winter (2018/19 onwards) withdrew between this calendar day and 31 March. Highest and lowest draw are ranked by gas withdrawn |
| Pipeline imports | ENTSOG daily physical flows at German border points by neighbour. Norwegian flows (Dornum, Emden) are reported by several German operators for the same gas; duplicate pairs are counted once |
| Implied demand | Pipeline imports + LNG + production + storage withdrawal − exports − storage injection |
| Weather model | Linear fit of implied demand on heating degree days (base 15 °C, 5-city average), applied to the 14-day forecast and the 10-year normal |
| LNG capacity use | 7-day average send-out ÷ declared total reference send-out (ALSI `dtrs`) |
| Market note | Facts computed in Python. An optional LLM step drafts the wording from those facts only, and the facts stay attached for audit |

## Limitations and next steps
- The scenarios assume past withdrawal patterns. They don't model weather, today's LNG supply or demand changes.
  Next: regress withdrawals on heating degree days (weather data) for a weather-driven forecast.
- Add pipeline flows (ENTSOG Transparency Platform) and a facility- and company-level storage view.

## Run it
```bash
pip install -r requirements.txt
echo "GIE_API_KEY=your_key" > .env     # free key from your account on https://agsi.gie.eu
python ingest.py                       # first run ~3–5 min (ENTSOG history); later runs only fetch new days
```
For daily automation, add `GIE_API_KEY` as a repository secret
(Settings → Secrets and variables → Actions). Optionally also add `ANTHROPIC_API_KEY`.

## Data
Source: **GIE AGSI+** (gas storage) and **GIE ALSI** (LNG terminals), Gas Infrastructure Europe.
https://agsi.gie.eu · https://alsi.gie.eu
