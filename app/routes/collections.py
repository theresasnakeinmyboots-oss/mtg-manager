from flask import Blueprint, render_template, request, jsonify, Response, send_file
from app.database import get_db
from app.scryfall import ScryfallClient
from app.importer import import_csv
from app.importer_dlens import import_dlens
from app.exporter_dlens import export_dlens
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
               ), 0) as total_value,
               SUM(CASE WHEN col.card_id IS NULL THEN col.count ELSE 0 END) as unenriched_count
           FROM collections c
           LEFT JOIN collection col ON col.collection_id = c.id
           LEFT JOIN cards k ON col.card_id = k.id
           WHERE c.is_staging = 0
           GROUP BY c.id
           ORDER BY c.name'''
    ).fetchall()


@collections_bp.route('/', strict_slashes=False)
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
    """Upload a CSV or .dlens file into a fresh staging collection, then stream enrichment progress."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    filename = secure_filename(file.filename)
    is_dlens = filename.lower().endswith('.dlens')
    is_csv = filename.lower().endswith('.csv')
    if not is_dlens and not is_csv:
        return jsonify({'error': 'Only .csv or .dlens files are supported'}), 400

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

            if is_dlens:
                import json as _json
                datadb_path = config.DATA_DIR / 'data.db'
                yield 'data: ' + _json.dumps({
                    'status': 'importing', 'progress': 10,
                    'message': f'Reading {filename}…'
                }) + '\n\n'
                inserted, skipped, missing, schema_info = import_dlens(
                    str(filepath), str(datadb_path), db, collection_id=staging_id
                )
                yield 'data: ' + _json.dumps({
                    'status': 'importing', 'progress': 35,
                    'message': (
                        f'Inserted {inserted}, skipped {skipped}, missing {len(missing)} — '
                        f'dlens cols: {schema_info["dlens_cols"]}, apk cols: {schema_info["apk_cols"]}'
                    )
                }) + '\n\n'
            else:
                import json as _json
                inserted, skipped = import_csv(str(filepath), db, collection_id=staging_id)
                missing = []
            yield f'data: {{"status": "enriching", "progress": 40, "message": "Starting enrichment..."}}\n\n'

            client = ScryfallClient(db)
            # For CSV: card_id is NULL — needs full enrichment
            # For dlens: card_id is set but cards row may be a stub (enriched_at IS NULL)
            cursor = db.execute(
                '''SELECT c.id, c.name, c.edition, c.card_number
                   FROM collection c
                   LEFT JOIN cards k ON k.id = c.card_id
                   WHERE c.collection_id = ?
                   AND (c.card_id IS NULL OR k.enriched_at IS NULL)
                   ORDER BY c.id''',
                (staging_id,)
            )
            unenriched = cursor.fetchall()
            total_unenriched = len(unenriched)

            import json as _json
            enriched = 0
            failed = []
            for i, row in enumerate(unenriched):
                set_code = client._set_name_map.get(row['edition'], row['edition'])
                ok, reason = client.enrich_card(row['id'], row['name'], row['edition'], set_code, collector_number=row['card_number'])
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
                'failed': failed,
                'missing': missing
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
               AND card_number = ? AND condition = ? AND foil IS ?''',
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


def _match_dest_row(db, dest_id, staged_card):
    """Find an existing row in dest_id that represents the same physical card as
    staged_card. Prefer matching by (scryfall_id, foil) so row ids — and therefore
    deck/set links pointing at them — survive a reimport even if condition/language
    metadata changed. Fall back to the looser (name, edition, card_number, condition,
    foil) key for rows that have no scryfall_id yet (unenriched)."""
    if staged_card['scryfall_id']:
        # Join through cards.scryfall_id too: collection.scryfall_id is a denormalized
        # copy that can lag behind, so don't trust it alone for the exact-match check.
        row = db.execute('''
            SELECT c.id FROM collection c
            LEFT JOIN cards k ON c.card_id = k.id
            WHERE c.collection_id = ? AND (c.scryfall_id = ? OR k.scryfall_id = ?) AND c.foil IS ?
        ''', (dest_id, staged_card['scryfall_id'], staged_card['scryfall_id'], staged_card['foil'])
        ).fetchone()
        if row:
            return row['id']
    return db.execute(
        '''SELECT id FROM collection
           WHERE collection_id = ? AND name = ? AND edition = ?
           AND card_number = ? AND condition = ? AND foil IS ?''',
        (dest_id, staged_card['name'], staged_card['edition'],
         staged_card['card_number'], staged_card['condition'], staged_card['foil'])
    ).fetchone()


@collections_bp.route('/replace-preview', methods=['POST'])
def replace_preview():
    """Dry-run a 'replace contents' reconciliation: report what would be kept,
    added, and removed without writing anything."""
    data = request.json
    staging_id = data.get('staging_id')
    dest_id = data.get('dest_id')
    if not staging_id or not dest_id:
        return jsonify({'error': 'staging_id and dest_id required'}), 400

    db = get_db()
    staged = db.execute('SELECT * FROM collection WHERE collection_id = ?', (staging_id,)).fetchall()
    dest_rows = db.execute('SELECT * FROM collection WHERE collection_id = ?', (dest_id,)).fetchall()

    matched_dest_ids = set()
    kept = added = 0
    for card in staged:
        match_id = _match_dest_row(db, dest_id, card)
        if match_id:
            matched_dest_ids.add(match_id)
            kept += 1
        else:
            added += 1

    removed_rows = [dict(r) for r in dest_rows if r['id'] not in matched_dest_ids]
    db.close()

    return jsonify({
        'kept': kept, 'added': added, 'removed': len(removed_rows),
        'removed_cards': [
            {'name': r['name'], 'edition': r['edition'], 'count': r['count']}
            for r in removed_rows
        ],
    })


@collections_bp.route('/replace-commit', methods=['POST'])
def replace_commit():
    """Reconcile dest_id's contents to exactly match staging_id's contents.
    Rows that match an existing dest row keep their id (and any deck/set links
    pointing at it survive). Unmatched dest rows are removed; unmatched staged
    rows are added. The staging collection is consumed."""
    data = request.json
    staging_id = data.get('staging_id')
    dest_id = data.get('dest_id')
    if not staging_id or not dest_id:
        return jsonify({'error': 'staging_id and dest_id required'}), 400

    db = get_db()
    staged = db.execute('SELECT * FROM collection WHERE collection_id = ?', (staging_id,)).fetchall()

    matched_dest_ids = set()
    kept = added = 0
    for card in staged:
        match_id = _match_dest_row(db, dest_id, card)
        if match_id:
            db.execute(
                '''UPDATE collection SET count = ?, tradelist_count = ?, condition = ?,
                   language = ?, card_id = ?, scryfall_id = ?, edition = ?, card_number = ?,
                   signed = ?, artist_proof = ?, altered_art = ?, misprint = ?, promo = ?,
                   textless = ?, my_price = ?
                   WHERE id = ?''',
                (card['count'], card['tradelist_count'], card['condition'], card['language'],
                 card['card_id'], card['scryfall_id'], card['edition'], card['card_number'],
                 card['signed'], card['artist_proof'], card['altered_art'], card['misprint'],
                 card['promo'], card['textless'], card['my_price'], match_id)
            )
            db.execute('DELETE FROM collection WHERE id = ?', (card['id'],))
            matched_dest_ids.add(match_id)
            kept += 1
        else:
            db.execute('UPDATE collection SET collection_id = ? WHERE id = ?', (dest_id, card['id']))
            matched_dest_ids.add(card['id'])
            added += 1

    removed = db.execute(
        'DELETE FROM collection WHERE collection_id = ? AND id NOT IN (SELECT value FROM json_each(?))',
        (dest_id, str(list(matched_dest_ids) or [0]))
    )
    removed_count = removed.rowcount

    db.execute('DELETE FROM collections WHERE id = ?', (staging_id,))
    db.commit()

    dest_name_actual = db.execute('SELECT name FROM collections WHERE id = ?', (dest_id,)).fetchone()['name']
    db.close()

    return jsonify({'success': True, 'kept': kept, 'added': added, 'removed': removed_count,
                    'dest_id': dest_id, 'dest_name': dest_name_actual})


@collections_bp.route('/<int:collection_id>/enrich', methods=['GET'])
def enrich_collection(collection_id):
    """SSE stream: enrich all unenriched cards in an existing collection."""
    db_check = get_db()
    row = db_check.execute('SELECT id FROM collections WHERE id = ? AND is_staging = 0', (collection_id,)).fetchone()
    db_check.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    def generate():
        import json as _json
        db = get_db()
        try:
            client = ScryfallClient(db)
            unenriched = db.execute(
                '''SELECT c.id, c.name, c.edition, c.card_number
                   FROM collection c
                   LEFT JOIN cards k ON k.id = c.card_id
                   WHERE c.collection_id = ?
                   AND (c.card_id IS NULL OR k.enriched_at IS NULL)
                   ORDER BY c.id''',
                (collection_id,)
            ).fetchall()
            total = len(unenriched)
            if total == 0:
                yield 'data: ' + _json.dumps({'status': 'complete', 'progress': 100,
                                               'enriched': 0, 'total': 0}) + '\n\n'
                db.close()
                return

            yield 'data: ' + _json.dumps({'status': 'enriching', 'progress': 0,
                                           'message': f'Enriching {total} cards…'}) + '\n\n'
            enriched = 0
            failed = []
            for i, row in enumerate(unenriched):
                set_code = client._set_name_map.get(row['edition'], row['edition'])
                ok, reason = client.enrich_card(
                    row['id'], row['name'], row['edition'], set_code,
                    collector_number=row['card_number']
                )
                if ok:
                    enriched += 1
                else:
                    failed.append({'name': row['name'], 'reason': reason})
                progress = int((i + 1) / total * 100)
                yield 'data: ' + _json.dumps({
                    'status': 'card', 'progress': progress,
                    'name': row['name'], 'ok': ok, 'current': i + 1, 'total': total
                }) + '\n\n'

            db.close()
            yield 'data: ' + _json.dumps({
                'status': 'complete', 'progress': 100,
                'enriched': enriched, 'total': total, 'failed': failed
            }) + '\n\n'
        except Exception as e:
            db.close()
            yield 'data: ' + _json.dumps({'status': 'error', 'message': str(e)}) + '\n\n'

    return Response(generate(), mimetype='text/event-stream')


@collections_bp.route('/<int:collection_id>/export-dlens', methods=['GET'])
def export_dlens_route(collection_id):
    """Export a collection as a .dlens file for import into Delver Lens."""
    db = get_db()
    coll = db.execute('SELECT name FROM collections WHERE id = ?', (collection_id,)).fetchone()
    if not coll:
        db.close()
        return jsonify({'error': 'Collection not found'}), 404

    datadb_path = config.DATA_DIR / 'data.db'
    safe_name = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in coll['name']).strip() or 'export'
    filename = f'{safe_name}_{datetime.utcnow().strftime("%Y%m%d_%H%M")}.dlens'
    dest_path = config.DATA_DIR / f'_export_tmp_{collection_id}.dlens'

    try:
        exported, missing = export_dlens(db, collection_id, str(datadb_path), str(dest_path),
                                          list_name=coll['name'])
    except FileNotFoundError as e:
        db.close()
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()

    if exported == 0:
        if dest_path.exists():
            dest_path.unlink()
        return jsonify({'error': 'No exportable cards (none are enriched / matched in data.db)',
                        'missing': missing}), 400

    response = send_file(str(dest_path), as_attachment=True, download_name=filename,
                          mimetype='application/octet-stream')
    response.headers['X-Export-Count'] = str(exported)
    response.headers['X-Export-Missing'] = str(len(missing))

    @response.call_on_close
    def _cleanup():
        if dest_path.exists():
            dest_path.unlink()

    return response
