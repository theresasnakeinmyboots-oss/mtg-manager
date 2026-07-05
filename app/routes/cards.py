from flask import Blueprint, render_template, abort
from app.database import get_db
from app.cards_repo import decks_containing, owned_total_for
import json

cards_bp = Blueprint('cards', __name__, url_prefix='/')


@cards_bp.route('/card/<scryfall_id>')
def reference(scryfall_id):
    """Read-only card reference page. No ownership data is mutated here —
    used for cards the user does not own (set view, deck view)."""
    db = get_db()
    row = db.execute('''
        SELECT b.scryfall_id, b.name, b.set_code, b.set_name, b.collector_number,
               b.mana_cost, b.cmc, b.colors, b.color_identity, b.type_line,
               b.oracle_text, b.flavor_text, b.power, b.toughness, b.rarity,
               b.legalities, b.image_uri_normal as image_uri, b.artist,
               b.price_usd, b.price_usd_foil, b.price_eur, b.price_eur_foil
        FROM scryfall_bulk b
        WHERE b.scryfall_id = ?
    ''', (scryfall_id,)).fetchone()

    if not row:
        db.close()
        abort(404)

    card = dict(row)

    try:
        legalities_raw = json.loads(card.get('legalities', '{}') or '{}')
        card['legal_formats'] = sorted(
            fmt.capitalize() for fmt, status in legalities_raw.items() if status == 'legal'
        )
    except Exception:
        card['legal_formats'] = []

    alternatives = db.execute('''
        SELECT scryfall_id, set_code, set_name, collector_number, image_uri_normal, rarity
        FROM scryfall_bulk
        WHERE name = ?
        ORDER BY set_name, CAST(collector_number AS INTEGER), collector_number
    ''', (card['name'],)).fetchall()
    alternatives = [dict(r) for r in alternatives]

    owned_total = owned_total_for(db, scryfall_id, card['name'])
    decks_with_card = decks_containing(db, scryfall_id, card['name'])

    db.close()
    return render_template('cards/reference.html', card=card, alternatives=alternatives,
                           owned_total=owned_total, decks_with_card=decks_with_card)
