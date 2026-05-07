import csv
from datetime import datetime
from pathlib import Path

CONDITION_MAP = {
    'Mint': 'M',
    'Near Mint': 'NM',
    'Good (Lightly Played)': 'LP',
    'Lightly Played': 'LP',
    'Played': 'PL',
    'Heavily Played': 'HP',
    'Poor': 'D',
}

def normalize_condition(condition_str: str) -> str:
    if not condition_str:
        return 'NM'
    return CONDITION_MAP.get(condition_str.strip(), condition_str.strip())

def import_csv(filepath: str, db, collection_id: int = None) -> tuple[int, int]:
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f'{filepath} does not exist')

    inserted = 0
    skipped = 0
    now = datetime.utcnow().isoformat() + 'Z'

    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('Name', '').strip()
            edition = row.get('Edition', '').strip()
            card_number = row.get('Card Number', '').strip()
            condition = normalize_condition(row.get('Condition', ''))
            foil = 'foil' if row.get('Foil', '').lower() in ('foil', 'yes', 'true') else ''
            count = int(row.get('Count', 1)) if row.get('Count', '').strip() else 1
            tradelist_count = int(row.get('Tradelist Count', 0)) if row.get('Tradelist Count', '').strip() else 0

            if not name:
                continue

            cursor = db.execute(
                '''SELECT id, count FROM collection
                   WHERE name = ? AND edition = ? AND card_number = ? AND condition = ? AND foil = ?
                   AND collection_id IS ?''',
                (name, edition, card_number, condition, foil, collection_id)
            )
            existing = cursor.fetchone()

            if existing:
                db.execute(
                    'UPDATE collection SET count = count + ? WHERE id = ?',
                    (count, existing[0])
                )
                skipped += 1
            else:
                db.execute(
                    '''INSERT INTO collection
                       (name, edition, card_number, count, tradelist_count, condition, language,
                        foil, signed, artist_proof, altered_art, misprint, promo, textless,
                        my_price, imported_at, collection_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (name, edition, card_number, count, tradelist_count, condition,
                     row.get('Language', 'English').strip(),
                     foil,
                     1 if row.get('Signed', '').lower() in ('yes', 'true') else 0,
                     1 if row.get('Artist Proof', '').lower() in ('yes', 'true') else 0,
                     1 if row.get('Altered Art', '').lower() in ('yes', 'true') else 0,
                     1 if row.get('Misprint', '').lower() in ('yes', 'true') else 0,
                     1 if row.get('Promo', '').lower() in ('yes', 'true') else 0,
                     1 if row.get('Textless', '').lower() in ('yes', 'true') else 0,
                     float(row.get('My Price', 0)) if row.get('My Price', '').strip() and row.get('My Price', '').strip() != 'No' else None,
                     now, collection_id)
                )
                inserted += 1

    db.commit()
    return inserted, skipped
