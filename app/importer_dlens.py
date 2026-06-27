import sqlite3
from datetime import datetime
from pathlib import Path

CONDITION_MAP = {
    'Mint': 'M',
    'Near Mint': 'NM',
    'Good (Lightly Played)': 'LP',
    'Lightly Played': 'LP',
    'Moderately Played': 'PL',
    'Played': 'PL',
    'Heavily Played': 'HP',
    'Poor': 'D',
}


def import_dlens(dlens_path: str, datadb_path: str, db, collection_id: int = None) -> tuple[int, int, list]:
    """
    Import a Delver Lens .dlens file into the collection.

    dlens_path:  path to the .dlens SQLite file uploaded by the user
    datadb_path: path to the persistent data.db extracted from the Delver Lens APK
    db:          our app's sqlite3 connection (with row_factory set)
    Returns (inserted, skipped, missing) where missing is a list of
    {'id': ..., 'scryfall_id': ...} dicts for cards not found in scryfall_bulk.
    """
    dlens_path = Path(dlens_path)
    datadb_path = Path(datadb_path)

    if not dlens_path.exists():
        raise FileNotFoundError(f'Delver Lens file not found: {dlens_path}')
    if not datadb_path.exists():
        raise FileNotFoundError(
            'data.db not found. Please upload the Delver Lens APK database via the Admin page first.'
        )

    apk_conn = sqlite3.connect(str(datadb_path))
    apk_conn.row_factory = sqlite3.Row
    apk_c = apk_conn.cursor()

    dlens_conn = sqlite3.connect(str(dlens_path))
    dlens_conn.row_factory = sqlite3.Row
    dlens_c = dlens_conn.cursor()

    # Inspect schema so we can handle whatever column names dlens uses
    dlens_c.execute('PRAGMA table_info(cards)')
    dlens_cols = [r[1] for r in dlens_c.fetchall()]

    apk_c.execute('PRAGMA table_info(cards)')
    apk_cols = [r[1] for r in apk_c.fetchall()]

    dlens_c.execute('SELECT * FROM cards')
    dlens_cards = dlens_c.fetchall()

    inserted = 0
    skipped = 0
    missing = []
    now = datetime.utcnow().isoformat() + 'Z'

    # dlens.cards uses 'card' as the FK into data.db, not '_id'
    card_ref_col = 'card' if 'card' in dlens_cols else dlens_cols[1]
    foil_col = next((c for c in dlens_cols if 'foil' in c.lower()), None)
    qty_col = next((c for c in dlens_cols if c.lower() in ('quantity', 'count', 'qty')), None)
    cond_col = next((c for c in dlens_cols if 'condition' in c.lower()), None)
    lang_col = next((c for c in dlens_cols if 'language' in c.lower() or c.lower() == 'lang'), None)

    apk_id_col = '_id' if '_id' in apk_cols else apk_cols[0]
    apk_scryfall_col = next((c for c in apk_cols if 'scryfall' in c.lower()), None)
    if not apk_scryfall_col:
        raise ValueError(f'Cannot find scryfall_id column in data.db cards table. Columns: {apk_cols}')

    for row in dlens_cards:
        internal_id = row[card_ref_col]
        foil = 'foil' if (foil_col and row[foil_col]) else ''
        quantity = row[qty_col] if qty_col else 1
        condition_raw = row[cond_col] if cond_col else ''
        language = row[lang_col] if lang_col else 'English'

        # Map condition
        condition = CONDITION_MAP.get(condition_raw, 'NM') if condition_raw else 'NM'

        # Resolve scryfall_id via APK database
        apk_c.execute(
            f'SELECT {apk_scryfall_col} FROM cards WHERE {apk_id_col} = ?', (internal_id,)
        )
        apk_row = apk_c.fetchone()
        if not apk_row:
            missing.append({'internal_id': internal_id, 'reason': 'not found in data.db'})
            continue
        scryfall_id = apk_row[apk_scryfall_col]

        # Look up card details in our scryfall_bulk table
        bulk_row = db.execute(
            'SELECT name, set_name, collector_number FROM scryfall_bulk WHERE scryfall_id = ?',
            (scryfall_id,)
        ).fetchone()

        if not bulk_row:
            missing.append({'internal_id': internal_id, 'scryfall_id': scryfall_id, 'reason': 'not in scryfall_bulk'})
            continue

        name = bulk_row['name']
        edition = bulk_row['set_name']
        card_number = bulk_row['collector_number']

        # Find or create the correct cards row for this exact scryfall_id
        cards_row = db.execute(
            'SELECT id FROM cards WHERE scryfall_id = ?', (scryfall_id,)
        ).fetchone()
        if cards_row:
            card_id = cards_row['id']
        else:
            cur = db.execute(
                '''INSERT INTO cards (scryfall_id, name, set_code, set_name, collector_number)
                   VALUES (?, ?, ?, ?, ?)''',
                (scryfall_id, name, '', edition, card_number)
            )
            card_id = cur.lastrowid

        # Dedup check
        existing = db.execute(
            '''SELECT id FROM collection
               WHERE name = ? AND edition = ? AND card_number = ? AND condition = ? AND foil = ?
               AND collection_id IS ?''',
            (name, edition, card_number, condition, foil, collection_id)
        ).fetchone()

        if existing:
            db.execute('UPDATE collection SET count = count + ? WHERE id = ?', (quantity, existing['id']))
            skipped += 1
        else:
            db.execute(
                '''INSERT INTO collection
                   (name, edition, card_number, count, tradelist_count, condition, language,
                    foil, signed, artist_proof, altered_art, misprint, promo, textless,
                    my_price, imported_at, collection_id, card_id, scryfall_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (name, edition, card_number, quantity, 0, condition, language,
                 foil, 0, 0, 0, 0, 0, 0, None, now, collection_id, card_id, scryfall_id)
            )
            inserted += 1

    db.commit()
    apk_conn.close()
    dlens_conn.close()

    return inserted, skipped, missing, {'dlens_cols': dlens_cols, 'apk_cols': apk_cols}
