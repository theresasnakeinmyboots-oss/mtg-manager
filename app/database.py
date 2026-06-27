import sqlite3
from pathlib import Path
import config

def get_db():
    db = sqlite3.connect(str(config.DB_PATH))
    db.row_factory = sqlite3.Row
    return db

def get_setting(db, key, default=None):
    row = db.execute('SELECT value FROM app_settings WHERE key=?', [key]).fetchone()
    return row['value'] if row else default


def set_setting(db, key, value):
    db.execute(
        'INSERT INTO app_settings (key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        [key, str(value)]
    )
    db.commit()


def init_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = get_db()

    migrations_dir = Path(__file__).parent.parent / 'migrations'
    for migration in sorted(migrations_dir.glob('*.sql')):
        with open(migration) as f:
            sql = f.read()
        # Run each statement individually so we can skip ones that already applied
        for statement in sql.split(';'):
            statement = statement.strip()
            if not statement:
                continue
            try:
                db.execute(statement)
            except sqlite3.OperationalError as e:
                # Ignore "already exists" and "duplicate column" errors so
                # re-running migrations on an existing DB is safe
                msg = str(e).lower()
                if 'already exists' in msg or 'duplicate column' in msg:
                    continue
                raise
    db.commit()
    db.close()
