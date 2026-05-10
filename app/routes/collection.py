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

@collection_bp.route('/test-keyrune')
def test_keyrune():
    return send_file(Path(__file__).parent.parent.parent / 'test_keyrune.html')

@collection_bp.route('/')
def index():
    return redirect(url_for('collection.browse'))

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

    search_q = request.args.get('q', '').strip()
    if search_q:
        where_clause += ' AND c.name LIKE ?'
        params.append(f'%{search_q}%')

    color = request.args.get('color', '').strip()
    if color:
        where_clause += ' AND COALESCE(k.colors, "[]") LIKE ?'
        params.append(f'%"{color}"%')

    card_type = request.args.get('type', '').strip()
    if card_type:
        where_clause += ' AND k.type_line LIKE ?'
        params.append(f'%{card_type}%')

    set_code = request.args.get('set', '').strip()
    if set_code:
        where_clause += ' AND c.edition LIKE ?'
        params.append(f'%{set_code}%')

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
    # keep legacy ?sort= param working
    if 'sort' in request.args and 'sort1' not in request.args:
        sort1 = request.args.get('sort', 'name').strip()

    parts = [SORT_COLS.get(s) for s in (sort1, sort2, sort3) if s and s in SORT_COLS]
    # deduplicate while preserving order
    seen = set()
    deduped = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    order_clause = 'ORDER BY ' + ', '.join(deduped) if deduped else 'ORDER BY c.name'

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

    db.close()

    return render_template('collection/index.html',
                         cards=cards,
                         page=page,
                         per_page=per_page,
                         total=total,
                         sort1=sort1,
                         sort2=sort2,
                         sort3=sort3,
                         search_q=search_q,
                         filter_color=color,
                         filter_type=card_type,
                         filter_set=set_code,
                         filter_condition=condition,
                         filter_foil=foil,
                         all_collections=all_collections,
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
    search_q = request.args.get('q', '').strip()
    if search_q:
        where_clause += ' AND c.name LIKE ?'
        params.append(f'%{search_q}%')
    color = request.args.get('color', '').strip()
    if color:
        where_clause += ' AND COALESCE(k.colors, "[]") LIKE ?'
        params.append(f'%"{color}"%')
    card_type = request.args.get('type', '').strip()
    if card_type:
        where_clause += ' AND k.type_line LIKE ?'
        params.append(f'%{card_type}%')
    set_code = request.args.get('set', '').strip()
    if set_code:
        where_clause += ' AND c.edition LIKE ?'
        params.append(f'%{set_code}%')
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
    parts = [SORT_COLS.get(s) for s in (sort1, sort2, sort3) if s and s in SORT_COLS]
    seen = set(); deduped = []
    for p in parts:
        if p not in seen:
            seen.add(p); deduped.append(p)
    order_clause = 'ORDER BY ' + ', '.join(deduped) if deduped else 'ORDER BY c.name'

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
            ORDER BY set_code, CAST(collector_number AS INTEGER), collector_number
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

