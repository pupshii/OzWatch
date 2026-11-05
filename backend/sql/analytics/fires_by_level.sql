-- NSW RFS incidents on the map right now, by alert level (most urgent first).
SELECT properties ->> 'alert_level' AS alert_level,
       count(*)                     AS incidents,
       count(*) FILTER (WHERE (properties ->> 'is_fire')::boolean) AS fires
FROM features_latest
WHERE layer = 'fires'
GROUP BY 1
ORDER BY array_position(
    ARRAY['Emergency Warning', 'Watch and Act', 'Advice', 'Not Applicable'],
    properties ->> 'alert_level'
);
