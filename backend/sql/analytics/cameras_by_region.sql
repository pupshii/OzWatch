-- Traffic cameras by TfNSW region code.
SELECT coalesce(properties ->> 'region', 'Unknown') AS region,
       count(*)                                     AS cameras
FROM features_latest
WHERE layer = 'cameras'
GROUP BY 1
ORDER BY cameras DESC, region;
