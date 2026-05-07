-- Named collections
CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    is_staging  INTEGER DEFAULT 0,   -- 1 = temporary import staging collection
    created_at  TEXT NOT NULL
);

-- Link collection rows to a named collection
ALTER TABLE collection ADD COLUMN collection_id INTEGER REFERENCES collections(id);

-- Default collection for any existing rows
INSERT INTO collections (name, description, is_staging, created_at)
VALUES ('Default', 'Main collection', 0, datetime('now'));

UPDATE collection SET collection_id = (SELECT id FROM collections WHERE name = 'Default');

CREATE INDEX IF NOT EXISTS idx_collection_collection_id ON collection(collection_id);
