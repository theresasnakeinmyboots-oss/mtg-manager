"""
Auto-fill a commander deck from owned cards.

Algorithm:
1. Load EDHREC synergy scores for the commander (cached only — no live fetch).
2. Build a pool of owned candidate cards joined against scryfall_bulk for oracle text.
3. Tag each candidate with health categories (ramp, removal, draw, etc.).
4. Fill quota slots defined by the strategy, greedily picking highest-synergy owned cards
   that match each category. Cards can satisfy multiple categories but are only picked once.
5. Fill remaining non-land slots with highest-synergy owned cards not yet picked.
6. Fill land slots: owned non-basics that produce correct colours first, then basic lands.
7. Return a structured preview — caller decides whether to apply.

Target deck size: 99 mainboard + 1 commander = 100.
"""
import re
from app.strategy import STRATEGIES, DEFAULT_STRATEGY

# ── Category regexes (same as health checks + suggestion tags) ────────────────

_CAT_RX = [
    ('Ramp',      re.compile(
        r'search your library for (a|up to \d+) (basic )?land|'
        r'add \{[WUBRGC]\}|add (one|two|\d+) mana|add mana of any|untap target land',
        re.IGNORECASE)),
    ('Removal',   re.compile(
        r'destroy target|exile target|'
        r'deals \d+ damage to (any target|target creature|each creature)|'
        r'-\d+/-\d+|return target .* to (its owner|their owner)\'s hand|'
        r'counter target (spell|creature|artifact|enchantment)',
        re.IGNORECASE)),
    ('Card draw', re.compile(
        r'draw (a|two|three|\d+) card|investigate|whenever .* draw|'
        r'look at the top \d+ card|scry \d',
        re.IGNORECASE)),
    ('Protection', re.compile(
        r'hexproof|indestructible|regenerate|shroud|ward|\bprotection\b|'
        r'counter target spell|can\'t be countered',
        re.IGNORECASE)),
    ('Token gen', re.compile(
        r'create[s]? \w+ [\w\s]+ token|put[s]? \w+ [\w\s]+ token|populate',
        re.IGNORECASE)),
    ('Anthem',    re.compile(
        r'creatures you control get \+|other creatures you control get \+',
        re.IGNORECASE)),
    ('Tutor',     re.compile(r'search your library for .* card', re.IGNORECASE)),
    ('Pillowfort', re.compile(
        r"can't attack you|can't attack unless|costs?.* more to attack|goad",
        re.IGNORECASE)),
    ('Spell payoffs', re.compile(
        r'magecraft|whenever you cast an? (instant|sorcery)|prowess|storm',
        re.IGNORECASE)),
    ('Voltron',   re.compile(r'\bequip\b|enchant creature', re.IGNORECASE)),
    ('Stax pieces', re.compile(
        r"spell[s]? cost[s]? .* more|each player (can only|can't|skips)|"
        r"creatures (don't untap|can't attack)",
        re.IGNORECASE)),
]

BASIC_LAND_NAMES = {
    'plains', 'island', 'swamp', 'mountain', 'forest',
    'wastes', 'snow-covered plains', 'snow-covered island',
    'snow-covered swamp', 'snow-covered mountain', 'snow-covered forest',
}

_COLOR_BASICS = {'W': 'Plains', 'U': 'Island', 'B': 'Swamp', 'R': 'Mountain', 'G': 'Forest'}


def _card_cats(oracle, type_line):
    oracle = oracle or ''
    tl = type_line or ''
    cats = set()
    for label, rx in _CAT_RX:
        if rx.search(oracle):
            cats.add(label)
    if 'Instant' in tl or 'Sorcery' in tl:
        cats.add('Spells')
    if ('Equipment' in tl or 'Aura' in tl) and 'Voltron' not in cats:
        cats.add('Voltron')
    return cats


def _is_land(type_line):
    return 'Land' in (type_line or '')


def _is_basic(name):
    return name.lower() in BASIC_LAND_NAMES


def build_candidate_pool(db, commander_name, commander_color_identity):
    """
    Return list of candidate dicts for owned non-commander, non-basic cards.
    Each dict: name, scryfall_id, type_line, oracle_text, mana_cost,
               image_uri_normal, cmc, colors, synergy (float|None),
               owned_count, cats (set), is_land, price_usd
    """
    # Load EDHREC synergy lookup
    from app.edhrec import _name_to_slug
    slug = _name_to_slug(commander_name)
    edhrec_rows = db.execute(
        'SELECT name, synergy FROM edhrec_cards WHERE slug=?', [slug]
    ).fetchall()
    synergy_map = {}
    for r in edhrec_rows:
        nl = r['name'].lower()
        if nl not in synergy_map or r['synergy'] > synergy_map[nl]:
            synergy_map[nl] = r['synergy']

    # Owned cards with oracle data, summed by name
    owned_rows = db.execute('''
        SELECT LOWER(k.name) as nl, k.name, k.scryfall_id, k.type_line,
               k.oracle_text, k.mana_cost, k.image_uri_normal,
               k.cmc, k.colors, k.color_identity, b.price_usd,
               SUM(c.count) as owned_count
        FROM collection c
        JOIN cards k ON c.card_id = k.id
        LEFT JOIN scryfall_bulk b ON k.scryfall_id = b.scryfall_id
        WHERE k.name IS NOT NULL AND k.scryfall_id IS NOT NULL
        GROUP BY k.name
    ''').fetchall()

    import json as _json
    cmd_lower = commander_name.lower()
    pool = []
    seen = set()
    for r in owned_rows:
        nl = r['nl']
        if nl == cmd_lower or nl in seen:
            continue
        if _is_basic(nl):
            continue
        seen.add(nl)

        # Colour identity check — skip cards outside commander's identity
        try:
            card_ci = set(_json.loads(r['color_identity'] or '[]'))
        except Exception:
            card_ci = set()
        try:
            cmd_ci = set(_json.loads(commander_color_identity or '[]'))
        except Exception:
            cmd_ci = set()
        if card_ci and not card_ci.issubset(cmd_ci):
            continue

        synergy = synergy_map.get(nl)
        cats = _card_cats(r['oracle_text'], r['type_line'])

        pool.append({
            'name':             r['name'],
            'scryfall_id':      r['scryfall_id'],
            'type_line':        r['type_line'] or '',
            'oracle_text':      r['oracle_text'] or '',
            'mana_cost':        r['mana_cost'] or '',
            'image_uri_normal': r['image_uri_normal'],
            'cmc':              float(r['cmc'] or 0),
            'colors':           r['colors'] or '[]',
            'synergy':          synergy,
            'owned_count':      r['owned_count'],
            'cats':             cats,
            'is_land':          _is_land(r['type_line']),
            'price_usd':        r['price_usd'],
            'in_edhrec':        synergy is not None,
        })

    return pool


def _sort_key(card):
    """Higher synergy first; unknown synergy (None) treated as neutral 0."""
    syn = card['synergy'] if card['synergy'] is not None else 0.0
    return -syn


def auto_fill(db, deck_id, commander_name, commander_color_identity,
              strategy_key, target_size=99):
    """
    Return a dict:
      picked      — list of card dicts to add (mainboard)
      skipped     — owned candidates not picked (with reason)
      land_picks  — list of land card dicts
      slots_filled — {category: count}
      stats       — summary numbers
    """
    import json as _json

    strategy = STRATEGIES.get(strategy_key) or STRATEGIES[DEFAULT_STRATEGY]
    checks = strategy['health_checks']

    # Cards already in the deck (skip them)
    existing = {
        r['name'].lower()
        for r in db.execute('SELECT name FROM deck_cards WHERE deck_id=?', [deck_id]).fetchall()
    }

    pool = build_candidate_pool(db, commander_name, commander_color_identity)

    non_lands = sorted([c for c in pool if not c['is_land']], key=_sort_key)
    lands     = sorted([c for c in pool if c['is_land']],     key=_sort_key)

    # Remove cards already in deck
    non_lands = [c for c in non_lands if c['name'].lower() not in existing]
    lands     = [c for c in lands     if c['name'].lower() not in existing]

    # ── Quota filling ─────────────────────────────────────────────────────────
    # Map strategy health check labels to oracle category tags
    LABEL_TO_CAT = {
        'Removal':       'Removal',
        'Card draw':     'Card draw',
        'Ramp':          'Ramp',
        'Protection':    'Protection',
        'Win cons':      None,   # handled by big-threat fallback
        'Pillowfort':    'Pillowfort',
        'Political':     None,
        'Voltron':       'Voltron',
        'Token gen':     'Token gen',
        'Anthem':        'Anthem',
        'Stax pieces':   'Stax pieces',
        'Spells':        'Spells',
        'Spell payoffs': 'Spell payoffs',
    }

    # Target counts per category (use the 'ok' threshold, capped at sensible max)
    targets = {}
    for label, thresholds in checks.items():
        cat = LABEL_TO_CAT.get(label)
        if cat:
            targets[cat] = min(thresholds['ok'], 15)

    picked = []
    picked_names = set()
    slots_filled = {cat: 0 for cat in targets}

    def pick(card):
        picked.append(card)
        picked_names.add(card['name'].lower())
        for cat in card['cats']:
            if cat in slots_filled:
                slots_filled[cat] += 1

    # Pass 1: fill each category quota from highest-synergy candidates
    for cat, target in targets.items():
        for card in non_lands:
            if slots_filled[cat] >= target:
                break
            if card['name'].lower() in picked_names:
                continue
            if cat in card['cats']:
                pick(card)

    # Pass 2: fill remaining non-land slots with best remaining synergy cards
    # Commander decks: 99 cards total, ~37 lands → ~62 non-lands
    non_land_target = target_size - 37
    remaining_non_lands = [c for c in non_lands if c['name'].lower() not in picked_names]
    for card in remaining_non_lands:
        if len(picked) >= non_land_target:
            break
        pick(card)

    # ── Land filling ──────────────────────────────────────────────────────────
    land_picks = []
    land_names = set()

    # Owned non-basic lands first
    for card in lands:
        if len(land_picks) >= 37:
            break
        land_picks.append(card)
        land_names.add(card['name'].lower())

    # Top up with basic lands matching commander colour identity
    basics_needed = 37 - len(land_picks)
    if basics_needed > 0:
        try:
            cmd_ci = list(set(_json.loads(commander_color_identity or '[]')))
        except Exception:
            cmd_ci = []
        available_basics = [_COLOR_BASICS[c] for c in cmd_ci if c in _COLOR_BASICS]
        if not available_basics:
            available_basics = ['Swamp']  # fallback

        # Check which basics are owned
        basic_owned = {}
        for basic_name in available_basics:
            row = db.execute('''
                SELECT SUM(c.count) as cnt FROM collection c
                JOIN cards k ON c.card_id = k.id
                WHERE LOWER(k.name) = LOWER(?)
            ''', [basic_name]).fetchone()
            basic_owned[basic_name] = int(row['cnt'] or 0) if row else 0

        # Distribute evenly across colours, draw from owned first
        per_basic = max(1, basics_needed // len(available_basics))
        remainder = basics_needed - per_basic * len(available_basics)
        for i, basic_name in enumerate(available_basics):
            count = per_basic + (1 if i < remainder else 0)
            owned = basic_owned.get(basic_name, 0)
            # Fetch scryfall data for this basic
            bulk = db.execute(
                'SELECT scryfall_id, name, type_line, image_uri_normal, mana_cost, cmc, colors FROM scryfall_bulk WHERE LOWER(name)=LOWER(?) LIMIT 1',
                [basic_name]
            ).fetchone()
            land_picks.append({
                'name':             basic_name,
                'scryfall_id':      bulk['scryfall_id'] if bulk else None,
                'type_line':        bulk['type_line'] if bulk else 'Basic Land',
                'mana_cost':        '',
                'image_uri_normal': bulk['image_uri_normal'] if bulk else None,
                'cmc':              0,
                'colors':           '[]',
                'synergy':          None,
                'in_edhrec':        False,
                'is_basic':         True,
                'owned_count':      owned,
                'count':            count,
                'price_usd':        None,
                'cats':             set(),
            })

    # Normalise non-basic land entries to have a count field
    for c in land_picks:
        if 'count' not in c:
            c['count'] = 1
    for c in picked:
        c['count'] = 1

    stats = {
        'total_picked':    len(picked) + len(land_picks),
        'non_land_picked': len(picked),
        'land_picked':     len(land_picks),
        'from_edhrec':     sum(1 for c in picked if c.get('in_edhrec')),
        'owned_pool_size': len(non_lands) + len(lands),
        'slots_filled':    slots_filled,
    }

    return {
        'picked':      picked,
        'land_picks':  land_picks,
        'slots_filled': slots_filled,
        'stats':       stats,
    }
