"""
Auto-fill a commander deck from owned cards.

Algorithm:
1. Load EDHREC synergy scores for the commander — served from cache if fresh,
   otherwise fetched live and cached now (so this never needs a manual step first).
2. Build a pool of owned candidate cards joined against scryfall_bulk for oracle text.
3. Tag each candidate with health categories (ramp, removal, draw, etc.).
4. Pick any EDHREC combo for the commander that's fully assemblable from owned cards
   (every piece not already in the deck is something we own).
5. Fill quota slots defined by the strategy, greedily picking highest-synergy owned cards
   that match each category. Cards can satisfy multiple categories but are only picked once.
6. Fill remaining non-land slots curve-aware: round-robin across CMC buckets toward a
   standard Commander curve shape, rather than blindly taking the next-highest synergy card.
7. Fill land slots: owned non-basics first (capped so colour coverage isn't crowded out),
   then at least one basic land per colour in the commander's identity, topped up evenly.
8. Return a structured preview — caller decides whether to apply.

Target deck size: 99 mainboard + 1 commander = 100.
"""
import re
from app.strategy import (STRATEGIES, DEFAULT_STRATEGY,
                          RAMP_RX, REMOVAL_RX, DRAW_RX, PROTECTION_RX,
                          detect_tribe, _card_tribes, _tribe_pattern)
from app.mtg_constants import BASIC_LANDS_LOWER, COLOR_BASIC

# ── Category regexes ──────────────────────────────────────────────────────────
# The first four reuse the shared role patterns; the rest are quota-specific
# labels matching the strategy health-check names this module fills against.

_CAT_RX = [
    ('Ramp',       RAMP_RX),
    ('Removal',    REMOVAL_RX),
    ('Card draw',  DRAW_RX),
    ('Protection', PROTECTION_RX),
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

# Aliases for backwards-compatible internal references
BASIC_LAND_NAMES = BASIC_LANDS_LOWER
_COLOR_BASICS = COLOR_BASIC


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
    # Load EDHREC synergy lookup — uses the cache if fresh, otherwise fetches
    # and caches it now so auto-fill never has to be preceded by a manual step.
    from app.edhrec import get_edhrec_data
    edhrec_data = get_edhrec_data(db, commander_name)
    synergy_map = {}
    if edhrec_data:
        for r in edhrec_data['cards']:
            nl = r['name'].lower()
            if nl not in synergy_map or r['synergy'] > synergy_map[nl]:
                synergy_map[nl] = r['synergy']

    # Tribal lookup — only populated if the commander is a creature with a type
    cmd_bulk = db.execute(
        'SELECT type_line FROM scryfall_bulk WHERE LOWER(name)=LOWER(?) LIMIT 1', [commander_name]
    ).fetchone()
    tribes = detect_tribe(cmd_bulk['type_line'] if cmd_bulk else None)
    tribe_set = {t.lower() for t in tribes}
    tribe_pattern = _tribe_pattern(tribes)
    tribe_rx = re.compile(tribe_pattern, re.IGNORECASE) if tribe_pattern else None

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
        if tribe_set and 'Creature' in (r['type_line'] or '') and _card_tribes(r['type_line']) & tribe_set:
            cats.add('Type count')
        if tribe_rx and tribe_rx.search(r['oracle_text'] or ''):
            cats.add('Typal payoffs')

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


# ── Mana curve targets ──────────────────────────────────────────────────────
# A generic, strategy-agnostic Commander curve shape: light at the top and
# bottom, heaviest at 2-3 CMC. (label, min_cmc, max_cmc_or_None, share_of_nonlands)
CURVE_BUCKETS = [
    ('0-1', 0, 1,    0.10),
    ('2',   2, 2,    0.24),
    ('3',   3, 3,    0.22),
    ('4',   4, 4,    0.18),
    ('5',   5, 5,    0.14),
    ('6+',  6, None, 0.12),
]

LAND_SLOTS = 37


def _curve_bucket(cmc):
    cmc = cmc or 0
    for label, lo, hi, _ in CURVE_BUCKETS:
        if cmc >= lo and (hi is None or cmc <= hi):
            return label
    return CURVE_BUCKETS[-1][0]


def _curve_target_counts(total):
    """Split `total` nonland slots across CURVE_BUCKETS by share, largest-remainder
    rounding so the counts sum exactly to `total`."""
    raw = [(label, pct * total) for label, _, _, pct in CURVE_BUCKETS]
    counts = {label: int(v) for label, v in raw}
    remainder = total - sum(counts.values())
    for _frac, label in sorted(((v - int(v), label) for label, v in raw), reverse=True)[:remainder]:
        counts[label] += 1
    return counts


def _select_combo_picks(db, commander_name, pool_by_name, existing_lower, max_combos=3):
    """
    Find EDHREC combos for this commander that are fully achievable from owned
    cards (every piece not already in the deck is something we own), and return
    the cards needed to complete them.

    Returns (picks, summaries) — picks are candidate-pool card dicts (already
    tagged with a 'Combo' category), summaries describe what was picked and why.
    """
    from app.edhrec import get_commander_combos

    try:
        combos = get_commander_combos(db, commander_name)
    except Exception:
        combos = []
    combos = sorted(combos, key=lambda c: -(c.get('count') or 0))

    cmd_lower = commander_name.lower()
    picks = []
    picked_lower = set()
    summaries = []

    for combo in combos:
        if len(summaries) >= max_combos:
            break
        piece_names = [c['name'] for c in combo.get('cards', []) if c['name'].lower() != cmd_lower]
        if not piece_names:
            continue
        missing = [n for n in piece_names if n.lower() not in existing_lower]
        if not missing:
            continue  # already fully assembled in the deck — nothing to add
        if not all(n.lower() in pool_by_name for n in missing):
            continue  # can't fully assemble it from owned cards — skip

        added = []
        for n in missing:
            nl = n.lower()
            if nl in picked_lower:
                continue
            card = pool_by_name[nl]
            card['cats'] = set(card['cats']) | {'Combo'}
            picked_lower.add(nl)
            picks.append(card)
            added.append(n)

        if added:
            summaries.append({
                'result':      ', '.join(combo.get('results') or []) or 'Combo',
                'popularity':  combo.get('count', 0),
                'cards_added': added,
            })

    return picks, summaries


def auto_fill(db, deck_id, commander_name, commander_color_identity,
              strategy_key, target_size=99):
    """
    Return a dict:
      picked       — list of card dicts to add (mainboard)
      land_picks   — list of land card dicts
      slots_filled — {category: count} (strategy health-check quotas)
      curve        — [{label, target, filled}] mana curve report
      combos       — [{result, popularity, cards_added}] combos assembled from owned cards
      stats        — summary numbers
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
    pool_by_name = {c['name'].lower(): c for c in pool}

    non_lands = sorted([c for c in pool if not c['is_land']], key=_sort_key)
    lands     = sorted([c for c in pool if c['is_land']],     key=_sort_key)

    # Remove cards already in deck
    non_lands = [c for c in non_lands if c['name'].lower() not in existing]
    lands     = [c for c in lands     if c['name'].lower() not in existing]

    picked = []
    picked_names = set()

    # ── Combo pass ───────────────────────────────────────────────────────────
    # Grab any EDHREC combo for this commander that we can fully assemble from
    # owned cards, before anything else gets a chance at those slots.
    combo_picks, combo_summaries = _select_combo_picks(db, commander_name, pool_by_name, existing)
    combo_lands = []
    for card in combo_picks:
        if card['name'].lower() in picked_names:
            continue
        picked_names.add(card['name'].lower())
        (combo_lands if card['is_land'] else picked).append(card)

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
        'Type count':    'Type count',
        'Typal payoffs': 'Typal payoffs',
    }

    # Target counts per category (use the 'ok' threshold, capped at sensible max)
    targets = {}
    for label, thresholds in checks.items():
        cat = LABEL_TO_CAT.get(label)
        if cat:
            targets[cat] = min(thresholds['ok'], 15)

    slots_filled = {cat: 0 for cat in targets}

    def pick(card):
        picked.append(card)
        picked_names.add(card['name'].lower())
        for cat in card['cats']:
            if cat in slots_filled:
                slots_filled[cat] += 1

    # Combo picks were added straight to `picked` above (bypassing pick()) so
    # they can't be double-picked in the passes below — count them toward the
    # quota bars now.
    for card in picked:
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

    # Pass 2: curve-aware fill of remaining non-land slots. Round-robins across
    # CMC buckets — one card from each bucket per sweep — instead of grabbing
    # whichever card has the next-highest synergy overall, so a pool skewed
    # toward one CMC can't crowd out the rest of the curve. If a bucket runs
    # out of supply before hitting its target, a second round-robin sweeps
    # again with no per-bucket cap, splitting the shortfall evenly across
    # whatever buckets still have cards rather than dumping it all into one.
    non_land_target = target_size - LAND_SLOTS
    curve_targets = _curve_target_counts(non_land_target)
    curve_filled = {label: 0 for label, *_rest in CURVE_BUCKETS}
    for card in picked:
        curve_filled[_curve_bucket(card['cmc'])] += 1

    bucket_queues = {
        label: [c for c in non_lands if _curve_bucket(c['cmc']) == label]
        for label, *_rest in CURVE_BUCKETS
    }

    def _round_robin_fill(target_of):
        progress = True
        while len(picked) < non_land_target and progress:
            progress = False
            for label, *_rest in CURVE_BUCKETS:
                if len(picked) >= non_land_target:
                    break
                if curve_filled[label] >= target_of(label):
                    continue
                queue = bucket_queues[label]
                while queue and queue[0]['name'].lower() in picked_names:
                    queue.pop(0)
                if not queue:
                    continue
                pick(queue.pop(0))
                curve_filled[label] += 1
                progress = True

    _round_robin_fill(lambda label: curve_targets[label])
    _round_robin_fill(lambda label: non_land_target)  # shortfall, evenly spread

    curve_report = [
        {'label': label, 'target': curve_targets[label], 'filled': curve_filled[label]}
        for label, *_rest in CURVE_BUCKETS
    ]

    # ── Land filling ──────────────────────────────────────────────────────────
    land_picks = list(combo_lands)
    land_names = {c['name'].lower() for c in land_picks}

    try:
        cmd_ci = list(dict.fromkeys(_json.loads(commander_color_identity or '[]')))
    except Exception:
        cmd_ci = []
    required_basics = [_COLOR_BASICS[c] for c in cmd_ci if c in _COLOR_BASICS] or ['Swamp']

    # Reserve one slot per required colour *before* filling non-basics, so a
    # large owned non-basic-land pool can never crowd out basic colour coverage.
    nonbasic_budget = max(0, LAND_SLOTS - len(land_picks) - len(required_basics))
    nonbasic_cap = len(land_picks) + nonbasic_budget
    for card in lands:
        if len(land_picks) >= nonbasic_cap:
            break
        if card['name'].lower() in land_names:
            continue
        land_picks.append(card)
        land_names.add(card['name'].lower())

    # Guaranteed basics: at least one of each colour in the commander's
    # identity, then distribute any remaining land slots evenly across them.
    basics_needed = max(LAND_SLOTS - len(land_picks), len(required_basics))
    basic_owned = {}
    for basic_name in required_basics:
        row = db.execute('''
            SELECT SUM(c.count) as cnt FROM collection c
            JOIN cards k ON c.card_id = k.id
            WHERE LOWER(k.name) = LOWER(?)
        ''', [basic_name]).fetchone()
        basic_owned[basic_name] = int(row['cnt'] or 0) if row else 0

    per_basic = max(1, basics_needed // len(required_basics))
    remainder = basics_needed - per_basic * len(required_basics)
    for i, basic_name in enumerate(required_basics):
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

    land_total = sum(c['count'] for c in land_picks)
    stats = {
        'total_picked':    len(picked) + land_total,
        'non_land_picked': len(picked),
        'land_picked':     land_total,
        'from_edhrec':     sum(1 for c in picked if c.get('in_edhrec')),
        'owned_pool_size': len(non_lands) + len(lands),
        'slots_filled':    slots_filled,
        'combo_pieces':    len(combo_picks),
    }

    return {
        'picked':       picked,
        'land_picks':   land_picks,
        'slots_filled': slots_filled,
        'curve':        curve_report,
        'combos':       combo_summaries,
        'stats':        stats,
    }
