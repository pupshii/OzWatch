-- Active NSW RFS incidents per hour over the last 24 h.
-- Every successful poll logs how many incidents the feed listed
-- (connector_runs.n_features); we take the highest count in each hour.
-- Hours with no successful poll are NULL (unknown), not 0.
WITH hours AS (
    SELECT generate_series(
        date_trunc('hour', now()) - interval '23 hours',
        date_trunc('hour', now()),
        interval '1 hour'
    ) AS hour
),
polls AS (
    SELECT date_trunc('hour', started_at) AS hour, max(n_features) AS incidents
    FROM connector_runs
    WHERE layer = 'fires' AND ok
      AND started_at >= date_trunc('hour', now()) - interval '23 hours'
    GROUP BY 1
)
SELECT hours.hour, polls.incidents
FROM hours
LEFT JOIN polls USING (hour)
ORDER BY hours.hour;
