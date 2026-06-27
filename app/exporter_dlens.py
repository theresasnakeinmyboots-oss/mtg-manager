import sqlite3
import time
from pathlib import Path

CONDITION_MAP_REVERSE = {
    'NM': 'Near Mint',
    'LP': 'Lightly Played',
    'PL': 'Played',
    'HP': 'Heavily Played',
    'D':  'Poor',
    'M':  'Mint',
}


def export_dlens(db, collection_id: int, datadb_path: str, dest_path: str, list_name: str = 'TheJar Export'):
    """
    Export one collection's cards to a .dlens-shaped SQLite file that Delver Lens
    can import. Mirrors the schema of a real Delver Lens export (lists, cards,
    delverlens, logs, android_metadata tables).

    Cards whose scryfall_id has no match in data.db are skipped and returned in
    `missing` so the caller can surface them — Delver Lens can only reference
    cards present in its own APK database.

    Returns (exported_count, missing) where missing is a list of
    {'name': ..., 'edition': ..., 'reason': ...} dicts.
    """
    datadb_path = Path(datadb_path)
    if not datadb_path.exists():
        raise FileNotFoundError(
            'data.db not found. Please fetch/upload it via the Admin page first.'
        )

    dest_path = Path(dest_path)
    if dest_path.exists():
        dest_path.unlink()

    datadb = sqlite3.connect(str(datadb_path))
    datadb.row_factory = sqlite3.Row

    rows = db.execute('''
        SELECT col.id, col.name, col.edition, col.card_number, col.count,
               col.condition, col.foil, col.language, k.scryfall_id
        FROM collection col
        LEFT JOIN cards k ON col.card_id = k.id
        WHERE col.collection_id = ?
        ORDER BY col.name
    ''', (collection_id,)).fetchall()

    out = sqlite3.connect(str(dest_path))
    cur = out.cursor()

    cur.execute('CREATE TABLE android_metadata (locale TEXT)')
    cur.execute("INSERT INTO android_metadata VALUES ('en_GB')")

    cur.execute('''CREATE TABLE delverlens (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    )''')
    cur.execute("INSERT INTO delverlens VALUES ('timestamp', ?)", (time.strftime('%Y%m%d%H%M%S'),))
    cur.execute("INSERT INTO delverlens VALUES ('version', '6.98')")

    cur.execute('''CREATE TABLE lists (
        _id INTEGER PRIMARY KEY,
        background INTEGER NOT NULL DEFAULT -1,
        category INTEGER NOT NULL DEFAULT 1,
        name TEXT NOT NULL DEFAULT '',
        creation NUMERIC NOT NULL,
        tab INTEGER NOT NULL DEFAULT 0,
        uuid TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT ''
    )''')
    now_ms = int(time.time() * 1000)
    list_id = 1
    cur.execute(
        'INSERT INTO lists (_id, background, category, name, creation, tab, uuid, note) VALUES (?,?,?,?,?,?,?,?)',
        (list_id, -1, 1, list_name, now_ms, 0, '', '')
    )

    cur.execute('''CREATE TABLE cards (
        _id INTEGER PRIMARY KEY,
        card INTEGER NOT NULL,
        foil INTEGER NOT NULL DEFAULT 0,
        price REAL NOT NULL DEFAULT 0,
        quantity INTEGER NOT NULL DEFAULT 1,
        image BLOB,
        creation NUMERIC NOT NULL,
        list INTEGER NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        condition TEXT NOT NULL DEFAULT '',
        language TEXT NOT NULL DEFAULT '',
        publish INTEGER NOT NULL DEFAULT 0,
        tab INTEGER NOT NULL DEFAULT 0,
        downloaded_img INTEGER NOT NULL DEFAULT 0,
        general INTEGER NOT NULL DEFAULT 0,
        img_uuid TEXT NOT NULL DEFAULT '',
        uuid TEXT NOT NULL DEFAULT '',
        price_acquired REAL NOT NULL DEFAULT 0,
        scryfall_id TEXT NOT NULL DEFAULT ''
    )''')

    cur.execute('''CREATE TABLE logs (
        _id INTEGER PRIMARY KEY,
        uuid TEXT NOT NULL,
        category INTEGER NOT NULL,
        creation NUMERIC NOT NULL
    )''')

    exported = 0
    missing = []
    next_id = 1

    for row in rows:
        scryfall_id = row['scryfall_id']
        if not scryfall_id:
            missing.append({'name': row['name'], 'edition': row['edition'], 'reason': 'not enriched'})
            continue

        # Prefer the front face (face=0) when a scryfall_id maps to multiple
        # data.db rows (double-faced cards store front/back as separate rows).
        apk_row = datadb.execute(
            'SELECT _id FROM cards WHERE scryfall_id = ? ORDER BY face LIMIT 1',
            (scryfall_id,)
        ).fetchone()
        if not apk_row:
            missing.append({'name': row['name'], 'edition': row['edition'], 'reason': 'not found in data.db'})
            continue

        is_foil = 1 if row['foil'] == 'foil' else 0
        condition = CONDITION_MAP_REVERSE.get(row['condition'], '') if row['condition'] else ''
        language = row['language'] or 'English'

        cur.execute('''
            INSERT INTO cards
                (_id, card, foil, price, quantity, image, creation, list, note,
                 condition, language, publish, tab, downloaded_img, general,
                 img_uuid, uuid, price_acquired, scryfall_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            next_id, apk_row['_id'], is_foil, 0.0, row['count'], None, now_ms + next_id,
            list_id, '', condition, language, 0, 0, 0, 0, '', '', 0.0, scryfall_id
        ))
        next_id += 1
        exported += 1

    out.commit()
    out.close()
    datadb.close()

    return exported, missing
