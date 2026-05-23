from flask import Blueprint, render_template, request, jsonify, Response, send_file, redirect, url_for
from app.database import get_db
from app.scryfall import ScryfallClient
from app.importer import import_csv
from werkzeug.utils import secure_filename
from pathlib import Path
import json
import config
import csv

collection_bp = Blueprint('collection', __name__, url_prefix='/')

# Colour sort: use color_identity as fallback for sagas/MDFCs where colors=[]
# Order: multicolor, W, U, B, R, G, artifacts, lands, colorless other
_EFF_COLORS = "CASE WHEN json_array_length(COALESCE(k.colors,'[]')) > 0 THEN k.colors ELSE COALESCE(k.color_identity,'[]') END"
_COLOR_SORT = f"""
    CASE
        WHEN json_array_length({_EFF_COLORS}) > 1 THEN 1
        WHEN {_EFF_COLORS} = '["W"]' THEN 2
        WHEN {_EFF_COLORS} = '["U"]' THEN 3
        WHEN {_EFF_COLORS} = '["B"]' THEN 4
        WHEN {_EFF_COLORS} = '["R"]' THEN 5
        WHEN {_EFF_COLORS} = '["G"]' THEN 6
        WHEN k.type_line LIKE '%Artifact%' THEN 7
        WHEN k.type_line LIKE '%Land%' THEN 8
        ELSE 9
    END
""".strip()

SORT_COLS = {
    'name':   'c.name',
    'set':    'c.edition, c.name',
    'count':  'total_count DESC, c.name',
    'color':  f'{_COLOR_SORT}, c.name',
    'type':   'k.type_line, c.name',
    'cost':   'COALESCE(k.cmc, 0), c.name',
    'rarity': 'k.rarity, c.name',
}

import re as _re

_COLOR_MAP = {
    'white': 'W', 'blue': 'U', 'black': 'B', 'red': 'R', 'green': 'G',
    'w': 'W', 'u': 'U', 'b': 'B', 'r': 'R', 'g': 'G',
}
_CMC_OPS = {'>=': '>=', '<=': '<=', '>': '>', '<': '<', '=': '=', ':': '='}

def _term_to_sql(k, v):
    """Convert a single key:value token to (sql_fragment, [params]).
    Returns (None, []) if unrecognised."""
    if k in ('name', 'n'):
        return 'c.name LIKE ?', [f'%{v}%']
    if k in ('type', 't'):
        return 'k.type_line LIKE ?', [f'%{v}%']
    if k in ('oracle', 'o', 'text', 'rules'):
        return 'k.oracle_text LIKE ?', [f'%{v}%']
    if k in ('color', 'colour', 'c'):
        vl = v.lower()
        if vl in ('multicolor', 'multicolour', 'm'):
            return 'json_array_length(COALESCE(k.colors, "[]")) > 1', []
        if vl in ('colorless', 'colourless'):
            return 'json_array_length(COALESCE(k.colors, "[]")) = 0 AND k.type_line NOT LIKE "%Land%"', []
        code = _COLOR_MAP.get(vl, v.upper())
        return 'COALESCE(k.colors, "[]") LIKE ?', [f'%"{code}"%']
    if k in ('rarity', 'r'):
        rarity_map = {'c': 'common', 'u': 'uncommon', 'r': 'rare', 'm': 'mythic'}
        return 'k.rarity = ?', [rarity_map.get(v.lower(), v.lower())]
    if k in ('set', 's', 'edition', 'e'):
        return 'UPPER(c.edition) = ?', [v.upper()]
    if k in ('cmc', 'mv', 'manavalue'):
        m = _re.match(r'([><=]{1,2})?(\d+(?:\.\d+)?)', v)
        if m:
            op = _CMC_OPS.get(m.group(1) or '=', '=')
            return f'COALESCE(k.cmc, 0) {op} ?', [float(m.group(2))]
    if k in ('power', 'pow'):
        m = _re.match(r'([><=]{1,2})?(\d+)', v)
        if m:
            op = _CMC_OPS.get(m.group(1) or '=', '=')
            return f'CAST(k.power AS INTEGER) {op} ?', [int(m.group(2))]
    if k in ('toughness', 'tou'):
        m = _re.match(r'([><=]{1,2})?(\d+)', v)
        if m:
            op = _CMC_OPS.get(m.group(1) or '=', '=')
            return f'CAST(k.toughness AS INTEGER) {op} ?', [int(m.group(2))]
    return None, []


def parse_advanced_query(q, where, params):
    """Parse Scryfall-style query string with AND/OR support.

    Terms separated by 'or' (case-insensitive) are grouped into OR blocks;
    everything else is implicitly AND.  Example:
        type:ninja o:ninjato or o:sneak
    becomes:
        AND type_line LIKE '%ninja%' AND (oracle_text LIKE '%ninjato%' OR oracle_text LIKE '%sneak%')
    """
    # Tokenise: key:value, key:"quoted value", bare word
    raw_tokens = _re.findall(r'(\w+):(\"[^\"]*\"|[^\s]+)|(\S+)', q)

    # Build list of ('term', k, v) or ('or',) or ('bare', word)
    parsed = []
    for key_part, val_part, bare in raw_tokens:
        if key_part:
            parsed.append(('term', key_part.lower(), val_part.strip('"')))
        elif bare.lower() == 'or':
            parsed.append(('or',))
        else:
            parsed.append(('bare', bare))

    # Group into AND-level chunks; consecutive items separated by 'or' form one OR group
    # e.g. [term, or, term, or, term, AND_implicit, term] → [[t,t,t], [t]]
    and_groups = []
    current_or = []
    for token in parsed:
        if token[0] == 'or':
            # continue accumulating into current OR group
            pass
        else:
            # Check if previous token was 'or' — if not, start a new AND group
            idx = parsed.index(token)  # safe because we process linearly below
            current_or.append(token)
            and_groups.append(current_or)
            current_or = []

    # Re-implement with a proper linear pass
    and_groups = []
    or_buf = []
    prev_was_or = False
    for token in parsed:
        if token[0] == 'or':
            prev_was_or = True
            continue
        if prev_was_or and or_buf:
            or_buf.append(token)
        else:
            if or_buf:
                and_groups.append(or_buf)
            or_buf = [token]
        prev_was_or = False
    if or_buf:
        and_groups.append(or_buf)

    # Convert each AND group to SQL
    for group in and_groups:
        or_frags = []
        or_params = []
        for token in group:
            if token[0] == 'term':
                sql, p = _term_to_sql(token[1], token[2])
                if sql:
                    or_frags.append(sql)
                    or_params.extend(p)
            elif token[0] == 'bare':
                or_frags.append('c.name LIKE ?')
                or_params.append(f'%{token[1]}%')

        if not or_frags:
            continue
        if len(or_frags) == 1:
            where.append(or_frags[0])
            params.extend(or_params)
        else:
            where.append('(' + ' OR '.join(or_frags) + ')')
            params.extend(or_params)


def build_order_parts(sorts_dirs):
    seen = set()
    parts = []
    for s, d in sorts_dirs:
        if not s or s not in SORT_COLS:
            continue
        col = SORT_COLS[s]
        if d == 'desc':
            first, *rest = col.split(', ')
            first = first.replace(' DESC', '').replace(' ASC', '') + ' DESC'
            col = ', '.join([first] + rest)
        if col not in seen:
            seen.add(col)
            parts.append(col)
    return parts

@collection_bp.route('/test-keyrune')
def test_keyrune():
    return send_file(Path(__file__).parent.parent.parent / 'test_keyrune.html')

@collection_bp.route('/')
def index():
    return redirect('/collection')

@collection_bp.route('/collection')
def browse():
    db = get_db()
    per_page = config.CARDS_PER_PAGE
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * per_page

    # Collection switcher
    collection_id = request.args.get('coll', type=int)
    all_collections = db.execute(
        'SELECT * FROM collections WHERE is_staging = 0 ORDER BY name'
    ).fetchall()
    active_collection = None
    if collection_id:
        active_collection = db.execute(
            'SELECT * FROM collections WHERE id = ?', (collection_id,)
        ).fetchone()

    where_clause = 'WHERE 1=1'
    params = []

    if collection_id:
        where_clause += ' AND c.collection_id = ?'
        params.append(collection_id)

    adv = request.args.get('adv', '').strip() == '1'
    search_q = request.args.get('q', '').strip()
    search_type = request.args.get('search_type', '').strip()
    search_rules = request.args.get('search_rules', '').strip()

    adv_clauses = []
    adv_params = []
    if adv and search_q:
        parse_advanced_query(search_q, adv_clauses, adv_params)
    elif search_q:
        where_clause += ' AND c.name LIKE ?'
        params.append(f'%{search_q}%')

    if not adv:
        if search_type:
            where_clause += ' AND k.type_line LIKE ?'
            params.append(f'%{search_type}%')
        if search_rules:
            where_clause += ' AND k.oracle_text LIKE ?'
            params.append(f'%{search_rules}%')

    if adv_clauses:
        where_clause += ' AND ' + ' AND '.join(adv_clauses)
        params.extend(adv_params)

    color = request.args.get('color', '').strip()
    if color == 'multicolor':
        where_clause += ' AND json_array_length(COALESCE(k.colors, "[]")) > 1'
    elif color == 'colorless':
        where_clause += ' AND json_array_length(COALESCE(k.colors, "[]")) = 0 AND (k.type_line NOT LIKE "%Land%" OR k.type_line IS NULL)'
    elif color:
        where_clause += ' AND COALESCE(k.colors, "[]") LIKE ?'
        params.append(f'%"{color}"%')

    card_type = request.args.get('type', '').strip()
    if card_type:
        where_clause += ' AND k.type_line LIKE ?'
        params.append(f'%{card_type}%')

    filter_sets = [s.strip().upper() for s in request.args.getlist('set') if s.strip()]
    if filter_sets:
        placeholders = ','.join('?' * len(filter_sets))
        where_clause += f' AND UPPER(c.edition) IN ({placeholders})'
        params.extend(filter_sets)

    condition = request.args.get('condition', '').strip()
    if condition:
        where_clause += ' AND c.condition = ?'
        params.append(condition)

    foil = request.args.get('foil', '').strip()
    if foil:
        where_clause += ' AND c.foil = ?'
        params.append('foil')

    sort1 = request.args.get('sort1', 'name').strip()
    sort2 = request.args.get('sort2', '').strip()
    sort3 = request.args.get('sort3', '').strip()
    dir1 = 'desc' if request.args.get('dir1', '').strip() == 'desc' else 'asc'
    dir2 = 'desc' if request.args.get('dir2', '').strip() == 'desc' else 'asc'
    dir3 = 'desc' if request.args.get('dir3', '').strip() == 'desc' else 'asc'
    # keep legacy ?sort= param working
    if 'sort' in request.args and 'sort1' not in request.args:
        sort1 = request.args.get('sort', 'name').strip()

    parts = build_order_parts([(sort1, dir1), (sort2, dir2), (sort3, dir3)])
    order_clause = 'ORDER BY ' + ', '.join(parts) if parts else 'ORDER BY c.name'

    query = f'''
        SELECT MIN(c.id) as id, c.name, c.edition, c.condition, SUM(c.count) as count, c.foil, c.card_number,
               COALESCE(k.type_line, '') as type_line,
               COALESCE(k.colors, '[]') as colors,
               COALESCE(k.image_uri_normal, '') as image_uri,
               COALESCE(k.mana_cost, '') as mana_cost,
               COALESCE(k.set_code, '') as set_code,
               COALESCE(k.rarity, '') as rarity,
               COALESCE(k.cmc, 0) as cmc,
               SUM(c.count) as total_count
        FROM collection c
        LEFT JOIN cards k ON c.card_id = k.id
        {where_clause}
        GROUP BY c.name, c.card_id, c.condition, c.foil
        {order_clause}
        LIMIT ? OFFSET ?
    '''

    cursor = db.execute(query, params + [per_page, offset])
    cards = [dict(row) for row in cursor.fetchall()]

    count_query = f'''SELECT COUNT(*) FROM (
                        SELECT 1 FROM collection c
                        LEFT JOIN cards k ON c.card_id = k.id
                        {where_clause}
                        GROUP BY c.name, c.card_id, c.condition, c.foil
                     )'''
    cursor = db.execute(count_query, params)
    total = cursor.fetchone()[0]

    all_sets = [
        (row['set_code'], row['set_name'])
        for row in db.execute(
            'SELECT DISTINCT set_code, set_name FROM scryfall_bulk ORDER BY set_name'
        ).fetchall()
    ]

    db.close()

    return render_template('collection/index.html',
                         cards=cards,
                         page=page,
                         per_page=per_page,
                         total=total,
                         sort1=sort1, dir1=dir1,
                         sort2=sort2, dir2=dir2,
                         sort3=sort3, dir3=dir3,
                         search_q=search_q,
                         search_type=search_type,
                         search_rules=search_rules,
                         adv=adv,
                         filter_color=color,
                         filter_type=card_type,
                         filter_sets=filter_sets,
                         filter_condition=condition,
                         filter_foil=foil,
                         all_collections=all_collections,
                         all_sets=all_sets,
                         active_collection=active_collection,
                         collection_id=collection_id or '')

@collection_bp.route('/collection/cards.json')
def cards_json():
    db = get_db()
    per_page = config.CARDS_PER_PAGE
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * per_page

    collection_id = request.args.get('coll', type=int)
    where_clause = 'WHERE 1=1'
    params = []
    if collection_id:
        where_clause += ' AND c.collection_id = ?'
        params.append(collection_id)
    adv = request.args.get('adv', '').strip() == '1'
    search_q = request.args.get('q', '').strip()
    search_type = request.args.get('search_type', '').strip()
    search_rules = request.args.get('search_rules', '').strip()
    adv_clauses = []; adv_params = []
    if adv and search_q:
        parse_advanced_query(search_q, adv_clauses, adv_params)
    elif search_q:
        where_clause += ' AND c.name LIKE ?'
        params.append(f'%{search_q}%')
    if not adv:
        if search_type:
            where_clause += ' AND k.type_line LIKE ?'
            params.append(f'%{search_type}%')
        if search_rules:
            where_clause += ' AND k.oracle_text LIKE ?'
            params.append(f'%{search_rules}%')
    if adv_clauses:
        where_clause += ' AND ' + ' AND '.join(adv_clauses)
        params.extend(adv_params)
    color = request.args.get('color', '').strip()
    if color == 'multicolor':
        where_clause += ' AND json_array_length(COALESCE(k.colors, "[]")) > 1'
    elif color == 'colorless':
        where_clause += ' AND json_array_length(COALESCE(k.colors, "[]")) = 0 AND (k.type_line NOT LIKE "%Land%" OR k.type_line IS NULL)'
    elif color:
        where_clause += ' AND COALESCE(k.colors, "[]") LIKE ?'
        params.append(f'%"{color}"%')
    card_type = request.args.get('type', '').strip()
    if card_type:
        where_clause += ' AND k.type_line LIKE ?'
        params.append(f'%{card_type}%')
    filter_sets = [s.strip().upper() for s in request.args.getlist('set') if s.strip()]
    if filter_sets:
        placeholders = ','.join('?' * len(filter_sets))
        where_clause += f' AND UPPER(c.edition) IN ({placeholders})'
        params.extend(filter_sets)
    condition = request.args.get('condition', '').strip()
    if condition:
        where_clause += ' AND c.condition = ?'
        params.append(condition)
    foil = request.args.get('foil', '').strip()
    if foil:
        where_clause += ' AND c.foil = ?'
        params.append('foil')

    sort1 = request.args.get('sort1', 'name').strip()
    sort2 = request.args.get('sort2', '').strip()
    sort3 = request.args.get('sort3', '').strip()
    dir1 = 'desc' if request.args.get('dir1', '').strip() == 'desc' else 'asc'
    dir2 = 'desc' if request.args.get('dir2', '').strip() == 'desc' else 'asc'
    dir3 = 'desc' if request.args.get('dir3', '').strip() == 'desc' else 'asc'
    parts = build_order_parts([(sort1, dir1), (sort2, dir2), (sort3, dir3)])
    order_clause = 'ORDER BY ' + ', '.join(parts) if parts else 'ORDER BY c.name'

    cursor = db.execute(f'''
        SELECT MIN(c.id) as id, c.name, c.edition, c.condition, SUM(c.count) as count, c.foil,
               COALESCE(k.image_uri_normal, '') as image_uri,
               SUM(c.count) as total_count
        FROM collection c
        LEFT JOIN cards k ON c.card_id = k.id
        {where_clause}
        GROUP BY c.name, c.card_id, c.condition, c.foil
        {order_clause}
        LIMIT ? OFFSET ?
    ''', params + [per_page, offset])
    cards = [dict(row) for row in cursor.fetchall()]
    db.close()

    return jsonify({'cards': cards, 'page': page, 'has_more': len(cards) == per_page})


@collection_bp.route('/collection/<int:card_id>')
def card_detail(card_id):
    db = get_db()
    # Look up the specific collection row, then group by name+card_id+condition+foil
    # to sum counts across duplicate rows for the same printing
    coll_row = db.execute('SELECT name, card_id, condition, foil FROM collection WHERE id = ?', (card_id,)).fetchone()
    if not coll_row:
        db.close()
        return jsonify({'error': 'Card not found'}), 404

    cursor = db.execute('''
        SELECT MIN(c.id) as id, c.name, c.edition, c.condition, SUM(c.count) as count,
               c.foil, MIN(c.card_number) as card_number, c.language, SUM(c.tradelist_count) as tradelist_count,
               MAX(c.signed) as signed, MAX(c.artist_proof) as artist_proof,
               MAX(c.altered_art) as altered_art, MAX(c.misprint) as misprint,
               MAX(c.promo) as promo, MAX(c.textless) as textless, MAX(c.my_price) as my_price,
               COALESCE(k.oracle_text, '') as oracle_text,
               COALESCE(k.flavor_text, '') as flavor_text,
               COALESCE(k.artist, '') as artist,
               COALESCE(k.rarity, '') as rarity,
               COALESCE(k.power, '') as power,
               COALESCE(k.toughness, '') as toughness,
               COALESCE(k.image_uri_normal, '') as image_uri,
               COALESCE(k.type_line, '') as type_line,
               COALESCE(k.mana_cost, '') as mana_cost,
               COALESCE(k.scryfall_id, '') as scryfall_id,
               COALESCE(k.set_code, '') as set_code,
               COALESCE(k.set_name, '') as set_name,
               COALESCE(k.legalities, '{}') as legalities,
               COALESCE(k.cmc, 0) as cmc,
               k.price_usd, k.price_usd_foil, k.price_eur, k.price_eur_foil,
               k.prices_updated_at
        FROM collection c
        LEFT JOIN cards k ON c.card_id = k.id
        WHERE c.name = ? AND c.card_id IS ? AND c.condition = ? AND c.foil = ?
        GROUP BY c.name, c.card_id, c.condition, c.foil
    ''', (coll_row['name'], coll_row['card_id'], coll_row['condition'], coll_row['foil']))
    row = cursor.fetchone()

    if not row:
        db.close()
        return jsonify({'error': 'Card not found'}), 404

    card = dict(row)
    card['collection_row_id'] = card_id

    # Parse legalities JSON into sorted list of legal formats
    try:
        legalities_raw = json.loads(card.get('legalities', '{}') or '{}')
        card['legal_formats'] = sorted(
            fmt.capitalize() for fmt, status in legalities_raw.items() if status == 'legal'
        )
    except Exception:
        card['legal_formats'] = []

    # Fetch all alternative printings from bulk data
    alternatives = []
    if card['name']:
        cursor = db.execute('''
            SELECT scryfall_id, set_code, set_name, collector_number, image_uri_normal, rarity
            FROM scryfall_bulk
            WHERE name = ?
            ORDER BY set_name, CAST(collector_number AS INTEGER), collector_number
        ''', (card['name'],))
        alternatives = [dict(r) for r in cursor.fetchall()]

    db.close()
    return render_template('collection/card_detail.html', card=card, alternatives=alternatives)

@collection_bp.route('/collection/<int:row_id>/switch-printing', methods=['POST'])
def switch_printing(row_id):
    data = request.get_json()
    scryfall_id = data.get('scryfall_id', '').strip()
    if not scryfall_id:
        return jsonify({'error': 'scryfall_id required'}), 400

    db = get_db()

    # Get the collection row
    coll_row = db.execute('SELECT * FROM collection WHERE id = ?', (row_id,)).fetchone()
    if not coll_row:
        db.close()
        return jsonify({'error': 'Row not found'}), 404

    # Get or create the cards row for the target printing
    target_card = db.execute('SELECT id FROM cards WHERE scryfall_id = ?', (scryfall_id,)).fetchone()
    if target_card:
        target_card_id = target_card['id']
    else:
        # Copy from scryfall_bulk into cards
        bulk = db.execute('SELECT * FROM scryfall_bulk WHERE scryfall_id = ?', (scryfall_id,)).fetchone()
        if not bulk:
            db.close()
            return jsonify({'error': 'Printing not found in bulk data'}), 404
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
        target_card_id = cursor.lastrowid

    # Get the new printing's set name to update edition
    bulk_edition = db.execute(
        'SELECT set_name, set_code, collector_number FROM scryfall_bulk WHERE scryfall_id = ?', (scryfall_id,)
    ).fetchone()
    new_edition = bulk_edition['set_name'] if bulk_edition else coll_row['edition']
    new_card_number = bulk_edition['collector_number'] if bulk_edition else coll_row['card_number']

    # Each import row already represents individual copies (count is usually 1 per row).
    # If this row has count > 1, split one copy out first.
    if coll_row['count'] > 1:
        db.execute('UPDATE collection SET count = count - 1 WHERE id = ?', (row_id,))
        cursor = db.execute('''
            INSERT INTO collection (card_id, scryfall_id, name, edition, card_number, count,
                tradelist_count, condition, language, foil, signed, artist_proof, altered_art,
                misprint, promo, textless, my_price, collection_id, imported_at)
            VALUES (?,?,?,?,?,1,0,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        ''', (target_card_id, scryfall_id, coll_row['name'], new_edition,
              new_card_number, coll_row['condition'],
              coll_row['language'], coll_row['foil'], coll_row['signed'],
              coll_row['artist_proof'], coll_row['altered_art'], coll_row['misprint'],
              coll_row['promo'], coll_row['textless'], coll_row['my_price'],
              coll_row['collection_id']))
        new_row_id = cursor.lastrowid
    else:
        db.execute(
            'UPDATE collection SET card_id = ?, scryfall_id = ?, edition = ?, card_number = ? WHERE id = ?',
            (target_card_id, scryfall_id, new_edition, new_card_number, row_id)
        )
        new_row_id = row_id

    db.commit()
    db.close()
    return jsonify({'success': True, 'new_row_id': new_row_id})


@collection_bp.route('/collection/quick-add', methods=['POST'])
def quick_add():
    """Add one copy of a card (by scryfall_id) to a collection, creating the cards row if needed."""
    data        = request.get_json(force=True)
    scryfall_id = data.get('scryfall_id', '').strip()
    collection_id = data.get('collection_id') or None

    if not scryfall_id:
        return jsonify(error='scryfall_id required'), 400

    db = get_db()

    bulk = db.execute('SELECT * FROM scryfall_bulk WHERE scryfall_id = ? LIMIT 1', [scryfall_id]).fetchone()
    if not bulk:
        db.close()
        return jsonify(error='Card not found in bulk data'), 404

    # Validate collection exists if one was specified
    if collection_id:
        coll = db.execute('SELECT id FROM collections WHERE id = ?', [collection_id]).fetchone()
        if not coll:
            db.close()
            return jsonify(error='Collection not found'), 404

    # Get or create the cards row
    card_row = db.execute('SELECT id FROM cards WHERE scryfall_id = ?', [scryfall_id]).fetchone()
    if card_row:
        card_id = card_row['id']
    else:
        cur = db.execute('''
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
        card_id = cur.lastrowid

    # Increment existing row or insert a new one
    existing = db.execute(
        '''SELECT id FROM collection
           WHERE card_id = ? AND foil = '' AND condition = 'NM'
           AND collection_id IS ?''',
        [card_id, collection_id]
    ).fetchone()

    if existing:
        db.execute('UPDATE collection SET count = count + 1 WHERE id = ?', [existing['id']])
    else:
        from datetime import datetime, timezone
        db.execute('''
            INSERT INTO collection
                (card_id, scryfall_id, name, edition, card_number, count, tradelist_count,
                 condition, language, foil, signed, artist_proof, altered_art, misprint,
                 promo, textless, my_price, collection_id, imported_at)
            VALUES (?,?,?,?,?,1,0,'NM','English','',0,0,0,0,0,0,NULL,?,?)
        ''', (card_id, scryfall_id, bulk['name'], bulk['set_name'], bulk['collector_number'],
              collection_id, datetime.now(timezone.utc).isoformat()))

    db.commit()
    db.close()
    return jsonify(ok=True, name=bulk['name'])


@collection_bp.route('/collection/api/collections')
def api_collections():
    """Return all non-staging collections for the context menu."""
    db = get_db()
    rows = db.execute('SELECT id, name FROM collections WHERE is_staging = 0 ORDER BY name').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@collection_bp.route('/enrich-card/<int:card_id>', methods=['POST'])
def enrich_card(card_id):
    db = get_db()
    cursor = db.execute('''
        SELECT c.name, c.edition, COALESCE(k.set_code, c.edition) as set_code
        FROM collection c
        LEFT JOIN cards k ON c.card_id = k.id
        WHERE c.id = ?
    ''', (card_id,))
    row = cursor.fetchone()

    if not row:
        db.close()
        return jsonify({'error': 'Card not found'}), 404

    name, edition, set_code = row['name'], row['edition'], row['set_code']

    # Clear cached Scryfall responses for this card so we fetch fresh data
    db.execute('DELETE FROM scryfall_cache WHERE cache_key LIKE ?', (f'%:{name.lower()}%',))
    db.commit()

    client = ScryfallClient(db)

    try:
        success, reason = client.enrich_card(card_id, name, edition, set_code, max_retries=3, force_update=True)
        db.close()

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': reason or 'Could not find card on Scryfall'}), 400
    except Exception as e:
        db.close()
        return jsonify({'error': str(e)}), 400

@collection_bp.route('/import', methods=['GET', 'POST'])
def import_cards():
    if request.method == 'GET':
        return render_template('collection/import.html')

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not file.filename.endswith('.csv'):
        return jsonify({'error': 'Only CSV files allowed'}), 400

    filename = secure_filename(file.filename)
    filepath = config.DATA_DIR / filename
    file.save(str(filepath))

    def generate():
        import json as _json
        db = get_db()
        try:
            yield 'data: ' + _json.dumps({'status': 'importing', 'progress': 1, 'message': 'Starting import...'}) + '\n\n'

            inserted, skipped = import_csv(str(filepath), db)
            yield 'data: ' + _json.dumps({'status': 'enriching', 'progress': 40, 'message': 'Starting enrichment...'}) + '\n\n'

            client = ScryfallClient(db)
            from app.scryfall import EDITION_TO_SET_CODE
            cursor = db.execute('SELECT id, name, edition FROM collection WHERE card_id IS NULL ORDER BY id')
            unenriched = cursor.fetchall()
            total_unenriched = len(unenriched)

            enriched = 0
            for i, row in enumerate(unenriched):
                set_code = EDITION_TO_SET_CODE.get(row['edition'], row['edition'])
                ok, _ = client.enrich_card(row['id'], row['name'], row['edition'], set_code)
                if ok:
                    enriched += 1
                progress = 40 + int((i + 1) / max(total_unenriched, 1) * 59)
                yield 'data: ' + _json.dumps({
                    'status': 'enriching', 'progress': progress,
                    'message': f'Enriching card {i+1}/{total_unenriched}...'
                }) + '\n\n'

            db.close()
            yield 'data: ' + _json.dumps({
                'status': 'complete', 'progress': 100, 'message': 'Complete!',
                'inserted': inserted, 'skipped': skipped, 'enriched': enriched
            }) + '\n\n'
        except Exception as e:
            db.close()
            yield 'data: ' + _json.dumps({'status': 'error', 'message': str(e)}) + '\n\n'

    return Response(generate(), mimetype='text/event-stream')

