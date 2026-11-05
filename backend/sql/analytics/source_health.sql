-- Reliability of each data source over the last 24 h, from connector_runs.
-- (Streams log one row per minute: ok = connected during that minute.)
WITH recent AS (
    SELECT * FROM connector_runs WHERE started_at >= now() - interval '24 hours'
),
last_failure AS (
    SELECT DISTINCT ON (layer) layer, error, started_at
    FROM recent
    WHERE NOT ok
    ORDER BY layer, started_at DESC
)
SELECT r.layer,
       count(*)                                               AS runs,
       count(*) FILTER (WHERE r.ok)                           AS ok_runs,
       round(100.0 * count(*) FILTER (WHERE r.ok) / count(*), 1) AS success_pct,
       round(avg(r.n_features) FILTER (WHERE r.ok))            AS avg_features,
       max(r.started_at)                                      AS last_run,
       f.error                                                AS last_error,
       f.started_at                                           AS last_error_at
FROM recent r
LEFT JOIN last_failure f USING (layer)
GROUP BY r.layer, f.error, f.started_at
ORDER BY r.layer;
