from flask import Blueprint, render_template, request, jsonify, Response
from app.database import get_db
from app.scryfall import ScryfallClient
from app.importer import import_csv
from werkzeug.utils import secure_filename
from datetime import datetime
from pathlib import Path
import config
import csv

collections_bp = Blueprint('collections', __name__, url_prefix='/collections')


def get_all_collections(db):
    return db.execute(
        '''SELECT c.*,
               COALESCE(SUM(col.count), 0) as card_count,
               COALESCE(SUM(
                   col.count * CASE WHEN col.foil = 'foil'
                       THEN COALESCE(k.price_usd_foil, k.price_usd, 0)
                       ELSE COALESCE(k.price_usd, 0)
                   END
               ), 0) as total_value
           FROM collections c
           LEFT JOIN collection col ON col.collection_id = c.id
           LEFT JOIN cards k ON col.card_id = k.id
           WHERE c.is_staging = 0
           GROUP BY c.id
           ORDER BY c.name'''
    ).fetchall()


@collections_bp.route('/')
def index():
    db = get_db()
    collections = get_all_collections(db)
    staging = db.execute(
        'SELECT c.*, COUNT(col.id) as card_count FROM collections c '
        'LEFT JOIN collection col ON col.collection_id = c.id '
        'WHERE c.is_staging = 1 GROUP BY c.id ORDER BY c.created_at DESC'
    ).fetchall()
    db.close()
    return render_template('collections/index.html', collections=collections, staging=staging)


@collections_bp.route('/new', methods=['POST'])
def new_collection():
    name = request.json.get('name', '').strip()
    description = request.json.get('description', '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    db = get_db()
    cursor = db.execute(
        'INSERT INTO collections (name, description, is_staging, created_at) VALUES (?, ?, 0, ?)',
        (name, description, datetime.utcnow().isoformat())
    )
    db.commit()
    cid = cursor.lastrowid
    db.close()
    return jsonify({'id': cid, 'name': name})


@collections_bp.route('/<int:collection_id>/delete', methods=['POST'])
def delete_collection(collection_id):
    db = get_db()
    row = db.execute('SELECT * FROM collections WHERE id = ?', (collection_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({'error': 'Not found'}), 404
    db.execute('DELETE FROM collection WHERE collection_id = ?', (collection_id,))
    db.execute('DELETE FROM collections WHERE id = ?', (collection_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})


@collections_bp.route('/<int:collection_id>/rename', methods=['POST'])
def rename_collection(collection_id):
    name = request.json.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    db = get_db()
    db.execute('UPDATE collections SET name = ? WHERE id = ?', (name, collection_id))
    db.commit()
    db.close()
    return jsonify({'success': True})


@collections_bp.route('/import', methods=['POST'])
def import_to_staging():
    """Upload a CSV into a fresh staging collection, then stream enrichment progress."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({'error': 'Only CSV files allowed'}), 400

    filename = secure_filename(file.filename)
    filepath = config.DATA_DIR / filename
    file.save(str(filepath))

    def generate():
        db = get_db()
        try:
            yield f'data: {{"status": "importing", "progress": 1, "message": "Starting import..."}}\n\n'

            # Create staging collection
            label = f'Import: {filename} {datetime.utcnow().strftime("%Y-%m-%d %H:%M")}'
            cursor = db.execute(
                'INSERT INTO collections (name, description, is_staging, created_at) VALUES (?, ?, 1, ?)',
                (label, '', datetime.utcnow().isoformat())
            )
            staging_id = cursor.lastrowid
            db.commit()

            inserted, skipped = import_csv(str(filepath), db, collection_id=staging_id)
            yield f'data: {{"status": "enriching", "progress": 40, "message": "Starting enrichment..."}}\n\n'

            client = ScryfallClient(db)
            cursor = db.execute(
                'SELECT id, name, edition FROM collection WHERE card_id IS NULL AND collection_id = ? ORDER BY id',
                (staging_id,)
            )
            unenriched = cursor.fetchall()
            total_unenriched = len(unenriched)

            from app.scryfall import EDITION_TO_SET_CODE
            import json as _json
            enriched = 0
            failed = []
            for i, row in enumerate(unenriched):
                set_code = EDITION_TO_SET_CODE.get(row['edition'], row['edition'])
                ok, reason = client.enrich_card(row['id'], row['name'], row['edition'], set_code)
                if ok:
                    enriched += 1
                else:
                    failed.append({'name': row['name'], 'edition': row['edition'], 'reason': reason})
                progress = 40 + int((i + 1) / max(total_unenriched, 1) * 59)
                yield 'data: ' + _json.dumps({
                    'status': 'card', 'progress': progress,
                    'name': row['name'], 'edition': row['edition'], 'ok': ok,
                    'reason': reason, 'current': i + 1, 'total': total_unenriched
                }) + '\n\n'

            db.close()
            yield 'data: ' + _json.dumps({
                'status': 'complete', 'progress': 100, 'message': 'Complete!',
                'inserted': inserted, 'skipped': skipped, 'enriched': enriched,
                'staging_id': staging_id, 'staging_name': label,
                'failed': failed
            }) + '\n\n'
        except Exception as e:
            db.close()
            yield 'data: ' + _json.dumps({'status': 'error', 'message': str(e)}) + '\n\n'

    return Response(generate(), mimetype='text/event-stream')


@collections_bp.route('/assign', methods=['POST'])
def assign_staging():
    """Move all cards from a staging collection into a real collection."""
    data = request.json
    staging_id = data.get('staging_id')
    dest_id = data.get('dest_id')       # existing collection id, or null
    dest_name = data.get('dest_name', '').strip()  # if creating new

    db = get_db()

    # Resolve or create destination
    if not dest_id:
        if not dest_name:
            db.close()
            return jsonify({'error': 'Destination collection required'}), 400
        cursor = db.execute(
            'INSERT INTO collections (name, description, is_staging, created_at) VALUES (?, ?, 0, ?)',
            (dest_name, '', datetime.utcnow().isoformat())
        )
        dest_id = cursor.lastrowid
        db.commit()

    # Move cards: merge if (name, edition, card_number, condition, foil) match in dest
    staged = db.execute(
        'SELECT * FROM collection WHERE collection_id = ?', (staging_id,)
    ).fetchall()

    moved = merged = 0
    for card in staged:
        existing = db.execute(
            '''SELECT id FROM collection
               WHERE collection_id = ? AND name = ? AND edition = ?
               AND card_number = ? AND condition = ? AND foil = ?''',
            (dest_id, card['name'], card['edition'],
             card['card_number'], card['condition'], card['foil'])
        ).fetchone()

        if existing:
            db.execute('UPDATE collection SET count = count + ? WHERE id = ?',
                       (card['count'], existing['id']))
            db.execute('DELETE FROM collection WHERE id = ?', (card['id'],))
            merged += 1
        else:
            db.execute('UPDATE collection SET collection_id = ? WHERE id = ?',
                       (dest_id, card['id']))
            moved += 1

    # Delete the now-empty staging collection
    db.execute('DELETE FROM collections WHERE id = ?', (staging_id,))
    db.commit()

    dest_name_actual = db.execute(
        'SELECT name FROM collections WHERE id = ?', (dest_id,)
    ).fetchone()['name']
    db.close()

    return jsonify({'success': True, 'moved': moved, 'merged': merged,
                    'dest_id': dest_id, 'dest_name': dest_name_actual})
