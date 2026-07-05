"""Shared helpers for the `cards` table.

The "get or create a cards row from scryfall_bulk" pattern was copy-pasted in
four places (switch_printing, quick_add, deck reconcile, dlens import). The full
20-column INSERT is column-order-sensitive and error-prone, so it lives here once.
"""


def get_or_create_card(db, scryfall_id):
    """Return the `cards.id` for a given scryfall_id, creating the row from
    scryfall_bulk if it doesn't exist yet.

    Returns (card_id, bulk_row). bulk_row is the scryfall_bulk row (or None if
    the printing isn't in bulk data, in which case card_id is also None).
    Does not commit — the caller owns the transaction.
    """
    existing = db.execute(
        'SELECT id FROM cards WHERE scryfall_id = ?', (scryfall_id,)
    ).fetchone()

    bulk = db.execute(
        'SELECT * FROM scryfall_bulk WHERE scryfall_id = ?', (scryfall_id,)
    ).fetchone()

    if existing:
        return existing['id'], bulk

    if not bulk:
        return None, None

    cursor = db.execute('''
        INSERT INTO cards (scryfall_id, name, set_code, set_name, collector_number,
            mana_cost, cmc, colors, color_identity, type_line, oracle_text, flavor_text,
            power, toughness, rarity, legalities, image_uri_normal, image_uri_small,
            artist, enriched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
    ''', (bulk['scryfall_id'], bulk['name'], bulk['set_code'], bulk['set_name'],
          bulk['collector_number'], bulk['mana_cost'], bulk['cmc'], bulk['colors'],
          bulk['color_identity'], bulk['type_line'], bulk['oracle_text'], bulk['flavor_text'],
          bulk['power'], bulk['toughness'], bulk['rarity'], bulk['legalities'],
          bulk['image_uri_normal'], bulk['image_uri_small'], bulk['artist']))
    return cursor.lastrowid, bulk
