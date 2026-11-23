-- =====================================================================
-- Germany Winter Gas Security Monitor - SQL transformation layer
-- Portable: runs unchanged in SQLite (local, via ingest.py) and in
-- Databricks SQL (after uploading raw_storage.csv / raw_lng.csv as tables).
-- Layers: raw_* (as delivered) -> silver_* (clean + features) -> gold_* (dashboard-ready)
-- =====================================================================

-- 1) SILVER: storage with a 7-day rolling net withdrawal
DROP TABLE IF EXISTS silver_storage;
CREATE TABLE silver_storage AS
SELECT
    area,
    gas_day,
    yr,
    md,
    gas_in_storage_twh,
    wgv_twh,
    fill_pct,
    injection_gwh,
    withdrawal_gwh,
    net_withdrawal_gwh,
    AVG(net_withdrawal_gwh) OVER (
        PARTITION BY area ORDER BY gas_day
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS net_withdrawal_7d_gwh
FROM raw_storage
WHERE fill_pct IS NOT NULL;

-- 2) GOLD: daily storage vs the same calendar day in the previous 5 years
DROP TABLE IF EXISTS gold_storage_daily;
CREATE TABLE gold_storage_daily AS
SELECT
    s.area,
    s.gas_day,
    s.yr,
    s.md,
    s.gas_in_storage_twh,
    s.wgv_twh,
    s.fill_pct,
    s.injection_gwh,
    s.withdrawal_gwh,
    s.net_withdrawal_gwh,
    s.net_withdrawal_7d_gwh,
    AVG(h.fill_pct)                                       AS fill_5y_avg,
    MIN(h.fill_pct)                                       AS fill_5y_min,
    MAX(h.fill_pct)                                       AS fill_5y_max,
    MAX(CASE WHEN h.yr = s.yr - 1 THEN h.fill_pct END)    AS fill_last_year,
    s.fill_pct - AVG(h.fill_pct)                          AS fill_vs_5y_pp,
    CASE WHEN s.net_withdrawal_7d_gwh > 0
         THEN s.gas_in_storage_twh * 1000 / s.net_withdrawal_7d_gwh
    END                                                   AS days_of_cover
FROM silver_storage s
LEFT JOIN silver_storage h
       ON h.area = s.area
      AND h.md   = s.md
      AND h.yr BETWEEN s.yr - 5 AND s.yr - 1
WHERE s.yr >= (SELECT MIN(yr) FROM silver_storage) + 5
GROUP BY s.area, s.gas_day, s.yr, s.md, s.gas_in_storage_twh, s.wgv_twh, s.fill_pct,
         s.injection_gwh, s.withdrawal_gwh, s.net_withdrawal_gwh, s.net_withdrawal_7d_gwh;

-- 3) GOLD: LNG terminal send-out and utilisation
DROP TABLE IF EXISTS gold_lng_daily;
CREATE TABLE gold_lng_daily AS
SELECT
    area,
    gas_day,
    yr,
    md,
    send_out_gwh,
    inventory_gwh,
    dtrs_gwh,
    CASE WHEN dtrs_gwh > 0 THEN 100.0 * send_out_gwh / dtrs_gwh END AS sendout_utilisation_pct,
    AVG(send_out_gwh) OVER (
        PARTITION BY area ORDER BY gas_day
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS send_out_7d_gwh
FROM raw_lng
WHERE send_out_gwh IS NOT NULL;

-- 4) SILVER: winter calendar (Oct-Mar). A winter is labelled by the year it starts in;
--    wkey orders days inside a winter: '0MM-DD' for Oct-Dec, '1MM-DD' for Jan-Mar.
DROP TABLE IF EXISTS silver_winter;
CREATE TABLE silver_winter AS
SELECT
    area,
    gas_day,
    yr,
    gas_in_storage_twh,
    wgv_twh,
    fill_pct,
    net_withdrawal_gwh,
    CASE WHEN md >= '10-01' THEN yr ELSE yr - 1 END          AS winter,
    CASE WHEN md >= '10-01' THEN '0' || md ELSE '1' || md END AS wkey
FROM silver_storage
WHERE md >= '10-01' OR md <= '03-31';

-- 5) GOLD: end-of-March scenarios. Take today's stock and subtract what each past
--    winter withdrew between this calendar day and 31 March.
DROP TABLE IF EXISTS gold_winter_scenarios;
CREATE TABLE gold_winter_scenarios AS
WITH latest AS (
    SELECT w.*
    FROM silver_winter w
    JOIN (SELECT area, MAX(gas_day) AS gas_day FROM silver_storage GROUP BY area) m
      ON w.area = m.area AND w.gas_day = m.gas_day
),
remaining_draw AS (
    SELECT
        h.area,
        h.winter                         AS analog_winter,
        SUM(h.net_withdrawal_gwh) / 1000.0 AS remaining_draw_twh
    FROM silver_winter h
    JOIN latest l
      ON h.area = l.area
     AND h.winter < l.winter
     AND h.wkey > l.wkey
    WHERE h.winter >= (SELECT MIN(yr) FROM silver_storage)
    GROUP BY h.area, h.winter
)
SELECT
    l.area,
    l.gas_day                                               AS as_of_gas_day,
    l.fill_pct                                              AS fill_now_pct,
    l.gas_in_storage_twh                                    AS stock_now_twh,
    d.analog_winter,
    d.remaining_draw_twh,
    l.gas_in_storage_twh - d.remaining_draw_twh             AS end_march_twh,
    100.0 * (l.gas_in_storage_twh - d.remaining_draw_twh) / l.wgv_twh AS end_march_fill_pct
FROM latest l
JOIN remaining_draw d ON d.area = l.area;
