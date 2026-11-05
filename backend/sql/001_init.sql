-- OzWatch schema. Idempotent: safe to run on every start-up.
CREATE EXTENSION IF NOT EXISTS postgis;

-- The current position/state of everything on the map: one row per (layer, id).
CREATE TABLE IF NOT EXISTS features_latest (
    layer         text        NOT NULL,
    id            text        NOT NULL,
    geom          geometry(Point, 4326) NOT NULL,
    observed_at   timestamptz NOT NULL,          -- time according to the source
    last_seen_at  timestamptz NOT NULL DEFAULT now(),  -- last poll that returned it
    properties    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (layer, id)
);
CREATE INDEX IF NOT EXISTS features_latest_geom_idx ON features_latest USING gist (geom);

-- Short rolling history (default 24h) for moving things, used by the analytics panel.
CREATE TABLE IF NOT EXISTS features_history (
    layer        text        NOT NULL,
    id           text        NOT NULL,
    geom         geometry(Point, 4326) NOT NULL,
    observed_at  timestamptz NOT NULL,
    properties   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (layer, id, observed_at)
);
CREATE INDEX IF NOT EXISTS features_history_time_idx ON features_history (layer, observed_at);
CREATE INDEX IF NOT EXISTS features_history_geom_idx ON features_history USING gist (geom);

-- One row per poll: powers /health and lets us report connector reliability.
CREATE TABLE IF NOT EXISTS connector_runs (
    run_id       bigserial   PRIMARY KEY,
    layer        text        NOT NULL,
    started_at   timestamptz NOT NULL,
    finished_at  timestamptz NOT NULL,
    ok           boolean     NOT NULL,
    n_features   integer     NOT NULL DEFAULT 0,
    error        text
);
CREATE INDEX IF NOT EXISTS connector_runs_layer_time_idx ON connector_runs (layer, started_at DESC);
