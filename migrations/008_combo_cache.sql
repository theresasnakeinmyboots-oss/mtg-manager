CREATE TABLE IF NOT EXISTS combo_cache (
    slug        TEXT PRIMARY KEY,
    combos_json TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);
