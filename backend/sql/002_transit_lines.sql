-- Train, metro and light rail line shapes built from TfNSW GTFS timetables (weekly).

-- Staging: raw shape points streamed in with COPY, then turned into lines in SQL.
CREATE UNLOGGED TABLE IF NOT EXISTS transit_shape_points (
    feed      text    NOT NULL,
    shape_id  text    NOT NULL,
    seq       integer NOT NULL,
    lon       double precision NOT NULL,
    lat       double precision NOT NULL
);
CREATE INDEX IF NOT EXISTS transit_shape_points_idx ON transit_shape_points (feed, shape_id);

-- Staging: which line (T1, M1, L2...) each shape belongs to.
CREATE UNLOGGED TABLE IF NOT EXISTS transit_shape_routes (
    feed      text NOT NULL,
    shape_id  text NOT NULL,
    line      text NOT NULL,
    name      text,
    color     text,
    PRIMARY KEY (feed, shape_id)
);

-- Result: one merged, simplified geometry per line.
CREATE TABLE IF NOT EXISTS transit_lines (
    feed      text NOT NULL,
    mode      text NOT NULL,          -- train | metro | lightrail
    line      text NOT NULL,          -- route short name, e.g. T1
    name      text,                   -- route long name
    color     text,                   -- official route colour, hex without '#'
    geom      geometry(MultiLineString, 4326) NOT NULL,
    built_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (feed, line)
);
