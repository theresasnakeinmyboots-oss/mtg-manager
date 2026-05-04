import sqlite3
from pathlib import Path
import config

def get_db():
    db = sqlite3.connect(str(config.DB_PATH))
    db.row_factory = sqlite3.Row
    return db

def init_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = get_db()
    migration_file = Path(__file__).parent.parent / 'migrations' / '001_init.sql'
    with open(migration_file) as f:
        db.executescript(f.read())
    db.commit()
    db.close()
