CREATE TABLE IF NOT EXISTS cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scryfall_id     TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    set_code        TEXT NOT NULL,
    set_name        TEXT NOT NULL,
    collector_number TEXT,
    mana_cost       TEXT,
    cmc             REAL DEFAULT 0,
    colors          TEXT,
    color_identity  TEXT,
    type_line       TEXT,
    oracle_text     TEXT,
    power           TEXT,
    toughness       TEXT,
    rarity          TEXT,
    legalities      TEXT,
    image_uri_normal TEXT,
    image_uri_small TEXT,
    enriched_at     TEXT
);

CREATE TABLE IF NOT EXISTS collection (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         INTEGER REFERENCES cards(id),
    scryfall_id     TEXT,
    name            TEXT NOT NULL,
    edition         TEXT,
    card_number     TEXT,
    count           INTEGER DEFAULT 1,
    tradelist_count INTEGER DEFAULT 0,
    condition       TEXT,
    language        TEXT DEFAULT 'English',
    foil            TEXT,
    signed          INTEGER DEFAULT 0,
    artist_proof    INTEGER DEFAULT 0,
    altered_art     INTEGER DEFAULT 0,
    misprint        INTEGER DEFAULT 0,
    promo           INTEGER DEFAULT 0,
    textless        INTEGER DEFAULT 0,
    my_price        REAL,
    imported_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scryfall_cache (
    cache_key       TEXT PRIMARY KEY,
    response        TEXT NOT NULL,
    fetched_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_collection_name   ON collection(name);
CREATE INDEX IF NOT EXISTS idx_collection_edition ON collection(edition);
CREATE INDEX IF NOT EXISTS idx_collection_card_id ON collection(card_id);
CREATE INDEX IF NOT EXISTS idx_cards_scryfall_id ON cards(scryfall_id);
