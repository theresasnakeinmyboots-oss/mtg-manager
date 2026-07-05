"""Shared helpers for the `cards` table.

The "get or create a cards row from scryfall_bulk" pattern was copy-pasted in
four places (switch_printing, quick_add, deck reconcile, dlens import). The full
20-column INSERT is column-order-sensitive and error-prone, so it lives here once.
"""


def resolve_oracle_id(db, scryfall_id):
    """Map a printing to its oracle_id (the card's identity as a game object)."""
    if not scryfall_id:
        return None
    row = db.execute(
        'SELECT oracle_id FROM scryfall_bulk WHERE scryfall_id = ?', (scryfall_id,)
    ).fetchone()
    return row['oracle_id'] if row else None


def decks_containing(db, scryfall_id, name=None):
    """Decks that contain this card as a game object — any printing counts.
    Falls back to name matching only when the printing has no oracle_id
    (unenriched rows, pack ephemera)."""
    oracle_id = resolve_oracle_id(db, scryfall_id)
    if oracle_id:
        rows = db.execute('''
            SELECT d.id, d.name, d.format, d.kind, dc.board, dc.count
            FROM deck_cards dc
            JOIN decks d ON d.id = dc.deck_id
            JOIN scryfall_bulk b ON dc.scryfall_id = b.scryfall_id
            WHERE b.oracle_id = ?
            ORDER BY d.name
        ''', (oracle_id,)).fetchall()
    elif name:
        rows = db.execute('''
            SELECT d.id, d.name, d.format, d.kind, dc.board, dc.count
            FROM deck_cards dc
            JOIN decks d ON d.id = dc.deck_id
            WHERE LOWER(dc.name) = LOWER(?)
            ORDER BY d.name
        ''', (name,)).fetchall()
    else:
        rows = []
    return [dict(r) for r in rows]


def owned_total_for(db, scryfall_id, name=None):
    """Total owned copies of this card as a game object, across all printings."""
    oracle_id = resolve_oracle_id(db, scryfall_id)
    if oracle_id:
        return db.execute('''
            SELECT COALESCE(SUM(c.count), 0)
            FROM collection c
            JOIN scryfall_bulk b ON c.scryfall_id = b.scryfall_id
            WHERE b.oracle_id = ?
        ''', (oracle_id,)).fetchone()[0]
    if name:
        return db.execute('''
            SELECT COALESCE(SUM(c.count), 0)
            FROM collection c
            JOIN cards k ON c.card_id = k.id
            WHERE LOWER(k.name) = LOWER(?)
        ''', (name,)).fetchone()[0]
    return 0


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
