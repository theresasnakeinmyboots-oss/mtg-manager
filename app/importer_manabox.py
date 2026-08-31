"""
Import ManaBox CSV exports into a collection, or resolve them into deck entries.

ManaBox rows carry a Scryfall ID directly, so — unlike the plain Deckbox-style
CSV importer — cards are matched against scryfall_bulk by id rather than by
(name, edition) text, same as the Delver Lens importer.

Expected header (ManaBox "Export as CSV" from the collection screen):
  Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,
  Scryfall ID,Purchase price,Misprint,Altered,Condition,Language,
  Purchase price currency,Added
"""

import csv
from datetime import datetime
from pathlib import Path

CONDITION_MAP = {
    'mint': 'M',
    'near_mint': 'NM',
    'excellent': 'LP',
    'good': 'LP',
    'lightly_played': 'LP',
    'good_lightly_played': 'LP',
    'moderately_played': 'PL',
    'played': 'PL',
    'heavily_played': 'HP',
    'poor': 'D',
    'damaged': 'D',
}

LANGUAGE_MAP = {
    'en': 'English', 'es': 'Spanish', 'fr': 'French', 'de': 'German',
    'it': 'Italian', 'pt': 'Portuguese', 'ja': 'Japanese', 'ko': 'Korean',
    'ru': 'Russian', 'zhs': 'Chinese Simplified', 'zht': 'Chinese Traditional',
    'he': 'Hebrew', 'la': 'Latin', 'grc': 'Ancient Greek', 'ar': 'Arabic',
    'sa': 'Sanskrit', 'ph': 'Phyrexian',
}

REQUIRED_HEADERS = {'Scryfall ID', 'Quantity'}


def is_manabox_csv(filepath: str) -> bool:
    """Sniff the header row to tell a ManaBox export apart from other CSV formats."""
    try:
        with open(filepath, newline='', encoding='utf-8-sig') as f:
            header = next(csv.reader(f), [])
    except (OSError, StopIteration):
        return False
    return REQUIRED_HEADERS.issubset(set(header))


def normalize_condition(condition_str: str) -> str:
    if not condition_str:
        return 'NM'
    return CONDITION_MAP.get(condition_str.strip().lower(), 'NM')


def normalize_foil(foil_str: str) -> str:
    return 'foil' if foil_str and 'foil' in foil_str.strip().lower() else ''


def normalize_language(lang_str: str) -> str:
    lang_str = (lang_str or '').strip()
    return LANGUAGE_MAP.get(lang_str.lower(), lang_str or 'English')


def _read_rows(filepath):
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f'{filepath} does not exist')
    with open(filepath, newline='', encoding='utf-8-sig') as f:
        yield from csv.DictReader(f)


def import_manabox(filepath: str, db, collection_id: int = None) -> tuple[int, int, list]:
    """
    Import a ManaBox CSV export into the collection.

    Returns (inserted, skipped, missing) where missing is a list of
    {'name': ..., 'scryfall_id': ...} dicts for rows whose Scryfall ID
    wasn't found in scryfall_bulk.
    """
    inserted = 0
    skipped = 0
    missing = []
    now = datetime.utcnow().isoformat() + 'Z'

    for row in _read_rows(filepath):
        scryfall_id = (row.get('Scryfall ID') or '').strip()
        raw_name = (row.get('Name') or '').strip()
        if not scryfall_id:
            missing.append({'name': raw_name, 'scryfall_id': None, 'reason': 'no Scryfall ID'})
            continue

        bulk_row = db.execute(
            'SELECT name, set_name, collector_number FROM scryfall_bulk WHERE scryfall_id = ?',
            (scryfall_id,)
        ).fetchone()
        if not bulk_row:
            missing.append({'name': raw_name, 'scryfall_id': scryfall_id, 'reason': 'not in scryfall_bulk'})
            continue

        name = bulk_row['name']
        edition = bulk_row['set_name']
        card_number = bulk_row['collector_number']
        condition = normalize_condition(row.get('Condition', ''))
        foil = normalize_foil(row.get('Foil', ''))
        language = normalize_language(row.get('Language', ''))
        quantity = int(row.get('Quantity') or 1)
        misprint = 1 if (row.get('Misprint', '').strip().lower() == 'true') else 0
        altered_art = 1 if (row.get('Altered', '').strip().lower() == 'true') else 0
        price_raw = (row.get('Purchase price') or '').strip()
        my_price = float(price_raw) if price_raw else None

        cards_row = db.execute('SELECT id FROM cards WHERE scryfall_id = ?', (scryfall_id,)).fetchone()
        if cards_row:
            card_id = cards_row['id']
        else:
            cur = db.execute(
                '''INSERT INTO cards (scryfall_id, name, set_code, set_name, collector_number)
                   VALUES (?, ?, ?, ?, ?)''',
                (scryfall_id, name, '', edition, card_number)
            )
            card_id = cur.lastrowid

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
                 foil, 0, 0, altered_art, misprint, 0, 0, my_price, now, collection_id, card_id, scryfall_id)
            )
            inserted += 1

    db.commit()
    return inserted, skipped, missing


def resolve_manabox_for_deck(filepath: str, db) -> tuple[list, list]:
    """Read a ManaBox CSV and resolve each row to a scryfall_id + name + count for deck use."""
    entries = []
    warnings = []

    for row in _read_rows(filepath):
        scryfall_id = (row.get('Scryfall ID') or '').strip()
        raw_name = (row.get('Name') or '').strip()
        quantity = int(row.get('Quantity') or 1)

        if not scryfall_id:
            warnings.append(f'Row for "{raw_name}" has no Scryfall ID')
            continue

        bulk = db.execute(
            'SELECT name FROM scryfall_bulk WHERE scryfall_id = ? LIMIT 1', (scryfall_id,)
        ).fetchone()
        if not bulk:
            warnings.append(f'scryfall_id {scryfall_id} ("{raw_name}") not in bulk data')
            continue

        entries.append({'scryfall_id': scryfall_id, 'name': bulk['name'], 'count': quantity})

    return entries, warnings
