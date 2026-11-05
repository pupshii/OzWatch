-- Vessels near Australia's major ports right now.
-- Each ship is assigned to its NEAREST port within 30 km (DISTINCT ON), so
-- ships between two close ports (e.g. Sydney Harbour and Port Botany) are not
-- counted twice.
WITH ports(port, lon, lat) AS (
    VALUES ('Sydney Harbour', 151.21, -33.85), ('Port Botany', 151.21, -33.97),
           ('Newcastle', 151.79, -32.92),      ('Port Kembla', 150.90, -34.47),
           ('Melbourne', 144.92, -37.84),      ('Geelong', 144.39, -38.12),
           ('Brisbane', 153.17, -27.38),       ('Gladstone', 151.27, -23.83),
           ('Hay Point', 149.30, -21.28),      ('Townsville', 146.83, -19.25),
           ('Darwin', 130.85, -12.47),         ('Port Hedland', 118.58, -20.31),
           ('Dampier', 116.71, -20.65),        ('Fremantle', 115.74, -32.05),
           ('Adelaide', 138.48, -34.78),       ('Hobart', 147.34, -42.88)
),
port_geo AS (
    SELECT port, ST_SetSRID(ST_MakePoint(lon, lat), 4326) AS geom FROM ports
),
nearest AS (
    SELECT DISTINCT ON (s.id)
           s.id, p.port, (s.properties ->> 'moving')::boolean AS moving
    FROM features_latest s
    JOIN port_geo p
      ON s.geom && ST_Expand(p.geom, 0.35)
     AND ST_DWithin(s.geom::geography, p.geom::geography, 30000)
    WHERE s.layer = 'ships'
    ORDER BY s.id, ST_Distance(s.geom::geography, p.geom::geography)
)
SELECT port,
       count(*)                                  AS ships,
       count(*) FILTER (WHERE NOT moving)        AS stationary
FROM nearest
GROUP BY port
ORDER BY ships DESC, port
LIMIT 10;
