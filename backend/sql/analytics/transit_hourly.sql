-- Public transport vehicles in service per hour over the last 24 h.
-- Each successful poll logs how many vehicles were reporting (connector_runs.n_features);
-- the hourly maximum shows the morning and evening peaks. No poll that hour = NULL (a gap).
WITH hours AS (
    SELECT generate_series(
        date_trunc('hour', now()) - interval '23 hours',
        date_trunc('hour', now()),
        interval '1 hour'
    ) AS hour
),
polls AS (
    SELECT date_trunc('hour', started_at) AS hour, max(n_features) AS vehicles
    FROM connector_runs
    WHERE layer = 'transit' AND ok
      AND started_at >= date_trunc('hour', now()) - interval '23 hours'
    GROUP BY 1
)
SELECT hours.hour, polls.vehicles
FROM hours
LEFT JOIN polls USING (hour)
ORDER BY hours.hour;
