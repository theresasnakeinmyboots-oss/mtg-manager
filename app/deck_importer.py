"""
Parse and resolve deck list text into deck_cards entries.

Supported formats
-----------------
  1 Lightning Bolt
  1x Lightning Bolt
  1 Lightning Bolt (M11)
  1 Lightning Bolt (M11) 141              ← MTGA format (short code + collector number)
  4 Ragavan, Nimble Pilferer (MH2) 138
  1x Lightning Bolt (Magic 2011)          ← full set name in parens (Deckbox / CSV export)
  1x,Lightning Bolt,Magic 2011,           ← CSV format (QuantityX,Name,Edition,Foil header)

Section headers (case-insensitive, alone on a line):
  Deck / Main / Mainboard
  Sideboard / Side
  Commander
  // comment lines are skipped
"""

import re

# Matches: "1x Card Name (Set hint) optional-number"
# Set hint may be a short code (M11) or a full set name with spaces.
_LINE_RE = re.compile(
    r'^\s*(\d+)[xX]?\s+'           # count
    r'(.+?)'                        # card name (non-greedy)
    r'(?:\s+\(([^)]+)\)'           # optional (anything in parens) as set hint
    r'(?:\s+\d+)?)?'               # optional collector number after closing paren
    r'\s*$'
)

# CSV row: quantity (with optional x), name, optional edition, optional foil
_CSV_RE = re.compile(r'^\s*(\d+)[xX]?\s*,\s*(.+?)(?:\s*,([^,]*)(?:,[^,]*)?)?\s*$')

_SECTION_RE = re.compile(
    r'^\s*(?://\s*)?(deck|main|mainboard|sideboard|side|commander)\s*$',
    re.IGNORECASE
)

_BASIC_LANDS = {
    'Plains', 'Island', 'Swamp', 'Mountain', 'Forest', 'Wastes',
    'Snow-Covered Plains', 'Snow-Covered Island', 'Snow-Covered Swamp',
    'Snow-Covered Mountain', 'Snow-Covered Forest',
}


def parse_deck_list(text):
    """
    Parse raw text into a list of dicts:
      { count, name, set_hint, board }
    board is 'main', 'side', or 'commander'.
    """
    entries = []
    board = 'main'

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        # Skip CSV header rows (e.g. "QuantityX,Name,Edition,Foil")
        if line.lower().startswith(('quantity', 'count', 'name,', 'qty')):
            continue

        # Skip pure comment lines
        if line.startswith('//'):
            continue

        # Section header?
        m = _SECTION_RE.match(line)
        if m:
            token = m.group(1).lower()
            if token in ('sideboard', 'side'):
                board = 'side'
            elif token == 'commander':
                board = 'commander'
            else:
                board = 'main'
            continue

        # Try CSV format first (comma-separated)
        m = _CSV_RE.match(line)
        if m:
            count    = int(m.group(1))
            name     = m.group(2).strip()
            set_hint = m.group(3).strip() if m.group(3) else None
            entries.append({'count': count, 'name': name, 'set_hint': set_hint, 'board': board})
            continue

        # Standard text format
        m = _LINE_RE.match(line)
        if not m:
            continue

        count    = int(m.group(1))
        name     = m.group(2).strip()
        set_hint = m.group(3).strip() if m.group(3) else None

        entries.append({
            'count':    count,
            'name':     name,
            'set_hint': set_hint,
            'board':    board,
        })

    return entries


def resolve_cards(db, parsed):
    """
    Match parsed entries against scryfall_bulk.
    Returns (entries, warnings) where entries are ready for INSERT into deck_cards.
    """
    resolved = []
    warnings = []

    for entry in parsed:
        name     = entry['name']
        set_hint = entry['set_hint']
        count    = entry['count']
        board    = entry['board']

        row = None

        if set_hint:
            hint_upper = set_hint.upper()
            # 1a. Owned copy, exact name + short set code
            row = db.execute('''
                SELECT b.scryfall_id, b.name FROM scryfall_bulk b
                JOIN cards k ON b.scryfall_id = k.scryfall_id
                JOIN collection c ON c.card_id = k.id
                WHERE LOWER(b.name)=LOWER(?) AND UPPER(b.set_code)=?
                LIMIT 1
            ''', [name, hint_upper]).fetchone()
            # 1b. Owned copy, exact name + full set name
            if not row:
                row = db.execute('''
                    SELECT b.scryfall_id, b.name FROM scryfall_bulk b
                    JOIN cards k ON b.scryfall_id = k.scryfall_id
                    JOIN collection c ON c.card_id = k.id
                    WHERE LOWER(b.name)=LOWER(?) AND LOWER(b.set_name)=LOWER(?)
                    LIMIT 1
                ''', [name, set_hint]).fetchone()
            # 1c. Any printing, short set code (fallback if not owned)
            if not row:
                row = db.execute(
                    'SELECT scryfall_id, name FROM scryfall_bulk WHERE LOWER(name)=LOWER(?) AND UPPER(set_code)=? LIMIT 1',
                    [name, hint_upper]
                ).fetchone()
            # 1d. Any printing, full set name (fallback if not owned)
            if not row:
                row = db.execute(
                    'SELECT scryfall_id, name FROM scryfall_bulk WHERE LOWER(name)=LOWER(?) AND LOWER(set_name)=LOWER(?) LIMIT 1',
                    [name, set_hint]
                ).fetchone()

        # 2. Owned copy, any set
        if not row:
            row = db.execute('''
                SELECT b.scryfall_id, b.name FROM scryfall_bulk b
                JOIN cards k ON b.scryfall_id = k.scryfall_id
                JOIN collection c ON c.card_id = k.id
                WHERE LOWER(b.name)=LOWER(?)
                LIMIT 1
            ''', [name]).fetchone()

        # 3. Any printing, any set
        if not row:
            row = db.execute(
                'SELECT scryfall_id, name FROM scryfall_bulk WHERE LOWER(name)=LOWER(?) LIMIT 1',
                [name]
            ).fetchone()

        # 4. Fuzzy prefix match (handles // double-faced card name differences)
        if not row:
            row = db.execute(
                'SELECT scryfall_id, name FROM scryfall_bulk WHERE LOWER(name) LIKE LOWER(?) LIMIT 1',
                [f'{name}%']
            ).fetchone()

        if not row:
            warnings.append(f'Could not find card: "{name}"')
            continue

        resolved.append({
            'scryfall_id': row['scryfall_id'],
            'name':        row['name'],
            'count':       count,
            'board':       board,
        })

    return resolved, warnings
