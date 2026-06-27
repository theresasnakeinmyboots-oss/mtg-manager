CREATE TABLE IF NOT EXISTS edhrec_cache (
    slug        TEXT PRIMARY KEY,          -- e.g. "ashling-rekindled"
    data        TEXT NOT NULL,             -- full JSON from EDHREC
    num_decks   INTEGER DEFAULT 0,         -- denormalised for quick display
    fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edhrec_cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT NOT NULL REFERENCES edhrec_cache(slug) ON DELETE CASCADE,
    scryfall_id     TEXT,                  -- matched against scryfall_bulk
    name            TEXT NOT NULL,
    sanitized       TEXT NOT NULL,
    tag             TEXT NOT NULL,         -- cardlist tag: creatures, instants, etc.
    synergy         REAL DEFAULT 0,
    inclusion       INTEGER DEFAULT 0,
    num_decks       INTEGER DEFAULT 0,
    potential_decks INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_edhrec_cards_slug        ON edhrec_cards(slug);
CREATE INDEX IF NOT EXISTS idx_edhrec_cards_scryfall_id ON edhrec_cards(scryfall_id);
CREATE INDEX IF NOT EXISTS idx_edhrec_cards_synergy     ON edhrec_cards(slug, synergy DESC);
