# Power BI build steps (about 25 minutes)

> **On a Mac:** Power BI Desktop is Windows-only. Use a Windows PC or VM, the Power BI web service
> with your university account, or **Tableau Public**. Tableau Public is free, runs on Mac, and the
> job ad names it as an alternative. The same three CSVs and the same page layout work in Tableau.

## 1. Load data (3 min)
Get data → Text/CSV → load these three files from the `data` folder:
- `gold_storage_daily.csv`
- `gold_lng_daily.csv`
- `gold_winter_scenarios.csv`

Also load `gold_balance_daily.csv` (daily supply by source, exports, implied demand, temperature)
and `gold_weather_daily.csv` (temperature vs normal, incl. forecast) for a supply-mix page:
a stacked area of the source columns over `gas_day`, plus a scatter of `implied_demand_gwh` vs `hdd`.

Check that `gas_day` is set to the **Date** type.
*Later:* switch each source to **Get data → Web** using the raw GitHub URL
(`https://raw.githubusercontent.com/<you>/<repo>/main/data/gold_storage_daily.csv`).
Then every refresh pulls the data that GitHub Actions updates daily.

## 2. Model (3 min): one slicer for all pages
New table:
```DAX
dim_area = DISTINCT ( UNION ( DISTINCT ( gold_storage_daily[area] ), DISTINCT ( gold_lng_daily[area] ) ) )
```
Model view: link `dim_area[area]` → `area` in all three tables (one-to-many, single direction).
Use `dim_area[area]` in the slicer and set it to **single select**, defaulting to DE.

## 3. Measures (4 min), in gold_storage_daily
```DAX
Latest Gas Day = MAX ( gold_storage_daily[gas_day] )

Fill % =
VAR d = [Latest Gas Day]
RETURN CALCULATE ( AVERAGE ( gold_storage_daily[fill_pct] ), gold_storage_daily[gas_day] = d )

vs 5Y Avg (pp) =
VAR d = [Latest Gas Day]
RETURN CALCULATE ( AVERAGE ( gold_storage_daily[fill_vs_5y_pp] ), gold_storage_daily[gas_day] = d )

vs Last Year (pp) =
VAR d = [Latest Gas Day]
RETURN CALCULATE (
    AVERAGE ( gold_storage_daily[fill_pct] ) - AVERAGE ( gold_storage_daily[fill_last_year] ),
    gold_storage_daily[gas_day] = d )

Patterns Emptying Storage =
CALCULATE ( COUNTROWS ( gold_winter_scenarios ), gold_winter_scenarios[storage_exhausted] = TRUE () )
```
(If `storage_exhausted` loads as text, compare with `"True"` instead of `TRUE ()`.)
Format the `pp` measures as `+0.0;-0.0;0.0`, so the sign always shows.

## 4. Page 1: "Storage" (8 min)
- **Slicer**: `dim_area[area]`
- **4 cards**: Fill %, vs 5Y Avg (pp), vs Last Year (pp), Patterns Emptying Storage ("of 8 past winters")
- **Line chart** (main visual): X = `gas_day`, Y = `fill_pct`, `fill_5y_avg`, `fill_5y_min`, `fill_5y_max`.
  Filter: gas_day on or after 2026-04-01, the current storage year.
  Make `fill_pct` thick and dark; `fill_5y_avg` grey and dashed; min/max thin light grey.
- **Column chart**: X = `gas_day`, Y = `net_withdrawal_gwh`. Use a relative date filter for the last 60 days.

## 5. Page 2: "LNG & winter outlook" (7 min)
- **Line chart**: `gold_lng_daily` X = `gas_day`, Y = `send_out_7d_gwh`, filtered to the last 12 months
- **Bar chart**: `gold_winter_scenarios` Y = `winter_label`, X = `end_march_fill_pct`.
  Colour bars by `scenario` (conditional formatting).
  Title: *"Where storage ends March if this winter repeats a past one"*
- **Text box**: paste the text from `data/market_note.md`

## Tips that make it look like an analyst built it
- Write titles as **conclusions**, not labels. For example: *"German storage is 9 pp below its 5-year average"*,
  not *"Storage fill level"*.
- Use one neutral palette and one highlight colour (the current year). No 3D, no pie charts.
- Put *"Source: GIE AGSI+ / ALSI"* in the footer of every page. GIE requires attribution.
- Show the last update date on every page with a card for `[Latest Gas Day]`.

## Optional: Databricks (10 min)
1. Sign up for **Databricks Free Edition** (free, ready immediately).
2. Upload `data/raw_storage.csv` and `data/raw_lng.csv` as tables named `raw_storage` and `raw_lng`.
   Use the "upload data / create table" option. Keep `md` as a string column.
3. Open the SQL editor, paste `transform.sql` and run all. The same SQL runs there unchanged.
4. Run `SELECT * FROM gold_winter_scenarios ORDER BY area, end_march_fill_pct` and take a screenshot for the README.

Interview line: *Free Edition restricts outbound internet access, so data is fetched by a scheduled
GitHub Actions job. Databricks is the SQL and analytics layer.*
