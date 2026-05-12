from flask import Blueprint, render_template, request, jsonify
from werkzeug.utils import secure_filename
from pathlib import Path
import config

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

DATADB_PATH = config.DATA_DIR / 'data.db'


@admin_bp.route('/', strict_slashes=False)
def index():
    exists = DATADB_PATH.exists()
    size_kb = round(DATADB_PATH.stat().st_size / 1024) if exists else None
    from datetime import datetime
    mtime = (
        datetime.fromtimestamp(DATADB_PATH.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        if exists else None
    )
    return render_template('admin/index.html', datadb_exists=exists, datadb_size_kb=size_kb, datadb_mtime=mtime)


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
