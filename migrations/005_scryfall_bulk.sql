CREATE TABLE IF NOT EXISTS scryfall_bulk (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scryfall_id     TEXT NOT NULL,
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
    flavor_text     TEXT,
    power           TEXT,
    toughness       TEXT,
    rarity          TEXT,
    legalities      TEXT,
    image_uri_normal TEXT,
    image_uri_small  TEXT,
    artist          TEXT,
    price_usd       REAL,
    price_usd_foil  REAL,
    price_eur       REAL,
    price_eur_foil  REAL,
    UNIQUE(scryfall_id)
);

CREATE INDEX IF NOT EXISTS idx_bulk_name_set ON scryfall_bulk(name, set_code);
CREATE INDEX IF NOT EXISTS idx_bulk_name ON scryfall_bulk(name);
