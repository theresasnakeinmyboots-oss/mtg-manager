from flask import Blueprint, render_template, request, jsonify
from werkzeug.utils import secure_filename
from pathlib import Path
import threading
import config

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

DATADB_PATH = config.DATA_DIR / 'data.db'
BULK_SYNC_LOG = config.DATA_DIR / 'bulk_sync.log'

_bulk_sync_running = False


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

    return render_template(
        'admin/index.html',
        datadb_exists=exists, datadb_size_kb=size_kb, datadb_mtime=mtime,
        bulk_sync_running=_bulk_sync_running,
        bulk_sync_log=log_tail,
    )


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
