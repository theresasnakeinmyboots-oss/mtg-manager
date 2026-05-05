from flask import Blueprint, render_template, request, jsonify, Response, send_file
from app.database import get_db
from app.scryfall import ScryfallClient
from app.importer import import_csv
from werkzeug.utils import secure_filename
from pathlib import Path
import json
import config
import csv

collection_bp = Blueprint('collection', __name__, url_prefix='/')

@collection_bp.route('/test-keyrune')
def test_keyrune():
    return send_file(Path(__file__).parent.parent.parent / 'test_keyrune.html')

@collection_bp.route('/')
def index():
    return render_template('collection/index.html')

@collection_bp.route('/collection')
def browse():
    db = get_db()
    per_page = config.CARDS_PER_PAGE
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * per_page

    where_clause = 'WHERE 1=1'
    params = []

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

    sort_by = request.args.get('sort', 'name').strip()
    if sort_by == 'set':
        order_clause = 'ORDER BY c.edition, c.name'
    elif sort_by == 'count':
        order_clause = 'ORDER BY total_count DESC, c.name'
    else:
        order_clause = 'ORDER BY c.name'

    query = f'''
        SELECT MIN(c.id) as id, c.name, c.edition, c.condition, SUM(c.count) as count, c.foil, c.card_number,
               COALESCE(k.type_line, '') as type_line,
               COALESCE(k.colors, '[]') as colors,
               COALESCE(k.image_uri_small, '') as image_uri,
               COALESCE(k.mana_cost, '') as mana_cost,
               SUM(c.count) as total_count
        FROM collection c
        LEFT JOIN cards k ON c.card_id = k.id
        {where_clause}
        GROUP BY c.name, c.edition, c.condition, c.foil
        {order_clause}
        LIMIT ? OFFSET ?
    '''

    cursor = db.execute(query, params + [per_page, offset])
    cards = [dict(row) for row in cursor.fetchall()]

    count_query = f'''SELECT COUNT(*) FROM (
                        SELECT 1 FROM collection c
                        LEFT JOIN cards k ON c.card_id = k.id
                        {where_clause}
                        GROUP BY c.name, c.edition, c.condition, c.foil
                     )'''
    cursor = db.execute(count_query, params)
    total = cursor.fetchone()[0]

    db.close()

    return render_template('collection/index.html',
                         cards=cards,
                         page=page,
                         per_page=per_page,
                         total=total,
                         sort_by=sort_by,
                         search_q=search_q,
                         filter_color=color,
                         filter_type=card_type,
                         filter_set=set_code,
                         filter_condition=condition,
                         filter_foil=foil)

@collection_bp.route('/collection/<int:card_id>')
def card_detail(card_id):
    db = get_db()
    cursor = db.execute('''
        SELECT c.name, c.edition, c.condition, c.foil
        FROM collection c
        WHERE c.id = ?
    ''', (card_id,))
    row = cursor.fetchone()

    if not row:
        db.close()
        return jsonify({'error': 'Card not found'}), 404

    card_name, edition, condition, foil = row
    # Find card details, preferring enriched card_id if any exist
    cursor = db.execute('''
        SELECT MIN(c.id) as id, c.name, c.edition, c.condition, SUM(c.count) as count,
               c.foil, MIN(c.card_number) as card_number, c.language, SUM(c.tradelist_count) as tradelist_count,
               MAX(c.signed) as signed, MAX(c.artist_proof) as artist_proof,
               MAX(c.altered_art) as altered_art, MAX(c.misprint) as misprint,
               MAX(c.promo) as promo, MAX(c.textless) as textless, MAX(c.my_price) as my_price,
               COALESCE(k.oracle_text, '') as oracle_text,
               COALESCE(k.rarity, '') as rarity,
               COALESCE(k.power, '') as power,
               COALESCE(k.toughness, '') as toughness,
               COALESCE(k.image_uri_normal, '') as image_uri,
               COALESCE(k.type_line, '') as type_line,
               COALESCE(k.mana_cost, '') as mana_cost,
               COALESCE(k.scryfall_id, '') as scryfall_id,
               COALESCE(k.set_code, '') as set_code
        FROM collection c
        LEFT JOIN cards k ON c.card_id = k.id
        WHERE c.name = ? AND c.edition = ? AND c.condition = ? AND c.foil = ?
        GROUP BY c.name, c.edition, c.condition, c.foil
    ''', (card_name, edition, condition, foil))
    row = cursor.fetchone()
    db.close()

    if not row:
        return jsonify({'error': 'Card not found'}), 404

    card = dict(row)
    return render_template('collection/card_detail.html', card=card)

@collection_bp.route('/enrich-card/<int:card_id>', methods=['POST'])
def enrich_card(card_id):
    db = get_db()
    cursor = db.execute('SELECT name, edition FROM collection WHERE id = ?', (card_id,))
    row = cursor.fetchone()

    if not row:
        db.close()
        return jsonify({'error': 'Card not found'}), 404

    name, edition = row
    client = ScryfallClient(db)

    try:
        success = client.enrich_card(card_id, name, edition, edition, max_retries=3)
        db.close()

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Could not find card on Scryfall'}), 400
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
        db = get_db()
        try:
            yield f'data: {{"status": "importing", "progress": 1, "message": "Starting import..."}}\n\n'

            # Count total rows first
            with open(filepath, newline='', encoding='utf-8-sig') as f:
                total_rows = sum(1 for _ in csv.DictReader(f))

            # Import CSV
            inserted, skipped = import_csv(str(filepath), db)
            yield f'data: {{"status": "enriching", "progress": 40, "message": "Starting enrichment..."}}\n\n'

            # Auto-enrich newly imported cards
            client = ScryfallClient(db)
            cursor = db.execute('SELECT id, name, edition FROM collection WHERE card_id IS NULL ORDER BY id')
            unenriched = cursor.fetchall()
            total_unenriched = len(unenriched)

            enriched = 0
            for i, row in enumerate(unenriched):
                if client.enrich_card(row['id'], row['name'], row['edition'], row['edition']):
                    enriched += 1

                progress = 40 + int((i + 1) / max(total_unenriched, 1) * 59)
                yield f'data: {{"status": "enriching", "progress": {progress}, "message": "Enriching card {i+1}/{total_unenriched}..."}}\n\n'

            db.close()
            yield f'data: {{"status": "complete", "progress": 100, "message": "Complete!", "inserted": {inserted}, "skipped": {skipped}, "enriched": {enriched}}}\n\n'
        except Exception as e:
            db.close()
            yield f'data: {{"status": "error", "message": "{str(e)}"}}\n\n'

    return Response(generate(), mimetype='text/event-stream')

