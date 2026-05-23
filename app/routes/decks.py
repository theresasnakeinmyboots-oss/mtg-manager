from flask import Blueprint, render_template, request, redirect, url_for, jsonify, abort
from app.database import get_db
from datetime import datetime, timezone

decks_bp = Blueprint('decks', __name__, url_prefix='/decks')

FORMAT_RULES = {
    'standard':  {'min_main': 60, 'max_main': None, 'max_side': 15, 'max_copies': 4,  'singleton': False, 'commander': False},
    'modern':    {'min_main': 60, 'max_main': None, 'max_side': 15, 'max_copies': 4,  'singleton': False, 'commander': False},
    'pioneer':   {'min_main': 60, 'max_main': None, 'max_side': 15, 'max_copies': 4,  'singleton': False, 'commander': False},
    'legacy':    {'min_main': 60, 'max_main': None, 'max_side': 15, 'max_copies': 4,  'singleton': False, 'commander': False},
    'commander': {'min_main': 99, 'max_main': 99,   'max_side': 0,  'max_copies': 1,  'singleton': True,  'commander': True},
}

BASIC_LANDS = {'Plains', 'Island', 'Swamp', 'Mountain', 'Forest',
               'Wastes', 'Snow-Covered Plains', 'Snow-Covered Island',
               'Snow-Covered Swamp', 'Snow-Covered Mountain', 'Snow-Covered Forest'}

FORMAT_LABELS = {
    'standard': 'Standard', 'modern': 'Modern', 'pioneer': 'Pioneer',
    'legacy': 'Legacy', 'commander': 'Commander / EDH',
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _validate_deck(cards, fmt):
    """Return list of validation error strings (empty = valid)."""
    rules = FORMAT_RULES.get(fmt)
    if not rules:
        return ['Unknown format']
    errors = []
    main = [c for c in cards if c['board'] == 'main']
    side = [c for c in cards if c['board'] == 'side']
    cmd  = [c for c in cards if c['board'] == 'commander']
    main_count = sum(c['count'] for c in main)
    side_count = sum(c['count'] for c in side)

    if rules['commander']:
        total = main_count + len(cmd)
        if total != 100:
            errors.append(f'Commander deck must have exactly 100 cards (currently {total})')
    else:
        if main_count < rules['min_main']:
            errors.append(f'Main deck needs at least {rules["min_main"]} cards (currently {main_count})')
        if rules['max_side'] is not None and side_count > rules['max_side']:
            errors.append(f'Sideboard can have at most {rules["max_side"]} cards (currently {side_count})')

    # Copy limit
    for c in main + side:
        if c['name'] not in BASIC_LANDS and c['count'] > rules['max_copies']:
            errors.append(f'{c["name"]}: max {rules["max_copies"]} copies (have {c["count"]})')

    return errors


def _deck_with_ownership(db, deck_id):
    """Fetch deck cards joined with ownership info from collection."""
    deck = db.execute('SELECT * FROM decks WHERE id = ?', [deck_id]).fetchone()
    if not deck:
        return None, None

    rows = db.execute('''
        SELECT
            dc.id, dc.scryfall_id, dc.name, dc.count, dc.board, dc.added_at,
            COALESCE((
                SELECT SUM(c2.count)
                FROM collection c2
                JOIN cards k2 ON c2.card_id = k2.id
                WHERE LOWER(k2.name) = LOWER(dc.name)
            ), 0) AS owned_count,
            b.image_uri_normal, b.type_line, b.mana_cost, b.rarity,
            b.colors, b.cmc, b.power, b.toughness, b.color_identity, b.oracle_text
        FROM deck_cards dc
        LEFT JOIN scryfall_bulk b ON dc.scryfall_id = b.scryfall_id
        WHERE dc.deck_id = ?
        ORDER BY dc.board, b.cmc, dc.name
    ''', [deck_id]).fetchall()

    return deck, rows


def _group_cards_by_type(rows):
    """Group a list of card rows into ordered type buckets."""
    order = ['Commander', 'Creature', 'Planeswalker', 'Instant', 'Sorcery',
             'Enchantment', 'Artifact', 'Land', 'Other']
    buckets = {t: [] for t in order}
    for row in rows:
        tl = row['type_line'] or ''
        placed = False
        for t in order[:-1]:
            if t in tl:
                buckets[t].append(row)
                placed = True
                break
        if not placed:
            buckets['Other'].append(row)
    return [(t, sorted(buckets[t], key=lambda r: r['name'].lower())) for t in order if buckets[t]]


# ── List all decks ────────────────────────────────────────────────────────────

@decks_bp.route('/', strict_slashes=False)
def index():
    import json as _json
    db = get_db()
    decks = db.execute('''
        SELECT d.*, COUNT(dc.id) AS card_types, COALESCE(SUM(dc.count), 0) AS total_cards
        FROM decks d
        LEFT JOIN deck_cards dc ON dc.deck_id = d.id
        GROUP BY d.id
        ORDER BY d.created_at DESC
    ''').fetchall()

    # Compute colour identity per deck (W/U/B/R/G in WUBRG order)
    _COLOR_ORDER = ['W', 'U', 'B', 'R', 'G']
    deck_colors = {}
    for deck in decks:
        rows = db.execute('''
            SELECT DISTINCT b.color_identity
            FROM deck_cards dc
            JOIN scryfall_bulk b ON dc.scryfall_id = b.scryfall_id
            WHERE dc.deck_id = ? AND b.color_identity IS NOT NULL AND b.color_identity != '[]'
        ''', [deck['id']]).fetchall()
        colors = set()
        for r in rows:
            try:
                colors.update(_json.loads(r['color_identity']))
            except Exception:
                pass
        deck_colors[deck['id']] = [c for c in _COLOR_ORDER if c in colors]

    db.close()
    return render_template('decks/index.html', decks=decks, format_labels=FORMAT_LABELS,
                           deck_colors=deck_colors)


# ── New deck (GET = form, POST = create) ─────────────────────────────────────

@decks_bp.route('/new', methods=['GET', 'POST'])
def new():
    if request.method == 'POST':
        name   = request.form.get('name', '').strip()
        fmt    = request.form.get('format', '').strip()
        desc   = request.form.get('description', '').strip()
        if not name or fmt not in FORMAT_RULES:
            return render_template('decks/new.html',
                                   formats=FORMAT_LABELS,
                                   error='Name and a valid format are required.',
                                   form=request.form)
        db = get_db()
        cur = db.execute(
            'INSERT INTO decks (name, format, description, created_at) VALUES (?,?,?,?)',
            [name, fmt, desc, _now()]
        )
        deck_id = cur.lastrowid
        db.commit()
        db.close()
        return redirect(url_for('decks.detail', deck_id=deck_id))
    return render_template('decks/new.html', formats=FORMAT_LABELS, error=None, form={})


# ── Import a deck list ────────────────────────────────────────────────────────

@decks_bp.route('/import', methods=['GET', 'POST'])
def import_deck():
    from app.deck_importer import parse_deck_list, resolve_cards
    if request.method == 'POST':
        name   = request.form.get('name', '').strip()
        fmt    = request.form.get('format', '').strip()
        desc   = request.form.get('description', '').strip()
        text   = request.form.get('decklist', '').strip()
        if not name or fmt not in FORMAT_RULES or not text:
            return render_template('decks/import.html',
                                   formats=FORMAT_LABELS,
                                   error='Name, format, and deck list are required.',
                                   form=request.form)
        db = get_db()
        parsed = parse_deck_list(text)
        entries, warnings = resolve_cards(db, parsed)

        cur = db.execute(
            'INSERT INTO decks (name, format, description, created_at) VALUES (?,?,?,?)',
            [name, fmt, desc, _now()]
        )
        deck_id = cur.lastrowid
        for entry in entries:
            db.execute(
                'INSERT INTO deck_cards (deck_id, scryfall_id, name, count, board, added_at) VALUES (?,?,?,?,?,?)',
                [deck_id, entry['scryfall_id'], entry['name'], entry['count'], entry['board'], _now()]
            )
        db.commit()
        db.close()
        return redirect(url_for('decks.detail', deck_id=deck_id,
                                _anchor='warnings' if warnings else ''))
    return render_template('decks/import.html', formats=FORMAT_LABELS, error=None, form={})


# ── Import a Delver Lens .dlens file as a deck ────────────────────────────────

@decks_bp.route('/import-dlens', methods=['GET', 'POST'])
def import_dlens_deck():
    import config, tempfile, os

    datadb_path = config.DATA_DIR / 'data.db'
    has_datadb  = datadb_path.exists()

    if request.method == 'POST':
        name  = request.form.get('name', '').strip()
        fmt   = request.form.get('format', '').strip()
        desc  = request.form.get('description', '').strip()
        board = request.form.get('board', 'main')
        if board not in ('main', 'side', 'commander'):
            board = 'main'
        f = request.files.get('file')

        if not name or fmt not in FORMAT_RULES:
            return render_template('decks/import_dlens.html', formats=FORMAT_LABELS,
                                   has_datadb=has_datadb, error='Name and format are required.', form=request.form)
        if not f or not f.filename.lower().endswith('.dlens'):
            return render_template('decks/import_dlens.html', formats=FORMAT_LABELS,
                                   has_datadb=has_datadb, error='Please upload a .dlens file.', form=request.form)
        if not has_datadb:
            return render_template('decks/import_dlens.html', formats=FORMAT_LABELS,
                                   has_datadb=False, error='data.db not found — upload it via the Admin page first.', form=request.form)

        # Save upload to a temp file, resolve cards, insert into deck_cards
        tmp = tempfile.NamedTemporaryFile(suffix='.dlens', delete=False)
        try:
            f.save(tmp.name)
            tmp.close()
            db = get_db()
            entries, warnings = _resolve_dlens_for_deck(tmp.name, str(datadb_path), db)
            cur = db.execute(
                'INSERT INTO decks (name, format, description, created_at) VALUES (?,?,?,?)',
                [name, fmt, desc, _now()]
            )
            deck_id = cur.lastrowid
            for entry in entries:
                db.execute(
                    'INSERT INTO deck_cards (deck_id, scryfall_id, name, count, board, added_at) VALUES (?,?,?,?,?,?)',
                    [deck_id, entry['scryfall_id'], entry['name'], entry['count'], board, _now()]
                )
            db.commit()
            db.close()
        finally:
            os.unlink(tmp.name)

        return redirect(url_for('decks.detail', deck_id=deck_id))

    return render_template('decks/import_dlens.html', formats=FORMAT_LABELS,
                           has_datadb=has_datadb, error=None, form={})


def _resolve_dlens_for_deck(dlens_path, datadb_path, db):
    """Read a .dlens file and resolve each card to a scryfall_id + name + count for deck use."""
    import sqlite3 as _sqlite3
    dlens_conn = _sqlite3.connect(dlens_path)
    dlens_conn.row_factory = _sqlite3.Row
    dlens_c = dlens_conn.cursor()

    dlens_c.execute('PRAGMA table_info(cards)')
    dlens_cols = [r[1] for r in dlens_c.fetchall()]
    dlens_c.execute('SELECT * FROM cards')
    dlens_cards = dlens_c.fetchall()
    dlens_conn.close()

    apk_conn = _sqlite3.connect(datadb_path)
    apk_conn.row_factory = _sqlite3.Row
    apk_c = apk_conn.cursor()
    apk_c.execute('PRAGMA table_info(cards)')
    apk_cols = [r[1] for r in apk_c.fetchall()]

    card_ref_col = 'card' if 'card' in dlens_cols else dlens_cols[1]
    qty_col      = next((c for c in dlens_cols if c.lower() in ('quantity', 'count', 'qty')), None)
    apk_id_col   = '_id' if '_id' in apk_cols else apk_cols[0]
    apk_sf_col   = next((c for c in apk_cols if 'scryfall' in c.lower()), None)

    entries  = []
    warnings = []

    for row in dlens_cards:
        internal_id = row[card_ref_col]
        count       = int(row[qty_col]) if qty_col else 1

        apk_c.execute(f'SELECT {apk_sf_col} FROM cards WHERE {apk_id_col} = ?', (internal_id,))
        apk_row = apk_c.fetchone()
        if not apk_row:
            warnings.append(f'Card internal_id {internal_id} not found in data.db')
            continue

        scryfall_id = apk_row[apk_sf_col]
        bulk = db.execute(
            'SELECT name FROM scryfall_bulk WHERE scryfall_id = ? LIMIT 1', [scryfall_id]
        ).fetchone()
        if not bulk:
            warnings.append(f'scryfall_id {scryfall_id} not in bulk data')
            continue

        entries.append({'scryfall_id': scryfall_id, 'name': bulk['name'], 'count': count})

    apk_conn.close()
    return entries, warnings


# ── Deck detail ───────────────────────────────────────────────────────────────

@decks_bp.route('/<int:deck_id>')
def detail(deck_id):
    db = get_db()
    deck, rows = _deck_with_ownership(db, deck_id)
    db.close()
    if not deck:
        abort(404)

    main_rows = [r for r in rows if r['board'] == 'main']
    side_rows = [r for r in rows if r['board'] == 'side']
    cmd_rows  = [r for r in rows if r['board'] == 'commander']

    # If everything landed in the commander board (bad import), treat them as mainboard
    # so type grouping and charts still work. Surface a warning instead of a blank page.
    all_in_commander = bool(cmd_rows and not main_rows and not side_rows)
    if all_in_commander:
        main_rows = cmd_rows
        cmd_rows  = []

    main_groups = _group_cards_by_type(main_rows)
    side_groups = _group_cards_by_type(side_rows)

    main_count  = sum(r['count'] for r in main_rows)
    side_count  = sum(r['count'] for r in side_rows)
    total_count = main_count + side_count + len(cmd_rows)
    owned_count = sum(min(r['count'], r['owned_count']) for r in rows)

    all_cards_for_validation = [dict(r) for r in rows]
    errors = _validate_deck(all_cards_for_validation, deck['format'])
    if all_in_commander:
        errors = ['All cards are on the Commander board — use "Move all to mainboard" to fix this import.'] + errors

    # ── Chart data (mainboard only, excluding lands) ──────────────────
    import json as _json

    # Mana curve: cmc 0–6+ bucketed, lands excluded
    curve = [0] * 7
    for r in main_rows:
        tl = r['type_line'] or ''
        if 'Land' in tl:
            continue
        cmc = int(r['cmc'] or 0)
        bucket = min(cmc, 6)
        curve[bucket] += r['count']
    chart_curve = _json.dumps({
        'labels': ['0', '1', '2', '3', '4', '5', '6+'],
        'data':   curve,
    })

    # Colour distribution: W/U/B/R/G/C counted by card copies
    import json as _json2
    color_counts = {'W': 0, 'U': 0, 'B': 0, 'R': 0, 'G': 0, 'C': 0}
    for r in main_rows + cmd_rows:
        tl = r['type_line'] or ''
        if 'Land' in tl:
            continue
        try:
            colors = _json2.loads(r['colors'] or '[]')
        except Exception:
            colors = []
        if not colors:
            color_counts['C'] += r['count']
        else:
            for c in colors:
                if c in color_counts:
                    color_counts[c] += r['count']
    chart_colors = _json.dumps({
        'labels': ['White', 'Blue', 'Black', 'Red', 'Green', 'Colorless'],
        'data':   [color_counts['W'], color_counts['U'], color_counts['B'],
                   color_counts['R'], color_counts['G'], color_counts['C']],
    })

    # Mana sources: count sources of each colour from lands/rocks/dorks
    # Parses oracle_text for "add {X}" patterns and basic land subtypes
    import re as _re
    mana_src = {'W': 0, 'U': 0, 'B': 0, 'R': 0, 'G': 0, 'C': 0, 'ANY': 0}
    mana_src_ids = {'W': [], 'U': [], 'B': [], 'R': [], 'G': [], 'C': [], 'ANY': []}
    _add_re = _re.compile(r'[Aa]dd\s+((?:\{[WUBRGCSXYZ0-9/]+\}[\s,]*(?:or\s+)?)+)', _re.IGNORECASE)
    _sym_re = _re.compile(r'\{([WUBRG])\}')
    _any_re = _re.compile(r'[Aa]dd\s+(?:one\s+mana\s+of\s+any\s+(?:one\s+)?(?:color|colour)|mana\s+of\s+any\s+(?:color|colour)|\{C\}.*?of\s+any\s+(?:color|colour))', _re.IGNORECASE)
    _basic_subtypes = {'Plains': 'W', 'Island': 'U', 'Swamp': 'B', 'Mountain': 'R', 'Forest': 'G'}

    for r in main_rows + cmd_rows:
        tl = r['type_line'] or ''
        oracle = r['oracle_text'] or ''
        count = r['count']
        sid = r['scryfall_id']

        # Basic land subtypes on type line (e.g. "Basic Land — Forest")
        subtype_colors = set()
        for subtype, color in _basic_subtypes.items():
            if subtype in tl:
                subtype_colors.add(color)
        if subtype_colors:
            for color in subtype_colors:
                mana_src[color] += count
                if sid not in mana_src_ids[color]:
                    mana_src_ids[color].append(sid)
            continue  # don't also parse oracle for basics

        # Parse oracle text for mana production
        if oracle:
            added = set()
            for match in _add_re.finditer(oracle):
                syms = _sym_re.findall(match.group(1))
                for s in syms:
                    added.add(s.upper())
            if added:
                for color in added:
                    if color in mana_src:
                        mana_src[color] += count
                        if sid not in mana_src_ids[color]:
                            mana_src_ids[color].append(sid)
                continue
            # "add one mana of any color" — bucket as Any rather than splitting
            if _any_re.search(oracle):
                mana_src['ANY'] += count
                if sid not in mana_src_ids['ANY']:
                    mana_src_ids['ANY'].append(sid)
                continue

    # Build chart data; Any Color goes at the end
    src_labels_all = ['White', 'Blue', 'Black', 'Red', 'Green', 'Colorless', 'Any Color']
    src_keys_all   = ['W',     'U',    'B',     'R',   'G',     'C',         'ANY']
    src_pairs = [(l, mana_src[k]) for l, k in zip(src_labels_all, src_keys_all) if mana_src[k] > 0]
    chart_mana_src = _json.dumps({
        'labels': [p[0] for p in src_pairs],
        'data':   [p[1] for p in src_pairs],
    })
    # Map colour name → list of scryfall_ids for JS filtering
    color_key_map = {'White':'W','Blue':'U','Black':'B','Red':'R','Green':'G','Colorless':'C','Any Color':'ANY'}
    mana_src_ids_by_name = {name: mana_src_ids[k] for name, k in color_key_map.items()}
    chart_mana_src_ids = _json.dumps(mana_src_ids_by_name)

    # Type breakdown: count by broad type (mainboard only)
    type_order = ['Creature', 'Instant', 'Sorcery', 'Enchantment', 'Artifact', 'Planeswalker', 'Land', 'Other']
    type_counts = {t: 0 for t in type_order}
    for r in main_rows:
        tl = r['type_line'] or ''
        placed = False
        for t in type_order[:-1]:
            if t in tl:
                type_counts[t] += r['count']
                placed = True
                break
        if not placed:
            type_counts['Other'] += r['count']
    # Drop zero-count types
    type_labels = [t for t in type_order if type_counts[t] > 0]
    type_data   = [type_counts[t] for t in type_labels]
    chart_types = _json.dumps({'labels': type_labels, 'data': type_data})

    # ── Opening hand probabilities (hypergeometric) ───────────────────
    from math import comb

    def hyper_at_least_one(N, K, n):
        """P(at least 1 hit) drawing n from N cards with K hits."""
        if N == 0 or K == 0:
            return 0.0
        if K >= N:
            return 1.0
        p_none = comb(N - K, n) / comb(N, n) if comb(N, n) > 0 else 1.0
        return 1.0 - p_none

    def hyper_range(N, K, n, lo, hi):
        """P(lo <= hits <= hi) drawing n from N with K hits."""
        if N == 0 or comb(N, n) == 0:
            return 0.0
        total = comb(N, n)
        prob = 0.0
        for k in range(lo, min(hi, K, n) + 1):
            if N - K >= n - k and n - k >= 0:
                prob += comb(K, k) * comb(N - K, n - k)
        return prob / total

    deck_size   = sum(r['count'] for r in main_rows + cmd_rows)
    land_count  = sum(r['count'] for r in main_rows + cmd_rows if 'Land' in (r['type_line'] or ''))
    cmc1_count  = sum(r['count'] for r in main_rows + cmd_rows
                      if 'Land' not in (r['type_line'] or '') and int(r['cmc'] or 0) <= 1 and int(r['cmc'] or 0) >= 0)
    cmc2_count  = sum(r['count'] for r in main_rows + cmd_rows
                      if 'Land' not in (r['type_line'] or '') and int(r['cmc'] or 0) <= 2)

    N, n = deck_size, 7
    draw_probs = []
    if N >= n:
        p_land      = hyper_at_least_one(N, land_count, n)
        p_2_5_lands = hyper_range(N, land_count, n, 2, 5)
        p_t1        = 1.0 - (
            comb(N - land_count, n) * comb(N - cmc1_count, 0) / comb(N, n)
            if False else  # use joint calc below
            0
        )
        # P(≥1 land AND ≥1 CMC≤1 spell) via inclusion-exclusion
        no_land  = comb(N - land_count, n) / comb(N, n) if comb(N, n) else 1
        no_spell = comb(N - cmc1_count, n) / comb(N, n) if comb(N, n) else 1
        both_missing = (comb(max(N - land_count - cmc1_count, 0), n) / comb(N, n)
                        if N - land_count - cmc1_count >= 0 else 0)
        p_t1 = 1.0 - no_land - no_spell + both_missing

        no_spell2 = comb(N - cmc2_count, n) / comb(N, n) if comb(N, n) else 1
        both_missing2 = (comb(max(N - land_count - cmc2_count, 0), n) / comb(N, n)
                         if N - land_count - cmc2_count >= 0 else 0)
        p_t2 = 1.0 - no_land - no_spell2 + both_missing2

        draw_probs = [
            ('≥1 land',            p_land),
            ('2–5 lands',          p_2_5_lands),
            ('Turn 1 play',        max(p_t1, 0.0)),
            ('Turn 2 play',        max(p_t2, 0.0)),
        ]

    # ── Deck health checks ────────────────────────────────────────────
    import re as _re2
    is_commander = deck['format'] == 'commander'

    def count_oracle(pattern, rows):
        rx = _re2.compile(pattern, _re2.IGNORECASE)
        return sum(r['count'] for r in rows if rx.search(r['oracle_text'] or ''))

    all_rows = main_rows + cmd_rows

    removal_count = count_oracle(
        r'destroy target|exile target|deals \d+ damage to (any target|target creature|each creature)|'
        r'-\d+/-\d+|return target .* to (its owner|their owner)\'s hand|'
        r'counter target (spell|creature|artifact|enchantment)',
        all_rows
    )
    draw_count = count_oracle(
        r'draw (a|two|three|\d+) card|investigate|whenever .* draw|'
        r'look at the top \d+ card|scry \d',
        all_rows
    )
    ramp_count = count_oracle(
        r'search your library for (a|up to \d+) (basic )?land|'
        r'add \{[WUBRGC]\}|add (one|two|\d+) mana|'
        r'add mana of any|untap target land',
        all_rows
    ) + sum(r['count'] for r in all_rows
            if 'Land' not in (r['type_line'] or '')
            and ('Artifact' in (r['type_line'] or '') or 'Creature' in (r['type_line'] or ''))
            and _re2.search(r'add \{', r['oracle_text'] or '', _re2.IGNORECASE))
    protection_count = count_oracle(
        r'hexproof|indestructible|regenerate|shroud|ward|'
        r'counter target spell|can\'t be countered',
        all_rows
    )
    wincon_count = count_oracle(
        r'players (lose|win) the game|you win the game|'
        r'deal (infinite|combat) damage|'
        r'infinite|combo|'
        r'\binfinite\b',
        all_rows
    ) + sum(r['count'] for r in all_rows
            if 'Land' not in (r['type_line'] or '')
            and int(r['cmc'] or 0) >= 6
            and (int(r['power'] or 0) >= 5 if r['power'] and r['power'].lstrip('-').isdigit() else False))

    # Thresholds: (label, count, (ok_min, warn_min), tip)
    if is_commander:
        health_checks = [
            ('Removal',    removal_count,    (8, 4),  'Aim for 8–12 in Commander'),
            ('Card draw',  draw_count,       (8, 4),  'Aim for 8–12 in Commander'),
            ('Ramp',       ramp_count,       (10, 5), 'Aim for 10+ in Commander'),
            ('Protection', protection_count, (4, 2),  'Hexproof, indestructible, counterspells'),
            ('Win cons',   wincon_count,     (3, 1),  'Big threats or game-winning combos'),
        ]
    else:
        health_checks = [
            ('Removal',    removal_count,    (6, 3),  'Aim for 6–10 in 60-card decks'),
            ('Card draw',  draw_count,       (6, 3),  'Aim for 6–10 in 60-card decks'),
            ('Ramp',       ramp_count,       (4, 2),  'Aim for 4+ in 60-card decks'),
            ('Protection', protection_count, (3, 1),  'Hexproof, indestructible, counterspells'),
            ('Win cons',   wincon_count,     (4, 2),  'Your main threats or combo pieces'),
        ]

    # tag each as ok / warn / low
    def health_tag(count, ok_min, warn_min):
        if count >= ok_min:   return 'ok'
        if count >= warn_min: return 'warn'
        return 'low'

    deck_health = [
        (label, count, health_tag(count, ok, warn), tip)
        for label, count, (ok, warn), tip in health_checks
    ]

    return render_template('decks/detail.html',
                           deck=deck,
                           cmd_rows=cmd_rows,
                           main_groups=main_groups,
                           side_groups=side_groups,
                           main_count=main_count,
                           side_count=side_count,
                           total_count=total_count,
                           owned_count=owned_count,
                           errors=errors,
                           format_label=FORMAT_LABELS.get(deck['format'], deck['format']),
                           chart_curve=chart_curve,
                           chart_colors=chart_colors,
                           chart_types=chart_types,
                           chart_mana_src=chart_mana_src,
                           chart_mana_src_ids=chart_mana_src_ids,
                           draw_probs=draw_probs,
                           land_count=land_count,
                           deck_size=deck_size,
                           deck_health=deck_health)


# ── Add a card (JSON API) ─────────────────────────────────────────────────────

@decks_bp.route('/<int:deck_id>/add', methods=['POST'])
def add_card(deck_id):
    db = get_db()
    if not db.execute('SELECT 1 FROM decks WHERE id=?', [deck_id]).fetchone():
        db.close()
        return jsonify(error='Deck not found'), 404

    data        = request.get_json(force=True)
    scryfall_id = data.get('scryfall_id', '').strip()
    board       = data.get('board', 'main')
    count       = int(data.get('count', 1))
    if board not in ('main', 'side', 'commander'):
        board = 'main'

    # Resolve name from bulk data
    card = db.execute(
        'SELECT name FROM scryfall_bulk WHERE scryfall_id=? LIMIT 1', [scryfall_id]
    ).fetchone()
    if not card:
        # Fall back to cards table
        card = db.execute(
            'SELECT name FROM cards WHERE scryfall_id=? LIMIT 1', [scryfall_id]
        ).fetchone()
    if not card:
        db.close()
        return jsonify(error='Card not found'), 404

    name = card['name']

    existing = db.execute(
        'SELECT id, count FROM deck_cards WHERE deck_id=? AND scryfall_id=? AND board=?',
        [deck_id, scryfall_id, board]
    ).fetchone()

    if existing:
        db.execute('UPDATE deck_cards SET count=count+? WHERE id=?', [count, existing['id']])
    else:
        db.execute(
            'INSERT INTO deck_cards (deck_id, scryfall_id, name, count, board, added_at) VALUES (?,?,?,?,?,?)',
            [deck_id, scryfall_id, name, count, board, _now()]
        )
    db.commit()

    new_count = db.execute(
        'SELECT count FROM deck_cards WHERE deck_id=? AND scryfall_id=? AND board=?',
        [deck_id, scryfall_id, board]
    ).fetchone()['count']
    db.close()
    return jsonify(ok=True, name=name, count=new_count)


# ── Card search (autocomplete) ────────────────────────────────────────────────

@decks_bp.route('/<int:deck_id>/search-cards')
def search_cards(deck_id):
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    db = get_db()
    rows = db.execute(
        '''SELECT name, scryfall_id, mana_cost, type_line, set_name
           FROM scryfall_bulk
           WHERE name LIKE ?
           ORDER BY name, set_name LIMIT 40''',
        [f'%{q}%']
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


# ── Update card count ─────────────────────────────────────────────────────────

@decks_bp.route('/<int:deck_id>/set-count', methods=['POST'])
def set_count(deck_id):
    db = get_db()
    data   = request.get_json(force=True)
    row_id = int(data.get('id', 0))
    count  = int(data.get('count', 1))
    if count < 1:
        db.execute('DELETE FROM deck_cards WHERE id=? AND deck_id=?', [row_id, deck_id])
    else:
        db.execute('UPDATE deck_cards SET count=? WHERE id=? AND deck_id=?', [count, row_id, deck_id])
    db.commit()
    db.close()
    return jsonify(ok=True)


# ── Remove a card ─────────────────────────────────────────────────────────────

@decks_bp.route('/<int:deck_id>/remove', methods=['POST'])
def remove_card(deck_id):
    db = get_db()
    data   = request.get_json(force=True)
    row_id = int(data.get('id', 0))
    db.execute('DELETE FROM deck_cards WHERE id=? AND deck_id=?', [row_id, deck_id])
    db.commit()
    db.close()
    return jsonify(ok=True)


# ── Move all commander-board cards to mainboard ───────────────────────────────

@decks_bp.route('/<int:deck_id>/reboard-to-main', methods=['POST'])
def reboard_to_main(deck_id):
    db = get_db()
    db.execute("UPDATE deck_cards SET board='main' WHERE deck_id=? AND board='commander'", [deck_id])
    db.commit()
    db.close()
    return redirect(url_for('decks.detail', deck_id=deck_id))


# ── Delete deck ───────────────────────────────────────────────────────────────

@decks_bp.route('/<int:deck_id>/delete', methods=['POST'])
def delete_deck(deck_id):
    db = get_db()
    db.execute('DELETE FROM decks WHERE id=?', [deck_id])
    db.commit()
    db.close()
    return redirect(url_for('decks.index'))


# ── List decks as JSON (for context menu) ────────────────────────────────────

@decks_bp.route('/api/list')
def api_list():
    db = get_db()
    rows = db.execute('SELECT id, name, format FROM decks ORDER BY name').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])
