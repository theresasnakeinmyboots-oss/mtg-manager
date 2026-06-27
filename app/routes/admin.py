from flask import Blueprint, render_template, request, jsonify
from werkzeug.utils import secure_filename
from pathlib import Path
import threading
import config
from app.database import get_db as _get_db, get_setting, set_setting

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

DATADB_PATH = config.DATA_DIR / 'data.db'
BULK_SYNC_LOG = config.DATA_DIR / 'bulk_sync.log'
DATADB_FETCH_LOG = config.DATA_DIR / 'datadb_fetch.log'

_bulk_sync_running = False
_datadb_fetch_running = False


def _run_datadb_fetch():
    global _datadb_fetch_running
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent.parent))
    try:
        import scripts.fetch_datadb as fd
        fd.main(str(DATADB_PATH), str(DATADB_FETCH_LOG))
    except Exception as e:
        with open(DATADB_FETCH_LOG, 'a') as log:
            log.write(f'\nError: {e}\n')
    finally:
        _datadb_fetch_running = False


def _run_bulk_sync():
    global _bulk_sync_running
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent.parent))
    try:
        import scripts.sync_bulk as sb
        sb.main()
    finally:
        _bulk_sync_running = False


@admin_bp.route('/', strict_slashes=False)
def index():
    from datetime import datetime
    from app.database import get_db
    from app.edhrec import CACHE_TTL_DAYS
    import json as _json

    exists = DATADB_PATH.exists()
    size_kb = round(DATADB_PATH.stat().st_size / 1024) if exists else None
    mtime = (
        datetime.fromtimestamp(DATADB_PATH.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        if exists else None
    )

    log_tail = ''
    if BULK_SYNC_LOG.exists():
        with open(BULK_SYNC_LOG, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        log_tail = ''.join(lines[-20:])

    fetch_log_tail = ''
    if DATADB_FETCH_LOG.exists():
        with open(DATADB_FETCH_LOG, encoding='utf-8', errors='replace') as f:
            fetch_log_tail = f.read()

    # Build EDHREC cache status per commander deck
    db = get_db()
    commander_decks = []
    decks = db.execute(
        "SELECT id, name FROM decks WHERE format='commander' ORDER BY name"
    ).fetchall()
    for deck in decks:
        cmd = db.execute(
            "SELECT dc.name FROM deck_cards dc WHERE dc.deck_id=? AND dc.board='commander' LIMIT 1",
            [deck['id']]
        ).fetchone()
        if not cmd:
            cmd = db.execute(
                "SELECT dc.name FROM deck_cards dc "
                "LEFT JOIN scryfall_bulk b ON dc.scryfall_id=b.scryfall_id "
                "WHERE dc.deck_id=? AND b.type_line LIKE '%Legendary%Creature%' "
                "ORDER BY b.cmc DESC, dc.name LIMIT 1",
                [deck['id']]
            ).fetchone()
        commander_name = cmd['name'] if cmd else None

        cache_row = None
        if commander_name:
            from app.edhrec import _name_to_slug
            slug = _name_to_slug(commander_name)
            cache_row = db.execute(
                'SELECT fetched_at FROM edhrec_cache WHERE slug=?', [slug]
            ).fetchone()

        commander_decks.append({
            'id': deck['id'],
            'name': deck['name'],
            'commander': commander_name,
            'cached_at': cache_row['fetched_at'][:10] if cache_row else None,
        })
    suggestions_per_type = int(get_setting(db, 'suggestions_per_type', 7))
    db.close()

    return render_template(
        'admin/index.html',
        datadb_exists=exists, datadb_size_kb=size_kb, datadb_mtime=mtime,
        bulk_sync_running=_bulk_sync_running,
        bulk_sync_log=log_tail,
        datadb_fetch_running=_datadb_fetch_running,
        datadb_fetch_log=fetch_log_tail,
        commander_decks=commander_decks,
        cache_ttl_days=CACHE_TTL_DAYS,
        suggestions_per_type=suggestions_per_type,
    )


@admin_bp.route('/settings', methods=['POST'])
def save_settings():
    db = _get_db()
    try:
        val = int(request.form.get('suggestions_per_type', 7))
        val = max(1, min(val, 50))
    except (ValueError, TypeError):
        val = 7
    set_setting(db, 'suggestions_per_type', val)
    db.close()
    return jsonify(ok=True, suggestions_per_type=val)


@admin_bp.route('/edhrec-refresh/<int:deck_id>', methods=['POST'])
def edhrec_refresh(deck_id):
    from app.database import get_db
    from app.edhrec import get_edhrec_data
    db = get_db()
    cmd = db.execute(
        "SELECT dc.name FROM deck_cards dc WHERE dc.deck_id=? AND dc.board='commander' LIMIT 1",
        [deck_id]
    ).fetchone()
    if not cmd:
        cmd = db.execute(
            "SELECT dc.name FROM deck_cards dc "
            "LEFT JOIN scryfall_bulk b ON dc.scryfall_id=b.scryfall_id "
            "WHERE dc.deck_id=? AND b.type_line LIKE '%Legendary%Creature%' "
            "ORDER BY b.cmc DESC, dc.name LIMIT 1",
            [deck_id]
        ).fetchone()
    if not cmd:
        db.close()
        return jsonify(error='No commander found'), 404
    result = get_edhrec_data(db, cmd['name'], force_refresh=True)
    db.close()
    if result:
        return jsonify(ok=True, commander=cmd['name'])
    return jsonify(error='EDHREC fetch failed'), 502


@admin_bp.route('/bulk-sync', methods=['POST'])
def bulk_sync():
    global _bulk_sync_running
    if _bulk_sync_running:
        return jsonify({'error': 'Sync already in progress'}), 409
    _bulk_sync_running = True
    t = threading.Thread(target=_run_bulk_sync, daemon=True)
    t.start()
    return jsonify({'started': True})


@admin_bp.route('/bulk-sync-log')
def bulk_sync_log():
    log_tail = ''
    if BULK_SYNC_LOG.exists():
        with open(BULK_SYNC_LOG, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        log_tail = ''.join(lines[-20:])
    return jsonify({'running': _bulk_sync_running, 'log': log_tail})


@admin_bp.route('/upload-datadb', methods=['POST'])
def upload_datadb():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not secure_filename(file.filename).lower().endswith('.db'):
        return jsonify({'error': 'Expected a .db file'}), 400
    file.save(str(DATADB_PATH))
    size_kb = round(DATADB_PATH.stat().st_size / 1024)
    return jsonify({'success': True, 'size_kb': size_kb})


@admin_bp.route('/fetch-datadb', methods=['POST'])
def fetch_datadb():
    global _datadb_fetch_running
    if _datadb_fetch_running:
        return jsonify({'error': 'Fetch already in progress'}), 409
    # Clear old log
    DATADB_FETCH_LOG.write_text('')
    _datadb_fetch_running = True
    t = threading.Thread(target=_run_datadb_fetch, daemon=True)
    t.start()
    return jsonify({'started': True})


@admin_bp.route('/fetch-datadb-log')
def fetch_datadb_log():
    log = ''
    if DATADB_FETCH_LOG.exists():
        log = DATADB_FETCH_LOG.read_text(encoding='utf-8', errors='replace')
    size_kb = round(DATADB_PATH.stat().st_size / 1024) if DATADB_PATH.exists() else None
    return jsonify({'running': _datadb_fetch_running, 'log': log, 'size_kb': size_kb})
