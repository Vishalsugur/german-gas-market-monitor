-- =====================================================================
-- Market fundamentals: weather, pipeline flows and the German gas balance
-- Portable: runs unchanged in SQLite (via ingest.py) and Databricks SQL.
-- Inputs: raw_flows (ENTSOG), raw_weather (Open-Meteo), raw_storage and raw_lng (GIE)
-- =====================================================================

-- 1) SILVER: temperature with heating degree days (base 15 °C, the usual German heating threshold)
DROP TABLE IF EXISTS silver_weather;
CREATE TABLE silver_weather AS
SELECT
    date,
    yr,
    md,
    temp_c,
    is_forecast,
    CASE WHEN temp_c < 15 THEN 15 - temp_c ELSE 0 END AS hdd
FROM raw_weather
WHERE temp_c IS NOT NULL;

-- 2) GOLD: temperature vs the 10-year normal for the same calendar day, incl. the 14-day forecast
DROP TABLE IF EXISTS gold_weather_daily;
CREATE TABLE gold_weather_daily AS
SELECT
    w.date,
    w.temp_c,
    w.hdd,
    w.is_forecast,
    AVG(h.temp_c)            AS normal_temp_c,
    AVG(h.hdd)               AS normal_hdd,
    w.temp_c - AVG(h.temp_c) AS temp_anomaly_c
FROM silver_weather w
LEFT JOIN silver_weather h
       ON h.md = w.md
      AND h.yr BETWEEN w.yr - 10 AND w.yr - 1
      AND h.is_forecast = 0
WHERE w.date >= '2025-01-01'
GROUP BY w.date, w.temp_c, w.hdd, w.is_forecast;

-- 3) SILVER: cross-border flows pivoted to one row per gas day (GWh/d)
DROP TABLE IF EXISTS silver_flows;
CREATE TABLE silver_flows AS
SELECT
    gas_day,
    SUM(CASE WHEN direction = 'entry' AND source = 'Norway'      THEN gwh ELSE 0 END) AS norway_gwh,
    SUM(CASE WHEN direction = 'entry' AND source = 'Netherlands' THEN gwh ELSE 0 END) AS netherlands_gwh,
    SUM(CASE WHEN direction = 'entry' AND source = 'Belgium'     THEN gwh ELSE 0 END) AS belgium_gwh,
    SUM(CASE WHEN direction = 'entry' AND source NOT IN ('Norway', 'Netherlands', 'Belgium', 'Production')
             THEN gwh ELSE 0 END)                                                    AS other_pipeline_gwh,
    SUM(CASE WHEN direction = 'entry' AND source = 'Production'  THEN gwh ELSE 0 END) AS production_gwh,
    SUM(CASE WHEN direction = 'exit'  AND source <> 'Production' THEN gwh ELSE 0 END) AS exports_gwh
FROM raw_flows
GROUP BY gas_day;

-- 4) GOLD: daily German gas balance. Supply = pipelines + LNG + production + storage withdrawal.
--    Implied demand = supply - exports - storage injection (includes linepack changes and metering gaps).
DROP TABLE IF EXISTS gold_balance_daily;
CREATE TABLE gold_balance_daily AS
WITH joined AS (
    SELECT
        f.gas_day,
        s.yr,
        s.md,
        f.norway_gwh,
        f.netherlands_gwh,
        f.belgium_gwh,
        f.other_pipeline_gwh,
        COALESCE(l.send_out_gwh, 0) AS lng_gwh,
        f.production_gwh,
        s.withdrawal_gwh            AS storage_withdrawal_gwh,
        s.injection_gwh             AS storage_injection_gwh,
        f.exports_gwh,
        w.temp_c,
        w.hdd
    FROM silver_flows f
    JOIN raw_storage s     ON s.area = 'DE' AND s.gas_day = f.gas_day
    LEFT JOIN raw_lng l    ON l.area = 'DE' AND l.gas_day = f.gas_day
    LEFT JOIN silver_weather w ON w.date = f.gas_day
)
SELECT
    *,
    norway_gwh + netherlands_gwh + belgium_gwh + other_pipeline_gwh + lng_gwh + production_gwh
        + storage_withdrawal_gwh                                              AS total_supply_gwh,
    norway_gwh + netherlands_gwh + belgium_gwh + other_pipeline_gwh + lng_gwh + production_gwh
        + storage_withdrawal_gwh - exports_gwh - storage_injection_gwh        AS implied_demand_gwh
FROM joined;
