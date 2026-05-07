from flask import Flask
from app.database import init_db
from app.utils import format_mana_cost, format_set_symbol, format_color_symbols
import config
from pathlib import Path

def create_app():
    static_path = Path(__file__).parent.parent / 'static'
    app = Flask(__name__, static_folder=str(static_path), static_url_path='/static')
    app.config['SECRET_KEY'] = config.SECRET_KEY
    app.config['DEBUG'] = config.FLASK_DEBUG

    init_db()

    # Register template filters
    app.jinja_env.filters['format_mana_cost'] = format_mana_cost
    app.jinja_env.filters['format_set_symbol'] = format_set_symbol
    app.jinja_env.filters['format_color_symbols'] = format_color_symbols

    from app.routes.collection import collection_bp
    from app.routes.collections import collections_bp
    app.register_blueprint(collection_bp)
    app.register_blueprint(collections_bp)

    return app
