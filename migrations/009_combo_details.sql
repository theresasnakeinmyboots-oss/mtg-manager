CREATE TABLE IF NOT EXISTS combo_details_cache (
    combo_id    TEXT PRIMARY KEY,
    detail_json TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);
