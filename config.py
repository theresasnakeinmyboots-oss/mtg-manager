import os
from pathlib import Path

DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
DB_PATH = DATA_DIR / 'mtg_collection.db'

SCRYFALL_API_BASE = 'https://api.scryfall.com'
SCRYFALL_RATE_LIMIT_MS = 200

FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-not-for-production')

CARDS_PER_PAGE = 50
