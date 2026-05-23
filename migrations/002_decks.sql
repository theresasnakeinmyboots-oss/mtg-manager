CREATE TABLE IF NOT EXISTS decks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    format      TEXT NOT NULL,  -- standard|commander|modern|pioneer|legacy
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deck_cards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id      INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    scryfall_id  TEXT NOT NULL,
    name         TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 1,
    board        TEXT NOT NULL DEFAULT 'main',  -- main|side|commander
    added_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deck_cards_deck_id    ON deck_cards(deck_id);
CREATE INDEX IF NOT EXISTS idx_deck_cards_scryfall_id ON deck_cards(scryfall_id);
