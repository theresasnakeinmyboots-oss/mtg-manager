from flask import Blueprint, render_template, request
from app.database import get_db

sets_bp = Blueprint('sets', __name__, url_prefix='/sets')


@sets_bp.route('/', strict_slashes=False)
def index():
    db = get_db()
    set_codes = [s.strip().upper() for s in request.args.getlist('set') if s.strip()]
    collection_id = request.args.get('coll', type=int)

    # All sets from bulk data, ordered by name
    all_sets = [
        (row['set_code'], row['set_name'])
        for row in db.execute(
            'SELECT DISTINCT set_code, set_name FROM scryfall_bulk ORDER BY set_name'
        ).fetchall()
    ]

    # All collections for switcher
    all_collections = db.execute(
        'SELECT * FROM collections WHERE is_staging = 0 ORDER BY name'
    ).fetchall()

    # Build a lookup so we can show set names for selected codes
    set_name_map = {code: name for code, name in all_sets}

    # One group per selected set: {set_code, set_name, cards[]}
    groups = []

    if set_codes:
        owned_where = 'AND c.collection_id = ?' if collection_id else ''

        for code in set_codes:
            params = ([collection_id, code] if collection_id else [code])
            rows = db.execute(f'''
                SELECT
                    b.scryfall_id,
                    b.name,
                    b.collector_number,
                    b.image_uri_normal,
                    b.mana_cost,
                    b.type_line,
                    b.rarity,
                    b.colors,
                    COALESCE(SUM(c.count), 0) as owned_count,
                    MIN(c.id) as collection_row_id
                FROM scryfall_bulk b
                LEFT JOIN cards k ON b.scryfall_id = k.scryfall_id
                LEFT JOIN collection c ON c.card_id = k.id {owned_where}
                WHERE b.set_code = ?
                GROUP BY b.scryfall_id
                ORDER BY CAST(b.collector_number AS INTEGER), b.collector_number
            ''', params).fetchall()

            groups.append({
                'set_code': code,
                'set_name': set_name_map.get(code, code),
                'cards': rows,
            })

    db.close()
    return render_template('sets/index.html',
                           all_sets=all_sets,
                           all_collections=all_collections,
                           groups=groups,
                           set_codes=set_codes,
                           collection_id=collection_id or '')
