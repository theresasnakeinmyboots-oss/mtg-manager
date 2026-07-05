-- oracle_id is Scryfall's stable identity for a card *as a game object*,
-- shared by every printing of the same card. It replaces LOWER(name)
-- string-matching as the canonical way to relate deck entries, collection
-- rows, and printings to each other.
ALTER TABLE scryfall_bulk ADD COLUMN oracle_id TEXT;

CREATE INDEX IF NOT EXISTS idx_bulk_oracle_id ON scryfall_bulk(oracle_id);

-- Deck-ownership and reconcile queries filter collection by scryfall_id;
-- until now that was an unindexed full scan per deck card.
CREATE INDEX IF NOT EXISTS idx_collection_scryfall_id ON collection(scryfall_id);
