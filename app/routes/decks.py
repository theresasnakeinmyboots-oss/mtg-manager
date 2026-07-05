from flask import Blueprint, render_template, request, redirect, url_for, jsonify, abort
from app.database import get_db
from app.cards_repo import get_or_create_card
from app.mtg_constants import BASIC_LANDS, BASIC_LANDS_LOWER
from app.strategy import role_tags, health_score
from datetime import datetime, timezone

decks_bp = Blueprint('decks', __name__, url_prefix='/decks')

FORMAT_RULES = {
    'standard':  {'min_main': 60, 'max_main': None, 'max_side': 15, 'max_copies': 4,  'singleton': False, 'commander': False},
    'modern':    {'min_main': 60, 'max_main': None, 'max_side': 15, 'max_copies': 4,  'singleton': False, 'commander': False},
    'pioneer':   {'min_main': 60, 'max_main': None, 'max_side': 15, 'max_copies': 4,  'singleton': False, 'commander': False},
    'legacy':    {'min_main': 60, 'max_main': None, 'max_side': 15, 'max_copies': 4,  'singleton': False, 'commander': False},
    'commander': {'min_main': 99, 'max_main': 99,   'max_side': 0,  'max_copies': 1,  'singleton': True,  'commander': True},
    'sealed':    {'min_main': 40, 'max_main': None, 'max_side': None, 'max_copies': None, 'singleton': False, 'commander': False},
}

FORMAT_LABELS = {
    'standard': 'Standard', 'modern': 'Modern', 'pioneer': 'Pioneer',
    'legacy': 'Legacy', 'commander': 'Commander / EDH', 'sealed': 'Sealed',
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def resolve_commander(db, deck_id):
    """Return the deck's commander row (name, scryfall_id) or None.

    Prefers whatever is on the commander board; if that's empty, falls back to
    the highest-CMC Legendary Creature in the deck (alphabetical tiebreak).
    """
    cmd = db.execute(
        "SELECT dc.name, dc.scryfall_id FROM deck_cards dc "
        "WHERE dc.deck_id=? AND dc.board='commander' LIMIT 1", [deck_id]
    ).fetchone()
    if cmd:
        return cmd
    return db.execute(
        "SELECT dc.name, dc.scryfall_id FROM deck_cards dc "
        "LEFT JOIN scryfall_bulk b ON dc.scryfall_id = b.scryfall_id "
        "WHERE dc.deck_id=? AND b.type_line LIKE '%Legendary%Creature%' "
        "ORDER BY b.cmc DESC, dc.name LIMIT 1", [deck_id]
    ).fetchone()


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

    # Copy limit (sealed has no copy limit)
    if rules['max_copies'] is not None:
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
            (
                -- Only link to a collection row when it's the exact printing the deck
                -- specifies. A different owned printing of the same card does NOT count —
                -- that's a mismatch to flag during reconciliation, not a thing to link past.
                -- Join through cards.scryfall_id too: collection.scryfall_id is a denormalized
                -- copy that can lag behind (e.g. a stale import), so don't trust it alone.
                SELECT MIN(c3.id) FROM collection c3
                LEFT JOIN cards k3 ON c3.card_id = k3.id
                WHERE c3.scryfall_id = dc.scryfall_id OR k3.scryfall_id = dc.scryfall_id
            ) AS collection_row_id,
            b.image_uri_normal, b.type_line, b.mana_cost, b.rarity,
            b.colors, b.cmc, b.power, b.toughness, b.color_identity, b.oracle_text,
            b.set_name, b.set_code, b.collector_number
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
    db = get_db()
    collections = db.execute(
        "SELECT id, name FROM collections WHERE is_staging=0 ORDER BY name"
    ).fetchall()
    if request.method == 'POST':
        name   = request.form.get('name', '').strip()
        fmt    = request.form.get('format', '').strip()
        desc   = request.form.get('description', '').strip()
        sealed_collection_id = request.form.get('sealed_collection_id', '').strip() or None
        if not name or fmt not in FORMAT_RULES:
            db.close()
            return render_template('decks/new.html',
                                   formats=FORMAT_LABELS,
                                   collections=collections,
                                   error='Name and a valid format are required.',
                                   form=request.form)
        cur = db.execute(
            'INSERT INTO decks (name, format, description, created_at) VALUES (?,?,?,?)',
            [name, fmt, desc, _now()]
        )
        deck_id = cur.lastrowid

        if fmt == 'sealed' and sealed_collection_id:
            coll_cards = db.execute(
                '''SELECT col.scryfall_id, col.name, SUM(col.count) as total_count
                   FROM collection col
                   WHERE col.collection_id = ?
                   GROUP BY col.scryfall_id, col.name''',
                [sealed_collection_id]
            ).fetchall()
            for card in coll_cards:
                sid = card['scryfall_id']
                cname = card['name']
                cnt = card['total_count'] or 1
                if not sid:
                    continue
                if not cname:
                    row = db.execute('SELECT name FROM scryfall_bulk WHERE scryfall_id=?', [sid]).fetchone()
                    cname = row['name'] if row else sid
                db.execute(
                    'INSERT INTO deck_cards (deck_id, scryfall_id, name, count, board, added_at) VALUES (?,?,?,?,?,?)',
                    [deck_id, sid, cname, cnt, 'main', _now()]
                )

        db.commit()
        db.close()
        return redirect(url_for('decks.detail', deck_id=deck_id))
    db.close()
    return render_template('decks/new.html', formats=FORMAT_LABELS, collections=collections, error=None, form={})


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

    if not deck:
        db.close()
        abort(404)

    main_rows = [r for r in rows if r['board'] == 'main']
    side_rows = [r for r in rows if r['board'] == 'side']
    cmd_rows  = [r for r in rows if r['board'] == 'commander']

    printing_mismatch_count = sum(
        1 for r in rows if r['owned_count'] > 0 and not r['collection_row_id']
    )

    # If everything landed in the commander board (bad import), treat them as mainboard
    # so type grouping and charts still work. Surface a warning instead of a blank page.
    all_in_commander = bool(cmd_rows and not main_rows and not side_rows)
    if all_in_commander:
        main_rows = cmd_rows
        cmd_rows  = []

    # For commander format: if no card is on the commander board yet, auto-promote
    # the best guess (highest CMC Legendary Creature, tiebreak alphabetically).
    if deck['format'] == 'commander' and not cmd_rows and not all_in_commander:
        eligible = [
            r for r in main_rows
            if 'Legendary' in (r['type_line'] or '') and 'Creature' in (r['type_line'] or '')
        ]
        if eligible:
            best = max(eligible, key=lambda r: (int(r['cmc'] or 0), r['name']))
            db.execute(
                "UPDATE deck_cards SET board='commander' WHERE id=?", [best['id']]
            )
            db.commit()
            # Reload rows after promotion
            _, rows = _deck_with_ownership(db, deck_id)
            main_rows = [r for r in rows if r['board'] == 'main']
            side_rows = [r for r in rows if r['board'] == 'side']
            cmd_rows  = [r for r in rows if r['board'] == 'commander']

    db.close()

    # Eligible commanders: Legendary Creatures anywhere in the deck (main or commander board)
    all_main = main_rows + cmd_rows
    eligible_commanders = [
        r for r in all_main
        if 'Legendary' in (r['type_line'] or '') and 'Creature' in (r['type_line'] or '')
    ]
    eligible_commanders.sort(key=lambda r: r['name'])

    # Active commander is whichever card is on the commander board
    active_commander = cmd_rows[0] if cmd_rows else (eligible_commanders[0] if eligible_commanders else None)

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
    from app.strategy import (STRATEGIES, DEFAULT_STRATEGY,
                               compute_health, compute_health_sids, detect_strategy)

    all_rows = [dict(r) for r in main_rows + cmd_rows]
    non_land_rows = [r for r in all_rows if 'Land' not in (r['type_line'] or '')]

    strategy_key = deck['strategy'] or DEFAULT_STRATEGY
    deck_health  = compute_health(all_rows, strategy_key, deck['format'] == 'commander', deck_format=deck['format'])
    health_sids  = compute_health_sids(all_rows, strategy_key, deck_format=deck['format'])
    chart_health_sids = _json.dumps(health_sids)

    # ── Cut candidate scryfall IDs (for deck-view badges) ────────────────
    # Only computed for commander decks with cached EDHREC data — no live fetch.
    db = get_db()

    # Load muted health checks for this deck
    muted_checks = {r['check_name'] for r in
                    db.execute('SELECT check_name FROM deck_health_mutes WHERE deck_id=?',
                               [deck_id]).fetchall()}

    # Load or run strategy detection
    last_detection = db.execute(
        'SELECT * FROM deck_strategy_feedback WHERE deck_id=? ORDER BY detected_at DESC LIMIT 1',
        [deck_id]
    ).fetchone()
    strategy_detection = dict(last_detection) if last_detection else None

    cut_sids = set()
    cut_candidates = []
    if deck['format'] == 'commander' and active_commander and cmd_rows:
        from app.edhrec import _name_to_slug as _edhrec_slug
        slug = _edhrec_slug(active_commander['name'])
        cache_row = db.execute(
            'SELECT 1 FROM edhrec_cache WHERE slug = ?', [slug]
        ).fetchone()
        if cache_row:
            edhrec_lookup = {}
            for ec in db.execute(
                'SELECT name, synergy FROM edhrec_cards WHERE slug = ?', [slug]
            ).fetchall():
                nl = ec['name'].lower()
                if nl not in edhrec_lookup or ec['synergy'] > edhrec_lookup[nl]:
                    edhrec_lookup[nl] = ec['synergy']

            cmd_name_lower = active_commander['name'].lower()
            for r in main_rows + cmd_rows:
                nl = r['name'].lower()
                if nl in BASIC_LANDS_LOWER or nl == cmd_name_lower:
                    continue
                if 'Land' in (r['type_line'] or '') and 'Creature' not in (r['type_line'] or ''):
                    continue
                raw_syn = edhrec_lookup.get(nl)
                syn_c = ((raw_syn + 1.0) / 2.0) if raw_syn is not None else 0.2
                health_c = health_score(r['oracle_text'], r['type_line'], r['cmc'], r['power'])
                keep_score = 0.6 * syn_c + 0.4 * health_c
                cut_candidates.append({
                    'scryfall_id':      r['scryfall_id'],
                    'name':             r['name'],
                    'type_line':        r['type_line'],
                    'image_uri_normal': r['image_uri_normal'],
                    'synergy':          raw_syn,
                    'health_score':     health_c,
                    'keep_score':       keep_score,
                })

            cut_candidates.sort(key=lambda c: c['keep_score'])
            cut_candidates = cut_candidates[:20]
            cut_sids = {c['scryfall_id'] for c in cut_candidates}

    # ── Suggestion cards for inline display (cached EDHREC only) ─────────
    # sugg_by_type: same type-order keys as _group_cards_by_type, each value
    # is a list of suggestion card dicts (not in deck, sorted by synergy desc).
    sugg_by_type = {}
    sugg_per_type_limit = 7
    if deck['format'] == 'commander' and active_commander:
        from app.edhrec import _name_to_slug as _slug2
        _slug2_val = _slug2(active_commander['name'])
        if db.execute('SELECT 1 FROM edhrec_cache WHERE slug=?', [_slug2_val]).fetchone():
            deck_names_lower = {r['name'].lower() for r in rows}
            owned_names_lower2 = set(
                r['name'].lower() for r in
                db.execute('SELECT DISTINCT LOWER(k.name) as name FROM collection c JOIN cards k ON c.card_id=k.id').fetchall()
            )
            sugg_rows2 = db.execute('''
                SELECT ec.name, ec.scryfall_id, ec.synergy, ec.inclusion, ec.potential_decks,
                       b.type_line, b.mana_cost, b.image_uri_normal, b.colors, b.price_usd,
                       b.oracle_text, b.cmc, b.power
                FROM edhrec_cards ec
                LEFT JOIN scryfall_bulk b ON ec.scryfall_id = b.scryfall_id
                WHERE ec.slug = ?
                ORDER BY ec.synergy DESC, ec.inclusion DESC
            ''', [_slug2_val]).fetchall()

            import re as _srx
            # Core role patterns are shared (strategy.ROLE_PATTERNS); these are the
            # extra suggestion-only tags layered on top.
            _sugg_extra_tags = [
                ('Tokens',    _srx.compile(r'create[s]? \w+ [\w\s]+ token|put[s]? \w+ [\w\s]+ token|populate', _srx.I)),
                ('Tutor',     _srx.compile(r'search your library for .* card', _srx.I)),
                ('Life gain', _srx.compile(r'you gain \d+ life|gains? \d+ life|lifelink', _srx.I)),
            ]

            def _sugg_role_tags(oracle, type_line, cmc, power):
                if not oracle:
                    return []
                # Core tags (Ramp/Removal/Card draw/Protection/Win con + Threat)
                tags = role_tags(oracle, type_line, cmc, power, limit=None)
                # Layer in suggestion-only extras
                tags += [label for label, rx in _sugg_extra_tags if rx.search(oracle)]
                return tags[:3]  # cap at 3 to keep the row tidy

            _type_order = ['Commander', 'Creature', 'Planeswalker', 'Instant', 'Sorcery',
                           'Enchantment', 'Artifact', 'Land', 'Other']
            _sugg_buckets = {t: [] for t in _type_order}
            _seen_sugg = set()
            for r in sugg_rows2:
                nl = r['name'].lower()
                if nl in deck_names_lower or nl in _seen_sugg:
                    continue
                _seen_sugg.add(nl)
                tl = r['type_line'] or ''
                bucket = 'Other'
                for t in _type_order[:-1]:
                    if t in tl:
                        bucket = t
                        break
                pct = round(100 * r['inclusion'] / r['potential_decks']) if r['potential_decks'] else 0
                _sugg_buckets[bucket].append({
                    'name': r['name'], 'scryfall_id': r['scryfall_id'],
                    'synergy': r['synergy'], 'pct': pct,
                    'type_line': tl, 'mana_cost': r['mana_cost'],
                    'image_uri_normal': r['image_uri_normal'],
                    'colors': r['colors'] or '[]',
                    'price_usd': r['price_usd'],
                    'owned': nl in owned_names_lower2,
                    'tags': _sugg_role_tags(r['oracle_text'], tl, r['cmc'], r['power']),
                })
            # Pass all suggestions uncapped — JS handles the per-type display cap
            from app.database import get_setting as _get_setting
            _sugg_limit = int(_get_setting(db, 'suggestions_per_type', 7))
            sugg_by_type = {t: cards for t, cards in _sugg_buckets.items() if cards}
            sugg_per_type_limit = _sugg_limit

    # ── Combo detection ───────────────────────────────────────────────────────
    combos_complete = []
    combos_near_miss = []
    if deck['format'] == 'commander' and active_commander:
        from app.edhrec import get_commander_combos
        deck_names = {r['name'].lower() for r in
                      db.execute('SELECT name FROM deck_cards WHERE deck_id=?', [deck_id]).fetchall()}
        owned_names = {r['name'].lower() for r in
                       db.execute('SELECT DISTINCT LOWER(k.name) as name FROM collection c JOIN cards k ON c.card_id=k.id').fetchall()}

        raw_combos = get_commander_combos(db, active_commander['name'])
        for combo in raw_combos:
            card_names = [c['name'] for c in combo['cards']]
            missing = [n for n in card_names if n.lower() not in deck_names]
            if not missing:
                combos_complete.append({**combo, 'missing': []})
            elif len(missing) <= 2:
                combos_near_miss.append({
                    **combo,
                    'missing': missing,
                    'missing_owned': [n for n in missing if n.lower() in owned_names],
                })

        # Sort: complete by popularity; near-miss by fewest missing then popularity
        combos_complete.sort(key=lambda c: -c['count'])
        combos_near_miss.sort(key=lambda c: (len(c['missing']), -c['count']))

        # Enrich all combo cards with image_uri_normal from scryfall_bulk
        all_sids = list({card['scryfall_id'] for combo in combos_complete + combos_near_miss
                         for card in combo['cards'] if card.get('scryfall_id')})
        if all_sids:
            placeholders = ','.join('?' * len(all_sids))
            img_rows = db.execute(
                f'SELECT scryfall_id, image_uri_normal FROM scryfall_bulk WHERE scryfall_id IN ({placeholders})',
                all_sids
            ).fetchall()
            img_map = {r['scryfall_id']: r['image_uri_normal'] for r in img_rows}
            for combo in combos_complete + combos_near_miss:
                for card in combo['cards']:
                    card['image_uri'] = img_map.get(card.get('scryfall_id', ''), '')

    db.close()

    return render_template('decks/detail.html',
                           deck=deck,
                           cmd_rows=cmd_rows,
                           main_groups=main_groups,
                           side_groups=side_groups,
                           main_count=main_count,
                           side_count=side_count,
                           total_count=total_count,
                           owned_count=owned_count,
                           printing_mismatch_count=printing_mismatch_count,
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
                           deck_health=deck_health,
                           chart_health_sids=chart_health_sids,
                           eligible_commanders=eligible_commanders,
                           active_commander=active_commander,
                           cut_sids=cut_sids,
                           cut_candidates=cut_candidates,
                           sugg_by_type=sugg_by_type,
                           sugg_per_type_limit=sugg_per_type_limit,
                           strategy_key=strategy_key,
                           strategies=STRATEGIES,
                           muted_checks=muted_checks,
                           strategy_detection=strategy_detection,
                           combos_complete=combos_complete,
                           combos_near_miss=combos_near_miss)


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


# ── Printing reconciliation ────────────────────────────────────────────────────
# When a deck card's exact printing isn't in the collection, but a different
# printing of the same name is, the user must decide per-card whether the deck
# list or the collection record is "wrong". There's no safe automatic answer.

@decks_bp.route('/<int:deck_id>/reconcile')
def reconcile(deck_id):
    db = get_db()
    deck = db.execute('SELECT * FROM decks WHERE id = ?', [deck_id]).fetchone()
    if not deck:
        db.close()
        abort(404)

    deck_cards = db.execute('''
        SELECT dc.id, dc.scryfall_id, dc.name, dc.count, dc.board,
               b.set_name, b.collector_number, b.image_uri_normal
        FROM deck_cards dc
        LEFT JOIN scryfall_bulk b ON dc.scryfall_id = b.scryfall_id
        WHERE dc.deck_id = ?
        ORDER BY dc.name
    ''', [deck_id]).fetchall()

    mismatches = []
    for dc in deck_cards:
        # Join through cards.scryfall_id too: collection.scryfall_id is a denormalized
        # copy that can lag behind, so don't trust it alone for the exact-match check.
        has_exact = db.execute('''
            SELECT 1 FROM collection c
            LEFT JOIN cards k ON c.card_id = k.id
            WHERE c.scryfall_id = ? OR k.scryfall_id = ?
            LIMIT 1
        ''', [dc['scryfall_id'], dc['scryfall_id']]).fetchone()
        if has_exact:
            continue

        owned_printings = db.execute('''
            SELECT c.id as collection_row_id, COALESCE(c.scryfall_id, k.scryfall_id) as scryfall_id,
                   c.count, c.foil, c.condition,
                   b.set_name, b.collector_number, b.image_uri_normal
            FROM collection c
            JOIN cards k ON c.card_id = k.id
            LEFT JOIN scryfall_bulk b ON COALESCE(c.scryfall_id, k.scryfall_id) = b.scryfall_id
            WHERE LOWER(k.name) = LOWER(?)
            ORDER BY b.set_name
        ''', [dc['name']]).fetchall()

        if owned_printings:
            mismatches.append({
                'deck_card': dict(dc),
                'owned_printings': [dict(p) for p in owned_printings],
            })

    db.close()
    return render_template('decks/reconcile.html', deck=deck, mismatches=mismatches)


@decks_bp.route('/<int:deck_id>/reconcile/use-deck-printing', methods=['POST'])
def reconcile_use_deck_printing(deck_id):
    """The deck list is correct — update the chosen owned collection row to that printing."""
    data = request.get_json(force=True)
    deck_card_id = data.get('deck_card_id')
    collection_row_id = data.get('collection_row_id')
    if not deck_card_id or not collection_row_id:
        return jsonify(error='deck_card_id and collection_row_id required'), 400

    db = get_db()
    dc = db.execute('SELECT * FROM deck_cards WHERE id = ? AND deck_id = ?', [deck_card_id, deck_id]).fetchone()
    coll_row = db.execute('SELECT * FROM collection WHERE id = ?', [collection_row_id]).fetchone()
    if not dc or not coll_row:
        db.close()
        return jsonify(error='Not found'), 404

    target_card_id, bulk_edition = get_or_create_card(db, dc['scryfall_id'])
    if target_card_id is None:
        db.close()
        return jsonify(error='Printing not found in bulk data'), 404

    new_edition = bulk_edition['set_name'] if bulk_edition else coll_row['edition']
    new_card_number = bulk_edition['collector_number'] if bulk_edition else coll_row['card_number']

    db.execute(
        'UPDATE collection SET card_id = ?, scryfall_id = ?, edition = ?, card_number = ? WHERE id = ?',
        (target_card_id, dc['scryfall_id'], new_edition, new_card_number, collection_row_id)
    )
    db.commit()
    db.close()
    return jsonify(success=True)


@decks_bp.route('/<int:deck_id>/reconcile/use-collection-printing', methods=['POST'])
def reconcile_use_collection_printing(deck_id):
    """The collection is correct — update the deck list to reference the owned printing."""
    data = request.get_json(force=True)
    deck_card_id = data.get('deck_card_id')
    scryfall_id = data.get('scryfall_id', '').strip()
    if not deck_card_id or not scryfall_id:
        return jsonify(error='deck_card_id and scryfall_id required'), 400

    db = get_db()
    dc = db.execute('SELECT * FROM deck_cards WHERE id = ? AND deck_id = ?', [deck_card_id, deck_id]).fetchone()
    if not dc:
        db.close()
        return jsonify(error='Not found'), 404

    db.execute('UPDATE deck_cards SET scryfall_id = ? WHERE id = ?', [scryfall_id, deck_card_id])
    db.commit()
    db.close()
    return jsonify(success=True)


# ── Delete deck ───────────────────────────────────────────────────────────────

@decks_bp.route('/<int:deck_id>/rename', methods=['POST'])
def rename_deck(deck_id):
    name = (request.json or {}).get('name', '').strip()
    if not name:
        return jsonify(error='Name cannot be empty'), 400
    db = get_db()
    db.execute('UPDATE decks SET name=? WHERE id=?', [name, deck_id])
    db.commit()
    db.close()
    return jsonify(ok=True, name=name)


@decks_bp.route('/<int:deck_id>/delete', methods=['POST'])
def delete_deck(deck_id):
    db = get_db()
    db.execute('DELETE FROM decks WHERE id=?', [deck_id])
    db.commit()
    db.close()
    return redirect(url_for('decks.index'))


# ── Clone deck ────────────────────────────────────────────────────────────────

@decks_bp.route('/<int:deck_id>/clone', methods=['POST'])
def clone_deck(deck_id):
    db = get_db()
    src = db.execute('SELECT * FROM decks WHERE id=?', [deck_id]).fetchone()
    if not src:
        db.close()
        abort(404)
    cur = db.execute(
        'INSERT INTO decks (name, format, description, created_at) VALUES (?,?,?,?)',
        [f'{src["name"]} (copy)', src['format'], src['description'], _now()]
    )
    new_id = cur.lastrowid
    db.execute('''
        INSERT INTO deck_cards (deck_id, scryfall_id, name, count, board, added_at)
        SELECT ?, scryfall_id, name, count, board, ?
        FROM deck_cards WHERE deck_id=?
    ''', [new_id, _now(), deck_id])
    db.commit()
    db.close()
    return redirect(url_for('decks.detail', deck_id=new_id))


# ── List decks as JSON (for context menu) ────────────────────────────────────

@decks_bp.route('/<int:deck_id>/set-commander', methods=['POST'])
def set_commander(deck_id):
    data = request.get_json(force=True)
    new_sid = data.get('scryfall_id', '').strip()
    db = get_db()
    if not db.execute('SELECT 1 FROM decks WHERE id=?', [deck_id]).fetchone():
        db.close()
        return jsonify(error='Deck not found'), 404

    # Move current commander board card(s) back to main
    db.execute(
        "UPDATE deck_cards SET board='main' WHERE deck_id=? AND board='commander'",
        [deck_id]
    )
    # Move the chosen card to the commander board
    db.execute(
        "UPDATE deck_cards SET board='commander' WHERE deck_id=? AND scryfall_id=?",
        [deck_id, new_sid]
    )
    db.commit()

    name = ''
    row = db.execute('SELECT name FROM scryfall_bulk WHERE scryfall_id=? LIMIT 1', [new_sid]).fetchone()
    if row:
        name = row['name']
    db.close()
    return jsonify(ok=True, name=name)


@decks_bp.route('/<int:deck_id>/set-strategy', methods=['POST'])
def set_strategy(deck_id):
    data = request.get_json(force=True)
    strategy = data.get('strategy', '').strip()
    from app.strategy import STRATEGIES
    if strategy not in STRATEGIES:
        return jsonify(error='Unknown strategy'), 400
    db = get_db()
    if not db.execute('SELECT 1 FROM decks WHERE id=?', [deck_id]).fetchone():
        db.close()
        return jsonify(error='Deck not found'), 404
    db.execute('UPDATE decks SET strategy=? WHERE id=?', [strategy, deck_id])
    db.commit()
    db.close()
    return jsonify(ok=True)


@decks_bp.route('/<int:deck_id>/mute-check', methods=['POST'])
def mute_check(deck_id):
    data = request.get_json(force=True)
    check_name = data.get('check_name', '').strip()
    if not check_name:
        return jsonify(error='check_name required'), 400
    db = get_db()
    db.execute('INSERT OR IGNORE INTO deck_health_mutes (deck_id, check_name) VALUES (?,?)',
               [deck_id, check_name])
    db.commit()
    db.close()
    return jsonify(ok=True)


@decks_bp.route('/<int:deck_id>/unmute-check', methods=['POST'])
def unmute_check(deck_id):
    data = request.get_json(force=True)
    check_name = data.get('check_name', '').strip()
    db = get_db()
    db.execute('DELETE FROM deck_health_mutes WHERE deck_id=? AND check_name=?',
               [deck_id, check_name])
    db.commit()
    db.close()
    return jsonify(ok=True)


@decks_bp.route('/<int:deck_id>/detect-strategy', methods=['POST'])
def detect_strategy_route(deck_id):
    from app.strategy import detect_strategy, save_detection
    db = get_db()
    deck = db.execute('SELECT * FROM decks WHERE id=?', [deck_id]).fetchone()
    if not deck:
        db.close()
        return jsonify(error='Deck not found'), 404
    rows = db.execute('''
        SELECT dc.name, dc.count, dc.board,
               b.oracle_text, b.type_line, b.cmc, b.power, b.toughness
        FROM deck_cards dc
        LEFT JOIN scryfall_bulk b ON dc.scryfall_id = b.scryfall_id
        WHERE dc.deck_id = ?
    ''', [deck_id]).fetchall()
    cmd_row = db.execute(
        "SELECT name FROM deck_cards WHERE deck_id=? AND board='commander' LIMIT 1",
        [deck_id]
    ).fetchone()
    detection = detect_strategy([dict(r) for r in rows],
                                 commander=cmd_row['name'] if cmd_row else None)
    save_detection(db, deck_id, detection)
    db.close()
    return jsonify(detection)


@decks_bp.route('/<int:deck_id>/rate-detection', methods=['POST'])
def rate_detection_route(deck_id):
    from app.strategy import rate_detection
    data = request.get_json(force=True)
    rating = data.get('rating')
    if rating not in (0, 1):
        return jsonify(error='rating must be 0 or 1'), 400
    db = get_db()
    rate_detection(db, deck_id, rating)
    db.close()
    return jsonify(ok=True)


@decks_bp.route('/api/list')
def api_list():
    db = get_db()
    rows = db.execute('SELECT id, name, format FROM decks ORDER BY name').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


# ── EDHREC suggestions ────────────────────────────────────────────────────────

@decks_bp.route('/<int:deck_id>/suggestions')
def suggestions(deck_id):
    from app.edhrec import get_edhrec_data, CACHE_TTL_DAYS
    import json as _json

    db = get_db()
    deck = db.execute('SELECT * FROM decks WHERE id = ?', [deck_id]).fetchone()
    if not deck:
        db.close()
        abort(404)

    # Active commander = whatever is on the commander board, else best guess.
    commander = resolve_commander(db, deck_id)

    # Cards already in the deck (by name, lowercase) for deduplication
    deck_card_names = set(
        r['name'].lower() for r in
        db.execute('SELECT name FROM deck_cards WHERE deck_id = ?', [deck_id]).fetchall()
    )

    # Owned card names (for "owned" flag on suggestions)
    owned_names = set(
        r['name'].lower() for r in
        db.execute('''SELECT DISTINCT LOWER(k.name) as name
                      FROM collection c JOIN cards k ON c.card_id = k.id''').fetchall()
    )

    edhrec_data = None
    error = None
    force = request.args.get('refresh') == '1'

    if commander:
        edhrec_data = get_edhrec_data(db, commander['name'], force_refresh=force)
        if edhrec_data is None:
            error = f"No EDHREC data found for '{commander['name']}'. It may not be recognised as a commander."

    # Build suggestion lists from edhrec_cards, joined against scryfall_bulk
    # Group into: high synergy, owned suggestions, cards to consider buying
    high_synergy = []
    owned_suggestions = []
    buy_suggestions = []
    cut_candidates = []

    if edhrec_data:
        # Get color identity of deck from commander card in scryfall_bulk
        color_identity = []
        if commander:
            bulk = db.execute(
                'SELECT color_identity FROM scryfall_bulk WHERE scryfall_id = ? LIMIT 1',
                [commander['scryfall_id']]
            ).fetchone()
            if bulk:
                try:
                    color_identity = _json.loads(bulk['color_identity'] or '[]')
                except Exception:
                    color_identity = []

        # Pull all EDHREC suggestions, joined to scryfall_bulk for local data
        rows = db.execute('''
            SELECT ec.name, ec.tag, ec.synergy, ec.inclusion, ec.num_decks,
                   ec.potential_decks, ec.scryfall_id,
                   b.type_line, b.mana_cost, b.cmc, b.image_uri_normal,
                   b.price_usd, b.color_identity as card_color_identity
            FROM edhrec_cards ec
            LEFT JOIN scryfall_bulk b ON ec.scryfall_id = b.scryfall_id
            WHERE ec.slug = ?
            ORDER BY ec.synergy DESC, ec.inclusion DESC
        ''', [edhrec_data['slug']]).fetchall()

        seen_names = set()
        for r in rows:
            name_lower = r['name'].lower()
            if name_lower in deck_card_names:
                continue
            if name_lower in seen_names:
                continue
            seen_names.add(name_lower)

            card = dict(r)
            card['owned'] = name_lower in owned_names
            card['pct'] = round(100 * r['inclusion'] / r['potential_decks']) if r['potential_decks'] else 0

            if r['tag'] in ('highsynergycards', 'newcards', 'gamechangers'):
                high_synergy.append(card)
            if card['owned']:
                owned_suggestions.append(card)
            else:
                buy_suggestions.append(card)

        # Sort
        high_synergy.sort(key=lambda c: (-c['synergy'], -c['inclusion']))
        owned_suggestions.sort(key=lambda c: (-c['synergy'], -c['inclusion']))
        buy_suggestions.sort(key=lambda c: (-c['synergy'], -c['inclusion']))

        # Cap lists
        from app.database import get_setting as _get_setting2
        _sugg_limit2 = int(_get_setting2(db, 'suggestions_per_type', 7))
        high_synergy      = high_synergy[:_sugg_limit2 * 3]
        owned_suggestions = owned_suggestions[:_sugg_limit2 * 6]
        buy_suggestions   = buy_suggestions[:_sugg_limit2 * 6]

        # ── Cut candidates: cards already in deck with low keep score ──
        # Score = 0.6 * synergy_component + 0.4 * health_component
        # Lower score = weaker card = better cut candidate
        edhrec_slug = edhrec_data['slug']

        # Build EDHREC lookup: card name (lower) → synergy score
        edhrec_lookup = {}
        for ec in db.execute(
            'SELECT name, synergy FROM edhrec_cards WHERE slug = ?', [edhrec_slug]
        ).fetchall():
            name_lower = ec['name'].lower()
            if name_lower not in edhrec_lookup or ec['synergy'] > edhrec_lookup[name_lower]:
                edhrec_lookup[name_lower] = ec['synergy']

        deck_cards_full = db.execute('''
            SELECT dc.id, dc.scryfall_id, dc.name, dc.count, dc.board,
                   b.type_line, b.mana_cost, b.image_uri_normal,
                   b.oracle_text, b.cmc, b.power, b.price_usd
            FROM deck_cards dc
            LEFT JOIN scryfall_bulk b ON dc.scryfall_id = b.scryfall_id
            WHERE dc.deck_id = ?
        ''', [deck_id]).fetchall()

        commander_name_lower = commander['name'].lower() if commander else ''

        for dc in deck_cards_full:
            name_lower = dc['name'].lower()
            # Skip basics and the commander itself
            if name_lower in BASIC_LANDS_LOWER or name_lower == commander_name_lower:
                continue
            # Skip pure lands (no oracle synergy signal)
            if 'Land' in (dc['type_line'] or '') and 'Creature' not in (dc['type_line'] or ''):
                continue

            # EDHREC synergy: normalise to 0–1 range (synergy is typically -1.0 to +1.0)
            raw_syn = edhrec_lookup.get(name_lower)
            if raw_syn is not None:
                # Map -1..+1 → 0..1; missing from EDHREC = 0.2 (unknown, slight penalty)
                syn_component = (raw_syn + 1.0) / 2.0
                in_edhrec = True
            else:
                syn_component = 0.2
                in_edhrec = False

            health_component = health_score(
                dc['oracle_text'], dc['type_line'], dc['cmc'], dc['power']
            )

            keep_score = 0.6 * syn_component + 0.4 * health_component

            cut_candidates.append({
                'deck_card_id':   dc['id'],
                'scryfall_id':    dc['scryfall_id'],
                'name':           dc['name'],
                'type_line':      dc['type_line'],
                'mana_cost':      dc['mana_cost'],
                'image_uri_normal': dc['image_uri_normal'],
                'price_usd':      dc['price_usd'],
                'board':          dc['board'],
                'synergy':        raw_syn,
                'in_edhrec':      in_edhrec,
                'health_score':   health_component,
                'keep_score':     keep_score,
            })

        # Sort worst keep score first, cap at 20
        cut_candidates.sort(key=lambda c: c['keep_score'])
        cut_candidates = cut_candidates[:20]

    db.close()
    return render_template('decks/suggestions.html',
                           deck=deck,
                           commander=commander,
                           edhrec_data=edhrec_data,
                           high_synergy=high_synergy,
                           owned_suggestions=owned_suggestions,
                           buy_suggestions=buy_suggestions,
                           cut_candidates=cut_candidates,
                           error=error,
                           format_label=FORMAT_LABELS.get(deck['format'], deck['format']),
                           cache_ttl_days=CACHE_TTL_DAYS)


@decks_bp.route('/<int:deck_id>/suggestions/refresh', methods=['POST'])
def suggestions_refresh(deck_id):
    return redirect(url_for('decks.suggestions', deck_id=deck_id, refresh='1'))


# ── Auto-fill ─────────────────────────────────────────────────────────────────

@decks_bp.route('/<int:deck_id>/auto-fill')
def auto_fill_preview(deck_id):
    import json as _json
    from app.auto_fill import auto_fill
    from app.strategy import STRATEGIES, DEFAULT_STRATEGY

    db = get_db()
    deck = db.execute('SELECT * FROM decks WHERE id=?', [deck_id]).fetchone()
    if not deck:
        db.close()
        abort(404)

    # Resolve commander
    cmd = resolve_commander(db, deck_id)

    if not cmd:
        db.close()
        return render_template('decks/auto_fill.html', deck=deck, error='No commander found.',
                               result=None, strategies=STRATEGIES,
                               strategy_key=deck['strategy'] or DEFAULT_STRATEGY,
                               format_label=FORMAT_LABELS.get(deck['format'], deck['format']))

    # Get commander colour identity
    bulk = db.execute('SELECT color_identity FROM scryfall_bulk WHERE scryfall_id=? LIMIT 1',
                      [cmd['scryfall_id']]).fetchone()
    color_identity = bulk['color_identity'] if bulk else '[]'

    strategy_key = request.args.get('strategy') or deck['strategy'] or DEFAULT_STRATEGY
    if strategy_key not in STRATEGIES:
        strategy_key = DEFAULT_STRATEGY

    # Check EDHREC cache exists
    from app.edhrec import _name_to_slug
    slug = _name_to_slug(cmd['name'])
    has_edhrec = bool(db.execute('SELECT 1 FROM edhrec_cache WHERE slug=?', [slug]).fetchone())

    result = None
    error = None
    if not has_edhrec:
        error = f"No EDHREC data cached for {cmd['name']}. Use ⟳ EDHREC on the deck page first."
    else:
        result = auto_fill(db, deck_id, cmd['name'], color_identity, strategy_key)

    db.close()
    return render_template('decks/auto_fill.html',
                           deck=deck,
                           commander=cmd,
                           result=result,
                           error=error,
                           strategies=STRATEGIES,
                           strategy_key=strategy_key,
                           format_label=FORMAT_LABELS.get(deck['format'], deck['format']))


@decks_bp.route('/<int:deck_id>/auto-fill/apply', methods=['POST'])
def auto_fill_apply(deck_id):
    import json as _json
    from app.auto_fill import auto_fill
    from app.strategy import STRATEGIES, DEFAULT_STRATEGY

    db = get_db()
    deck = db.execute('SELECT * FROM decks WHERE id=?', [deck_id]).fetchone()
    if not deck:
        db.close()
        abort(404)

    cmd = db.execute(
        "SELECT dc.name, dc.scryfall_id FROM deck_cards dc "
        "WHERE dc.deck_id=? AND dc.board='commander' LIMIT 1", [deck_id]
    ).fetchone()
    if not cmd:
        db.close()
        return jsonify(error='No commander found'), 400

    bulk = db.execute('SELECT color_identity FROM scryfall_bulk WHERE scryfall_id=? LIMIT 1',
                      [cmd['scryfall_id']]).fetchone()
    color_identity = bulk['color_identity'] if bulk else '[]'

    strategy_key = request.form.get('strategy') or deck['strategy'] or DEFAULT_STRATEGY
    if strategy_key not in STRATEGIES:
        strategy_key = DEFAULT_STRATEGY

    result = auto_fill(db, deck_id, cmd['name'], color_identity, strategy_key)

    added = 0
    for card in result['picked'] + result['land_picks']:
        sid = card.get('scryfall_id')
        if not sid:
            continue
        count = card.get('count', 1)
        existing = db.execute(
            'SELECT id, count FROM deck_cards WHERE deck_id=? AND scryfall_id=? AND board=?',
            [deck_id, sid, 'main']
        ).fetchone()
        if existing:
            db.execute('UPDATE deck_cards SET count=count+? WHERE id=?', [count, existing['id']])
        else:
            db.execute(
                'INSERT INTO deck_cards (deck_id, scryfall_id, name, count, board, added_at) VALUES (?,?,?,?,?,?)',
                [deck_id, sid, card['name'], count, 'main', _now()]
            )
        added += count

    db.commit()
    db.close()
    return redirect(url_for('decks.detail', deck_id=deck_id))


# ── Combo detail (lazy-loaded) ────────────────────────────────────────────────

@decks_bp.route('/<int:deck_id>/combo-detail/<path:combo_id>')
def combo_detail(deck_id, combo_id):
    from app.database import get_db
    from app.edhrec import fetch_combo_detail
    from flask import jsonify as _jsonify
    db = get_db()
    # Look up href from combo_cache
    row = db.execute('SELECT combos_json FROM combo_cache WHERE slug IN (SELECT slug FROM combo_cache LIMIT 1)').fetchone()
    href = None
    if row:
        import json as _j
        for combo in _j.loads(row['combos_json']):
            if combo['comboId'] == combo_id:
                href = combo['href']
                break
    if not href:
        db.close()
        return _jsonify(error='not found'), 404
    detail = fetch_combo_detail(db, combo_id, href)
    db.close()
    if not detail:
        return _jsonify(error='fetch failed'), 502
    return _jsonify(detail)
