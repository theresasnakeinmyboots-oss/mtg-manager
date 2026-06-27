from flask import Blueprint, render_template, request
from app.database import get_db

sets_bp = Blueprint('sets', __name__, url_prefix='/sets')


@sets_bp.route('/', strict_slashes=False)
def index():
    db = get_db()
    set_codes = [s.strip().upper() for s in request.args.getlist('set') if s.strip()]
    collection_id = request.args.get('coll', type=int)

    # Filters
    filter_color  = request.args.get('color', '').strip()
    filter_type   = request.args.get('type', '').strip()
    filter_rarity = request.args.get('rarity', '').strip()
    filter_foil   = request.args.get('foil', '').strip()
    filter_name   = request.args.get('name', '').strip()
    filter_owned  = request.args.get('owned', '').strip()  # 'unowned' | 'owned' | ''

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

    # Sets-overview stats (only needed when no specific sets are selected)
    owned_sets = []
    if not set_codes:
        owned_where = 'AND c.collection_id = ?' if collection_id else ''
        params = [collection_id] if collection_id else []
        owned_sets = db.execute(f'''
            SELECT b.set_code, b.set_name,
                   COUNT(DISTINCT b.scryfall_id) as total_cards,
                   COUNT(DISTINCT CASE WHEN c.id IS NOT NULL THEN b.scryfall_id END) as owned_distinct,
                   COALESCE(SUM(c.count), 0) as owned_total
            FROM scryfall_bulk b
            LEFT JOIN cards k ON b.scryfall_id = k.scryfall_id
            LEFT JOIN collection c ON c.card_id = k.id {owned_where}
            GROUP BY b.set_code
            HAVING owned_distinct > 0
            ORDER BY b.set_name
        ''', params).fetchall()

    # One group per selected set: {set_code, set_name, cards[]}
    groups = []

    if set_codes:
        owned_where = 'AND c.collection_id = ?' if collection_id else ''

        # Build extra WHERE fragments for card-level filters (applied to bulk data)
        card_filters = ''
        card_filter_params = []

        if filter_color == 'multicolor':
            card_filters += ' AND json_array_length(COALESCE(b.colors, "[]")) > 1'
        elif filter_color == 'colorless':
            card_filters += ' AND json_array_length(COALESCE(b.colors, "[]")) = 0 AND b.type_line NOT LIKE "%Land%"'
        elif filter_color:
            card_filters += ' AND COALESCE(b.colors, "[]") LIKE ?'
            card_filter_params.append(f'%"{filter_color}"%')

        if filter_type:
            card_filters += ' AND b.type_line LIKE ?'
            card_filter_params.append(f'%{filter_type}%')

        if filter_rarity:
            card_filters += ' AND b.rarity = ?'
            card_filter_params.append(filter_rarity.upper())

        if filter_name:
            card_filters += ' AND b.name LIKE ?'
            card_filter_params.append(f'%{filter_name}%')

        for code in set_codes:
            base_params = ([collection_id, code] if collection_id else [code])
            params = base_params + card_filter_params
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
                    MIN(c.id) as collection_row_id,
                    c.foil
                FROM scryfall_bulk b
                LEFT JOIN cards k ON b.scryfall_id = k.scryfall_id
                LEFT JOIN collection c ON c.card_id = k.id {owned_where}
                WHERE b.set_code = ?{card_filters}
                GROUP BY b.scryfall_id
                ORDER BY CAST(b.collector_number AS INTEGER), b.collector_number
            ''', params).fetchall()

            # post-query filters (fields not in bulk data)
            if filter_foil:
                rows = [r for r in rows if r['foil'] == 'foil' or r['owned_count'] == 0]
            if filter_owned == 'unowned':
                rows = [r for r in rows if r['owned_count'] == 0]
            elif filter_owned == 'owned':
                rows = [r for r in rows if r['owned_count'] > 0]

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
                           owned_sets=owned_sets,
                           set_codes=set_codes,
                           collection_id=collection_id or '',
                           filter_color=filter_color,
                           filter_type=filter_type,
                           filter_rarity=filter_rarity,
                           filter_foil=filter_foil,
                           filter_name=filter_name,
                           filter_owned=filter_owned)
