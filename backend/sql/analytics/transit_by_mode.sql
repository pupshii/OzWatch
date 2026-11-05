-- Sydney public transport vehicles reporting a position right now, by mode.
SELECT CASE properties ->> 'mode'
           WHEN 'bus' THEN 'Buses'
           WHEN 'train' THEN 'Trains'
           WHEN 'metro' THEN 'Metro'
           WHEN 'ferry' THEN 'Ferries'
           WHEN 'lightrail' THEN 'Light rail'
           ELSE coalesce(properties ->> 'mode', 'Other')
       END        AS mode,
       count(*)   AS vehicles
FROM features_latest
WHERE layer = 'transit'
GROUP BY 1
ORDER BY vehicles DESC, mode;
