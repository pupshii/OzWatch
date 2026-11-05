-- Distinct aircraft seen within 50 km of the Sydney CBD, per hour, last 24 h.
-- Hours with no data still appear (generate_series + LEFT JOIN = gap filling),
-- so the chart's x-axis is always a full 24 hours.
-- Buckets are UTC hours; Sydney's offset is a whole number of hours, so they
-- line up with local hours too (the browser labels them in local time).
WITH params AS (
    SELECT ST_SetSRID(ST_MakePoint(151.2093, -33.8688), 4326) AS cbd,
           50000.0 AS radius_m
),
hours AS (
    SELECT generate_series(
        date_trunc('hour', now()) - interval '23 hours',
        date_trunc('hour', now()),
        interval '1 hour'
    ) AS hour
),
near_sydney AS (
    SELECT date_trunc('hour', h.observed_at) AS hour,
           h.id,
           (h.properties ->> 'on_ground')::boolean AS on_ground
    FROM features_history h, params p
    WHERE h.layer = 'flights'
      AND h.observed_at >= date_trunc('hour', now()) - interval '23 hours'
      -- cheap index-assisted box test first, then the exact distance in metres
      AND h.geom && ST_Expand(p.cbd, 0.6)
      AND ST_DWithin(h.geom::geography, p.cbd::geography, p.radius_m)
)
SELECT hours.hour,
       count(DISTINCT n.id)                                   AS aircraft,
       count(DISTINCT n.id) FILTER (WHERE NOT n.on_ground)    AS airborne
FROM hours
LEFT JOIN near_sydney n USING (hour)
GROUP BY hours.hour
ORDER BY hours.hour;
