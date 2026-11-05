-- Vessels on the map right now, by type, split into moving and stationary.
SELECT coalesce(properties ->> 'category', 'Unknown')                      AS category,
       count(*)                                                            AS ships,
       count(*) FILTER (WHERE (properties ->> 'moving')::boolean)          AS moving
FROM features_latest
WHERE layer = 'ships'
GROUP BY 1
ORDER BY ships DESC, category;
