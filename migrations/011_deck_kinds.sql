-- Deck kinds: a 'list' deck is a theoretical decklist (cards identified by
-- game object / oracle_id — ownership is informational, no exact printing
-- implied). A 'physical' deck is built from specific owned cards, stored
-- separately from the rest of the collection, and backed by allocations.
-- NB: the migration runner splits files on semicolons, so never put one in a comment.
ALTER TABLE decks ADD COLUMN kind TEXT NOT NULL DEFAULT 'list';

-- Which physical collection rows back which deck cards. qty allows one deck
-- card entry (count N) to draw copies from multiple collection rows, and one
-- collection row (count M) to feed multiple decks up to M total.
CREATE TABLE IF NOT EXISTS deck_allocations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_card_id      INTEGER NOT NULL REFERENCES deck_cards(id) ON DELETE CASCADE,
    collection_row_id INTEGER NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
    qty               INTEGER NOT NULL DEFAULT 1,
    UNIQUE(deck_card_id, collection_row_id)
);

CREATE INDEX IF NOT EXISTS idx_alloc_deck_card      ON deck_allocations(deck_card_id);
CREATE INDEX IF NOT EXISTS idx_alloc_collection_row ON deck_allocations(collection_row_id);

-- One-time cleanup: deck deletion never removed deck_cards (sqlite3 runs with
-- foreign_keys OFF, so the ON DELETE CASCADE never fired). Safe to re-run.
DELETE FROM deck_cards WHERE deck_id NOT IN (SELECT id FROM decks);
