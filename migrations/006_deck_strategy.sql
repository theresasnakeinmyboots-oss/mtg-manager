ALTER TABLE decks ADD COLUMN strategy TEXT DEFAULT NULL;

CREATE TABLE IF NOT EXISTS deck_health_mutes (
    deck_id    INTEGER NOT NULL,
    check_name TEXT    NOT NULL,
    PRIMARY KEY (deck_id, check_name)
);

CREATE TABLE IF NOT EXISTS deck_strategy_feedback (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id           INTEGER NOT NULL,
    detected_strategy TEXT    NOT NULL,
    confidence        REAL    NOT NULL DEFAULT 0,
    reasoning         TEXT,
    user_rating       INTEGER DEFAULT NULL,  -- 1 = correct, 0 = wrong
    detected_at       TEXT    NOT NULL
);
