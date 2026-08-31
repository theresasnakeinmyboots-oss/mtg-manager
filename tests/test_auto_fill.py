import json
import tempfile
import pytest
from pathlib import Path
from datetime import datetime

from app.database import get_db, init_db
from app.auto_fill import auto_fill, _curve_target_counts, CURVE_BUCKETS, LAND_SLOTS
from app.edhrec import _name_to_slug

COMMANDER = 'Test Commander'
COMMANDER_SID = 'sid-commander'


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    """auto_fill() now self-fetches EDHREC synergy/combo data on a cache miss —
    keep tests hermetic by making that path fail fast instead of hitting the
    network, exactly like a live fetch failure would (falls back gracefully)."""
    import app.edhrec as edhrec_mod
    monkeypatch.setattr(edhrec_mod, '_fetch_edhrec', lambda slug: None)

    class _DeadSession:
        def get(self, *a, **kw):
            raise RuntimeError('network disabled in tests')

    monkeypatch.setattr(edhrec_mod, '_session', lambda: _DeadSession())


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        import config
        original_data_dir = config.DATA_DIR
        original_db_path = config.DB_PATH
        config.DATA_DIR = Path(tmpdir)
        config.DB_PATH = Path(tmpdir) / 'test.db'
        init_db()
        db = get_db()
        yield db
        db.close()
        config.DATA_DIR = original_data_dir
        config.DB_PATH = original_db_path


def _mk_deck(db, fmt='commander'):
    cur = db.execute(
        'INSERT INTO decks (name, format, description, created_at) VALUES (?,?,?,?)',
        ['Test Deck', fmt, '', datetime.utcnow().isoformat()]
    )
    deck_id = cur.lastrowid
    db.execute(
        'INSERT INTO deck_cards (deck_id, scryfall_id, name, count, board, added_at) VALUES (?,?,?,?,?,?)',
        [deck_id, COMMANDER_SID, COMMANDER, 1, 'commander', datetime.utcnow().isoformat()]
    )
    db.commit()
    return deck_id


def _add_bulk(db, scryfall_id, name, set_code='TST', type_line='Creature',
              cmc=2, colors=None, color_identity=None, oracle_text='', price_usd=None):
    db.execute('''
        INSERT INTO scryfall_bulk (scryfall_id, name, set_code, set_name, collector_number,
                                    mana_cost, cmc, colors, color_identity, type_line,
                                    oracle_text, rarity, price_usd)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (scryfall_id, name, set_code, set_code, '1', '', cmc,
          json.dumps(colors or []), json.dumps(color_identity or []),
          type_line, oracle_text, 'common', price_usd))


def _add_owned(db, scryfall_id, name, type_line='Creature', cmc=2, colors=None,
               color_identity=None, oracle_text='', count=1):
    """Insert into scryfall_bulk (for price/basic lookups) + cards + collection (as owned)."""
    _add_bulk(db, scryfall_id, name, type_line=type_line, cmc=cmc, colors=colors,
              color_identity=color_identity, oracle_text=oracle_text)
    cur = db.execute('''
        INSERT INTO cards (scryfall_id, name, set_code, set_name, collector_number,
                            mana_cost, cmc, colors, color_identity, type_line, oracle_text)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    ''', (scryfall_id, name, 'TST', 'TST', '1', '', cmc,
          json.dumps(colors or []), json.dumps(color_identity or []), type_line, oracle_text))
    card_id = cur.lastrowid
    db.execute('''
        INSERT INTO collection (card_id, scryfall_id, name, edition, card_number, count,
                                 condition, imported_at)
        VALUES (?,?,?,?,?,?,?,?)
    ''', (card_id, scryfall_id, name, 'TST', '1', count, 'NM', datetime.utcnow().isoformat()))
    db.commit()


def _add_basics(db):
    for name, sid in [('Plains', 'sid-plains'), ('Island', 'sid-island'),
                       ('Swamp', 'sid-swamp'), ('Mountain', 'sid-mountain'),
                       ('Forest', 'sid-forest')]:
        _add_bulk(db, sid, name, type_line='Basic Land', cmc=0)
    db.commit()


def _setup_commander(db, color_identity, type_line='Legendary Creature'):
    _add_bulk(db, COMMANDER_SID, COMMANDER, type_line=type_line,
              cmc=3, color_identity=color_identity)
    db.commit()
    return _mk_deck(db)


# ── Curve target math ───────────────────────────────────────────────────────

def test_curve_target_counts_sums_to_total():
    for total in (0, 1, 37, 62, 99):
        counts = _curve_target_counts(total)
        assert sum(counts.values()) == total
        assert set(counts.keys()) == {label for label, *_ in CURVE_BUCKETS}


# ── Basic land guarantee ─────────────────────────────────────────────────────

def test_guarantees_one_basic_per_colour_even_with_many_nonbasics(temp_db):
    db = temp_db
    _add_basics(db)
    deck_id = _setup_commander(db, ['U', 'B'])

    # Own 40 generic nonbasic lands (more than LAND_SLOTS) that don't fix colour.
    for i in range(40):
        _add_owned(db, f'sid-land-{i}', f'Generic Land {i}', type_line='Land', cmc=0)

    result = auto_fill(db, deck_id, COMMANDER, json.dumps(['U', 'B']), 'midrange')

    names = {c['name'] for c in result['land_picks']}
    assert 'Island' in names
    assert 'Swamp' in names
    # No colours outside the identity should sneak in
    assert 'Plains' not in names
    assert 'Mountain' not in names
    assert 'Forest' not in names


def test_land_total_hits_land_slots_when_pool_is_large(temp_db):
    db = temp_db
    _add_basics(db)
    deck_id = _setup_commander(db, ['G'])
    for i in range(50):
        _add_owned(db, f'sid-land-{i}', f'Generic Land {i}', type_line='Land', cmc=0)

    result = auto_fill(db, deck_id, COMMANDER, json.dumps(['G']), 'midrange')
    total_lands = sum(c['count'] for c in result['land_picks'])
    assert total_lands == LAND_SLOTS


# ── Mana curve ────────────────────────────────────────────────────────────

def test_curve_aware_fill_does_not_dump_all_high_synergy_low_cmc(temp_db):
    db = temp_db
    _add_basics(db)
    deck_id = _setup_commander(db, ['R'])

    # 40 two-drops with no health-check oracle text (won't be quota-picked),
    # all far more "synergistic" than the six-drops below.
    for i in range(40):
        _add_owned(db, f'sid-two-{i}', f'Two Drop {i}', type_line='Creature', cmc=2)
    for i in range(40):
        _add_owned(db, f'sid-six-{i}', f'Six Drop {i}', type_line='Creature', cmc=6)

    result = auto_fill(db, deck_id, COMMANDER, json.dumps(['R']), 'midrange')

    picked_cmcs = [c['cmc'] for c in result['picked']]
    two_count = sum(1 for c in picked_cmcs if c == 2)
    six_plus_count = sum(1 for c in picked_cmcs if c >= 6)

    curve_row = {row['label']: row for row in result['curve']}
    # Both buckets get at least their target share — no bucket is starved to 0
    # the way a blind highest-synergy-first sort would starve the 6-drops.
    assert two_count >= curve_row['2']['target'] > 0
    assert six_plus_count >= curve_row['6+']['target'] > 0
    # The pool only has 2-drops and 6-drops, so the other buckets' targets go
    # unmet — the round-robin fallback then tops up the shortfall one card
    # per bucket per sweep, so the *additional* cards beyond each bucket's own
    # target land split evenly rather than all going to one bucket.
    two_extra = two_count - curve_row['2']['target']
    six_extra = six_plus_count - curve_row['6+']['target']
    assert abs(two_extra - six_extra) <= 1
    assert two_count + six_plus_count == len(result['picked'])


# ── Auto-fetch EDHREC data ──────────────────────────────────────────────────

def test_fetches_and_caches_edhrec_data_when_missing(temp_db, monkeypatch):
    db = temp_db
    _add_basics(db)
    deck_id = _setup_commander(db, ['R'])
    _add_owned(db, 'sid-bolt', 'Test Bolt', type_line='Instant', cmc=1)

    slug = _name_to_slug(COMMANDER)
    assert db.execute('SELECT 1 FROM edhrec_cache WHERE slug=?', [slug]).fetchone() is None

    fake_raw = {
        'container': {'json_dict': {
            'card': {'num_decks': 1234},
            'cardlists': [{
                'tag': 'topcards',
                'cardviews': [{
                    'name': 'Test Bolt', 'id': 'sid-bolt', 'synergy': 0.42,
                    'inclusion': 900, 'num_decks': 900, 'potential_decks': 1000,
                }],
            }],
        }},
    }
    import app.edhrec as edhrec_mod
    monkeypatch.setattr(edhrec_mod, '_fetch_edhrec', lambda s: fake_raw)

    result = auto_fill(db, deck_id, COMMANDER, json.dumps(['R']), 'midrange')

    # It should have fetched live and persisted the result...
    row = db.execute('SELECT num_decks FROM edhrec_cache WHERE slug=?', [slug]).fetchone()
    assert row is not None
    assert row['num_decks'] == 1234
    # ...and used the synergy score it just fetched to rank the pick.
    bolt = next(c for c in result['picked'] if c['name'] == 'Test Bolt')
    assert bolt['synergy'] == pytest.approx(0.42)
    assert bolt['in_edhrec'] is True
    assert result['stats']['from_edhrec'] == 1


# ── Combos ────────────────────────────────────────────────────────────────

def test_picks_combo_pieces_fully_owned(temp_db):
    db = temp_db
    _add_basics(db)
    deck_id = _setup_commander(db, ['U', 'B'])

    _add_owned(db, 'sid-piece-a', 'Combo Piece A', type_line='Artifact', cmc=2)
    _add_owned(db, 'sid-piece-b', 'Combo Piece B', type_line='Creature', cmc=3)

    slug = _name_to_slug(COMMANDER)
    combos_json = json.dumps([{
        'comboId': 'combo-1',
        'href': '/combos/x/combo-1',
        'cards': [
            {'name': COMMANDER, 'scryfall_id': COMMANDER_SID},
            {'name': 'Combo Piece A', 'scryfall_id': 'sid-piece-a'},
            {'name': 'Combo Piece B', 'scryfall_id': 'sid-piece-b'},
        ],
        'results': ['Infinite mana'],
        'count': 50,
        'bracket': '',
    }])
    db.execute(
        'INSERT INTO combo_cache (slug, combos_json, fetched_at) VALUES (?,?,?)',
        [slug, combos_json, datetime.utcnow().isoformat()]
    )
    db.commit()

    result = auto_fill(db, deck_id, COMMANDER, json.dumps(['U', 'B']), 'midrange')

    picked_names = {c['name'] for c in result['picked']}
    assert 'Combo Piece A' in picked_names
    assert 'Combo Piece B' in picked_names
    assert len(result['combos']) == 1
    assert result['combos'][0]['result'] == 'Infinite mana'
    assert set(result['combos'][0]['cards_added']) == {'Combo Piece A', 'Combo Piece B'}

    piece_a = next(c for c in result['picked'] if c['name'] == 'Combo Piece A')
    assert 'Combo' in piece_a['cats']


def test_skips_combo_with_unowned_piece(temp_db):
    db = temp_db
    _add_basics(db)
    deck_id = _setup_commander(db, ['U', 'B'])

    _add_owned(db, 'sid-piece-a', 'Combo Piece A', type_line='Artifact', cmc=2)
    # 'Combo Piece C' deliberately not owned — combo should be skipped entirely.

    slug = _name_to_slug(COMMANDER)
    combos_json = json.dumps([{
        'comboId': 'combo-2',
        'href': '/combos/x/combo-2',
        'cards': [
            {'name': COMMANDER, 'scryfall_id': COMMANDER_SID},
            {'name': 'Combo Piece A', 'scryfall_id': 'sid-piece-a'},
            {'name': 'Combo Piece C', 'scryfall_id': 'sid-piece-c'},
        ],
        'results': ['Win the game'],
        'count': 10,
        'bracket': '',
    }])
    db.execute(
        'INSERT INTO combo_cache (slug, combos_json, fetched_at) VALUES (?,?,?)',
        [slug, combos_json, datetime.utcnow().isoformat()]
    )
    db.commit()

    result = auto_fill(db, deck_id, COMMANDER, json.dumps(['U', 'B']), 'midrange')

    assert result['combos'] == []
    picked_names = {c['name'] for c in result['picked']}
    # Piece A is still eligible to be picked normally, just not tagged as a combo
    if 'Combo Piece A' in picked_names:
        card = next(c for c in result['picked'] if c['name'] == 'Combo Piece A')
        assert 'Combo' not in card['cats']


# ── Tribal strategy ──────────────────────────────────────────────────────────

def test_tribal_strategy_prioritises_type_and_payoffs(temp_db):
    db = temp_db
    _add_basics(db)
    deck_id = _setup_commander(db, ['R'], type_line='Legendary Creature — Dragon')

    # Owned pool: some Dragons (the tribe), a typal payoff naming Dragons, and
    # a pile of unrelated filler that would win on curve/synergy alone.
    for i in range(5):
        _add_owned(db, f'sid-dragon-{i}', f'Dragon {i}', type_line='Creature — Dragon', cmc=4)
    _add_owned(db, 'sid-lord', 'Dragon Lord', type_line='Creature — Wizard',
               oracle_text='Other Dragons you control get +1/+1.', cmc=3)
    for i in range(40):
        _add_owned(db, f'sid-filler-{i}', f'Filler {i}', type_line='Creature — Bear', cmc=3)

    result = auto_fill(db, deck_id, COMMANDER, json.dumps(['R']), 'tribal')

    picked_names = {c['name'] for c in result['picked']}
    for i in range(5):
        assert f'Dragon {i}' in picked_names
    assert 'Dragon Lord' in picked_names

    dragon_lord = next(c for c in result['picked'] if c['name'] == 'Dragon Lord')
    assert 'Typal payoffs' in dragon_lord['cats']
    dragon_0 = next(c for c in result['picked'] if c['name'] == 'Dragon 0')
    assert 'Type count' in dragon_0['cats']

    assert result['slots_filled']['Type count'] == 5
    assert result['slots_filled']['Typal payoffs'] == 1


def test_tribal_strategy_with_no_commander_tribe_is_inert(temp_db):
    db = temp_db
    _add_basics(db)
    # Commander has no creature subtype at all
    deck_id = _setup_commander(db, ['R'], type_line='Legendary Creature')
    _add_owned(db, 'sid-dragon-0', 'Dragon 0', type_line='Creature — Dragon', cmc=4)

    result = auto_fill(db, deck_id, COMMANDER, json.dumps(['R']), 'tribal')

    assert result['slots_filled']['Type count'] == 0
    assert result['slots_filled']['Typal payoffs'] == 0
