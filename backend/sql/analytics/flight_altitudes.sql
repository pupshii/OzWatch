-- Airborne aircraft right now, in 5,000 ft altitude bands (0-45,000 ft).
-- width_bucket puts anything above 45,000 ft in bucket 10.
WITH bands AS (
    SELECT generate_series(1, 10) AS band
),
airborne AS (
    -- greatest(..., 0): a slightly negative barometric altitude still counts in the lowest band
    SELECT width_bucket(greatest((properties ->> 'altitude_ft')::numeric, 0), 0, 45000, 9) AS band
    FROM features_latest
    WHERE layer = 'flights'
      AND (properties ->> 'on_ground')::boolean IS FALSE
      AND properties ->> 'altitude_ft' IS NOT NULL
)
SELECT b.band,
       (b.band - 1) * 5000 AS from_ft,
       CASE WHEN b.band = 10 THEN NULL ELSE b.band * 5000 END AS to_ft,
       count(a.band) AS aircraft
FROM bands b
LEFT JOIN airborne a USING (band)
GROUP BY b.band
ORDER BY b.band;
