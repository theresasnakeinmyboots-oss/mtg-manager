"""
Deck strategy detection and health check configuration.

Strategies define:
  - label/description shown to the user
  - which health checks apply and their thresholds
  - extra checks specific to that strategy
  - detection signals (oracle text patterns + type heuristics)
"""
import re
from datetime import datetime, timezone

# ── Core role-detection patterns ──────────────────────────────────────────────
# These five patterns were copy-pasted (by hand, with drift) across health
# computation, suggestion tagging, cut-candidate scoring, and auto-fill. They are
# the single source of truth now; everything else imports from here.

RAMP_RX = re.compile(
    r'search your library for (a|up to \d+) (basic )?land|'
    r'add \{[WUBRGC]\}|add (one|two|\d+) mana|add mana of any|untap target land',
    re.IGNORECASE)
REMOVAL_RX = re.compile(
    r'destroy target|exile target|'
    r'deals \d+ damage to (any target|target creature|each creature)|'
    r'-\d+/-\d+|return target .* to (its owner|their owner)\'s hand|'
    r'counter target (spell|creature|artifact|enchantment)',
    re.IGNORECASE)
DRAW_RX = re.compile(
    r'draw (a|two|three|\d+) card|investigate|whenever .* draw|'
    r'look at the top \d+ card|scry \d',
    re.IGNORECASE)
PROTECTION_RX = re.compile(
    r'hexproof|indestructible|regenerate|shroud|ward|\bprotection\b|'
    r'counter target spell|can\'t be countered',
    re.IGNORECASE)
WINCON_RX = re.compile(
    r'players (lose|win) the game|you win the game|'
    r'deal (infinite|combat) damage|\binfinite\b',
    re.IGNORECASE)

# Label -> compiled pattern, in priority order for tagging.
ROLE_PATTERNS = [
    ('Ramp',       RAMP_RX),
    ('Removal',    REMOVAL_RX),
    ('Card draw',  DRAW_RX),
    ('Protection', PROTECTION_RX),
    ('Win con',    WINCON_RX),
]


def _is_big_threat(type_line, cmc, power):
    """A 6+ CMC creature with power 5+ counts as a win condition / threat."""
    return ('Land' not in (type_line or '')
            and int(cmc or 0) >= 6
            and power is not None and str(power).lstrip('-').isdigit()
            and int(power) >= 5)


def find_win_cons(rows):
    """
    Return the deck's actual win-condition cards — deduped by scryfall_id,
    highest CMC first. A card qualifies if its oracle text reads as an
    explicit win condition (WINCON_RX) or it's a big enough threat to end
    the game on its own (_is_big_threat) — the same two checks that already
    feed the 'Win cons' health-check count, just surfaced as a card list
    instead of a number. Independent of the deck's chosen strategy, since
    whether a card can win the game isn't a matter of strategy preference.
    rows must have scryfall_id, oracle_text, type_line, cmc, power.
    """
    seen = set()
    out = []
    for r in rows:
        sid = r.get('scryfall_id')
        if not sid or sid in seen:
            continue
        oracle = r.get('oracle_text') or ''
        is_explicit = bool(WINCON_RX.search(oracle))
        is_threat = _is_big_threat(r.get('type_line'), r.get('cmc'), r.get('power'))
        if not (is_explicit or is_threat):
            continue
        seen.add(sid)
        out.append({**r, 'is_explicit': is_explicit, 'is_threat': is_threat})
    out.sort(key=lambda r: -(r.get('cmc') or 0))
    return out


def _card_tribes(type_line):
    """Creature subtypes (the part after the em dash), lowercased, for tribe matching."""
    if not type_line or '—' not in type_line:
        return set()
    return {s.lower() for s in type_line.split('—', 1)[1].split()}


def detect_tribe(commander_type_line):
    """Return the commander's creature subtypes (its 'tribe'), or [] if it isn't
    a creature / has no subtypes. A multi-typed commander (e.g. 'Elf Warrior')
    yields both — a card matching either counts toward the tribe."""
    if not commander_type_line or 'Creature' not in commander_type_line:
        return []
    return sorted(_card_tribes(commander_type_line))


def count_type_tribe(rows, tribes):
    """Count owned/deck creatures sharing a subtype with the commander's tribe."""
    if not tribes:
        return 0
    tribe_set = {t.lower() for t in tribes}
    return sum(
        r['count'] for r in rows
        if 'Creature' in (r.get('type_line') or '') and _card_tribes(r.get('type_line')) & tribe_set
    )


def _tribe_pattern(tribes):
    if not tribes:
        return None
    return r'\b(?:' + '|'.join(re.escape(t) for t in tribes) + r')s?\b'


def count_typal_payoffs(rows, tribes):
    """Count cards whose oracle text references the tribe by name — lords,
    typal tutors, cost reducers, etc."""
    pattern = _tribe_pattern(tribes)
    if not pattern:
        return 0
    rx = re.compile(pattern, re.IGNORECASE)
    return sum(r['count'] for r in rows if rx.search(r.get('oracle_text') or ''))


def role_tags(oracle, type_line=None, cmc=None, power=None, limit=3):
    """Return up to `limit` role labels for a card based on its oracle text,
    adding 'Threat' for big creatures. Used for suggestion-card chips."""
    if not oracle:
        oracle = ''
    tags = [label for label, rx in ROLE_PATTERNS if rx.search(oracle)]
    if _is_big_threat(type_line, cmc, power) and 'Win con' not in tags:
        tags.append('Threat')
    return tags[:limit] if limit else tags


def health_score(oracle, type_line=None, cmc=None, power=None):
    """0.0–1.0 score for how many health categories a card covers (saturates
    at 2 categories = 1.0). Used for cut-candidate keep-scoring."""
    if not oracle:
        return 0.0
    cats = sum(1 for _, rx in ROLE_PATTERNS if rx.search(oracle))
    if _is_big_threat(type_line, cmc, power):
        cats += 1
    return min(cats / 2.0, 1.0)


# ── Strategy definitions ──────────────────────────────────────────────────────

STRATEGIES = {
    'aggro': {
        'label': 'Aggro',
        'description': 'Win fast through creatures and direct damage before opponents stabilise.',
        'health_checks': {
            'Removal':    {'ok': 6,  'warn': 3,  'tip': 'Aim for 6–10 removal spells'},
            'Card draw':  {'ok': 4,  'warn': 2,  'tip': 'Aggro needs some refuel — 4+ draw effects'},
            'Win cons':   {'ok': 4,  'warn': 2,  'tip': 'Threats and finishers'},
        },
    },
    'midrange': {
        'label': 'Midrange',
        'description': 'Efficient creatures and interaction across all stages of the game.',
        'health_checks': {
            'Removal':    {'ok': 8,  'warn': 4,  'tip': 'Aim for 8–12 removal spells'},
            'Card draw':  {'ok': 8,  'warn': 4,  'tip': 'Aim for 8–12 draw effects'},
            'Ramp':       {'ok': 10, 'warn': 5,  'tip': 'Aim for 10+ non-land ramp'},
            'Protection': {'ok': 4,  'warn': 2,  'tip': 'Protect your key pieces'},
            'Win cons':   {'ok': 3,  'warn': 1,  'tip': 'Big threats or game-winning combos'},
        },
    },
    'control': {
        'label': 'Control',
        'description': 'Answer threats and win in the late game with card advantage and finishers.',
        'health_checks': {
            'Removal':    {'ok': 12, 'warn': 8,  'tip': 'Control needs lots of removal — aim for 12+'},
            'Card draw':  {'ok': 12, 'warn': 8,  'tip': 'Card advantage is your engine — aim for 12+'},
            'Ramp':       {'ok': 8,  'warn': 4,  'tip': 'Mana rocks and ramp to cast big spells'},
            'Protection': {'ok': 6,  'warn': 3,  'tip': 'Counterspells and protection for your threats'},
            'Win cons':   {'ok': 2,  'warn': 1,  'tip': 'A few finishers is enough — quality over quantity'},
        },
    },
    'combo': {
        'label': 'Combo',
        'description': 'Assemble specific card combinations to win in a single turn.',
        'health_checks': {
            'Removal':    {'ok': 6,  'warn': 3,  'tip': 'Enough to survive to your combo turn'},
            'Card draw':  {'ok': 12, 'warn': 8,  'tip': 'Tutors and draw to find combo pieces'},
            'Ramp':       {'ok': 12, 'warn': 6,  'tip': 'Go off as fast as possible'},
            'Protection': {'ok': 8,  'warn': 4,  'tip': 'Protect your combo from interaction'},
            'Win cons':   {'ok': 2,  'warn': 1,  'tip': 'Your combo IS your win con — fewer pieces needed'},
        },
    },
    'pillowfort': {
        'label': 'Pillowfort / Politics',
        'description': 'Discourage attacks against you and manipulate opponents into fighting each other.',
        'health_checks': {
            'Removal':    {'ok': 6,  'warn': 3,  'tip': 'Some targeted removal for threats that can\'t be redirected'},
            'Card draw':  {'ok': 8,  'warn': 4,  'tip': 'Stay ahead on cards while opponents battle'},
            'Ramp':       {'ok': 10, 'warn': 5,  'tip': 'Aim for 10+ non-land ramp'},
            'Pillowfort': {'ok': 6,  'warn': 3,  'tip': 'Effects that discourage attacks or goad opponents (Ghostly Prison, Propaganda, Goad)'},
            'Political':  {'ok': 4,  'warn': 2,  'tip': 'Effects that manipulate opponents — monarch, voting, deals (Gahiji, Hunted Wumpus)'},
        },
    },
    'voltron': {
        'label': 'Voltron',
        'description': 'Stack auras and equipment on one creature to kill opponents with commander damage.',
        'health_checks': {
            'Removal':    {'ok': 6,  'warn': 3,  'tip': 'Clear the path for your commander'},
            'Card draw':  {'ok': 8,  'warn': 4,  'tip': 'Find your equipment and auras'},
            'Ramp':       {'ok': 10, 'warn': 5,  'tip': 'Aim for 10+ non-land ramp'},
            'Protection': {'ok': 8,  'warn': 5,  'tip': 'Your commander must survive — hexproof, indestructible, boots'},
            'Voltron':    {'ok': 12, 'warn': 8,  'tip': 'Auras and equipment to boost your commander'},
        },
    },
    'tokens': {
        'label': 'Tokens',
        'description': 'Create many creature tokens and win through wide attacks or sacrifice synergies.',
        'health_checks': {
            'Removal':    {'ok': 6,  'warn': 3,  'tip': 'Some removal to handle threats tokens can\'t block'},
            'Card draw':  {'ok': 8,  'warn': 4,  'tip': 'Refuel your token generation'},
            'Ramp':       {'ok': 10, 'warn': 5,  'tip': 'Aim for 10+ non-land ramp'},
            'Token gen':  {'ok': 10, 'warn': 6,  'tip': 'Cards that create multiple tokens (Raise the Alarm, Rhys the Redeemed)'},
            'Anthem':     {'ok': 4,  'warn': 2,  'tip': 'Cards that buff all your creatures (+1/+1 or similar)'},
        },
    },
    'spellslinger': {
        'label': 'Spellslinger',
        'description': 'Cast lots of instants and sorceries for value — creatures enable and reward your spells rather than being the primary threat.',
        'health_checks': {
            'Removal':      {'ok': 8,  'warn': 4,  'tip': 'Instants and sorceries double as removal — aim for 8+'},
            'Card draw':    {'ok': 14, 'warn': 8,  'tip': 'Spellslinger runs on card advantage — aim for 14+'},
            'Ramp':         {'ok': 10, 'warn': 5,  'tip': 'Rocks and ritual effects to chain spells'},
            'Spells':       {'ok': 20, 'warn': 12, 'tip': 'Instants and sorceries (your engine) — aim for 20+'},
            'Spell payoffs':{'ok': 8,  'warn': 4,  'tip': 'Cards that trigger or get better when you cast spells (Guttersnipe, Archmage Emeritus, magecraft)'},
        },
    },
    'stax': {
        'label': 'Stax',
        'description': 'Lock down the game with resource denial, taxing effects, and asymmetric hate.',
        'health_checks': {
            'Removal':    {'ok': 6,  'warn': 3,  'tip': 'Remove threats that break through your locks'},
            'Card draw':  {'ok': 8,  'warn': 4,  'tip': 'Stay ahead while opponents are locked out'},
            'Ramp':       {'ok': 12, 'warn': 6,  'tip': 'Fast mana to deploy stax pieces early'},
            'Stax pieces':{'ok': 8,  'warn': 4,  'tip': 'Tax and denial effects (Sphere of Resistance, Ghostly Prison, Winter Orb)'},
            'Win cons':   {'ok': 2,  'warn': 1,  'tip': 'A few slow win conditions is fine — you\'re grinding them out'},
        },
    },
    'tribal': {
        'label': 'Tribal',
        'description': 'Build around a shared creature type — lords, typal payoffs, and a critical mass of that type.',
        'health_checks': {
            'Removal':       {'ok': 6,  'warn': 3,  'tip': 'Some removal to survive to your payoffs'},
            'Card draw':     {'ok': 8,  'warn': 4,  'tip': 'Aim for 8+ draw effects'},
            'Ramp':          {'ok': 10, 'warn': 5,  'tip': 'Aim for 10+ non-land ramp'},
            'Type count':    {'ok': 20, 'warn': 12, 'tip': 'Critical mass of your tribe — 20+ creatures of the commander\'s type'},
            'Typal payoffs': {'ok': 8,  'warn': 4,  'tip': 'Lords, tutors, and cost reducers that key off the type by name'},
        },
    },
    'other': {
        'label': 'Other',
        'description': 'A custom or unusual strategy that doesn\'t fit standard categories.',
        'health_checks': {
            'Removal':    {'ok': 8,  'warn': 4,  'tip': 'Aim for 8–12 removal spells'},
            'Card draw':  {'ok': 8,  'warn': 4,  'tip': 'Aim for 8–12 draw effects'},
            'Ramp':       {'ok': 10, 'warn': 5,  'tip': 'Aim for 10+ non-land ramp'},
            'Protection': {'ok': 4,  'warn': 2,  'tip': 'Protect your key pieces'},
            'Win cons':   {'ok': 3,  'warn': 1,  'tip': 'Your main threats or game-winning cards'},
        },
    },
}

# Default (commander midrange) — used when strategy is not set
DEFAULT_STRATEGY = 'midrange'


# ── Extra check patterns ──────────────────────────────────────────────────────

def count_pillowfort(rows):
    rx = re.compile(
        r"can't attack you|can't attack unless|cost[s]? .* more to attack|"
        r"goad|goaded|attacks each combat if able|"
        r"opponent[s]? must attack|whenever .* attacks (you|a player other than)",
        re.IGNORECASE)
    return sum(r['count'] for r in rows if rx.search(r.get('oracle_text') or ''))

def count_political(rows):
    rx = re.compile(
        r"become[s]? the monarch|monarch|"
        r"each player|each opponent|council['']s dilemma|will of the council|"
        r"vote|you may have .* gain control|"
        r"hunted|tempting offer",
        re.IGNORECASE)
    return sum(r['count'] for r in rows if rx.search(r.get('oracle_text') or ''))

def count_voltron(rows):
    rx = re.compile(r'\bequip\b|\baura\b|enchant creature|attach', re.IGNORECASE)
    return sum(r['count'] for r in rows if rx.search(r.get('oracle_text') or '') or
               'Equipment' in (r.get('type_line') or '') or 'Aura' in (r.get('type_line') or ''))

def count_token_gen(rows):
    rx = re.compile(
        r'create[s]? \w+ [\w\s]+ token|put[s]? \w+ [\w\s]+ token|'
        r'populate|create[s]? a token copy',
        re.IGNORECASE)
    return sum(r['count'] for r in rows if rx.search(r.get('oracle_text') or ''))

def count_anthem(rows):
    rx = re.compile(
        r'creatures you control get \+|creatures you control have|'
        r'other creatures you control get \+',
        re.IGNORECASE)
    return sum(r['count'] for r in rows if rx.search(r.get('oracle_text') or ''))

def count_spells(rows):
    return sum(r['count'] for r in rows
               if any(t in (r.get('type_line') or '') for t in ('Instant', 'Sorcery')))

def count_spell_payoffs(rows):
    rx = re.compile(
        r'magecraft|whenever you cast an? (instant|sorcery)|'
        r'whenever you cast a (noncreature|nonland)|'
        r'prowess|storm|whenever you cast your (second|third|\d+)',
        re.IGNORECASE)
    return sum(r['count'] for r in rows if rx.search(r.get('oracle_text') or ''))

def count_stax(rows):
    rx = re.compile(
        r'spell[s]? cost[s]? .* more|each player (can only|can\'t|skips)|'
        r'creatures (don\'t untap|can\'t attack)|'
        r'opponents can\'t|players can\'t|'
        r'sphere of resistance|winter orb|static orb',
        re.IGNORECASE)
    return sum(r['count'] for r in rows if rx.search(r.get('oracle_text') or ''))


EXTRA_COUNTERS = {
    'Pillowfort':    count_pillowfort,
    'Political':     count_political,
    'Voltron':       count_voltron,
    'Token gen':     count_token_gen,
    'Anthem':        count_anthem,
    'Stax pieces':   count_stax,
    'Spells':        count_spells,
    'Spell payoffs': count_spell_payoffs,
}


# ── Health check computation ──────────────────────────────────────────────────

SEALED_HEALTH_CHECKS = {
    'Removal':    {'ok': 4,  'warn': 2,  'tip': 'Aim for 4–6 removal spells in a 40-card sealed deck'},
    'Card draw':  {'ok': 3,  'warn': 1,  'tip': 'Even 3 draw effects help in sealed'},
    'Creatures':  {'ok': 14, 'warn': 10, 'tip': 'Sealed decks need a creature base of 14–18'},
    'Win cons':   {'ok': 2,  'warn': 1,  'tip': 'Bombs and big threats — 2+ is enough in sealed'},
}


def compute_health(rows, strategy_key, is_commander, deck_format=None, commander_type_line=None):
    """
    Returns list of (label, count, tag, tip) tuples for the given strategy.
    rows must have oracle_text, type_line, count, cmc, power fields.
    commander_type_line is only used for the 'Tribal' strategy's Type
    count / Typal payoffs checks — harmless to pass for any other strategy.
    """
    import re as _re

    if deck_format == 'sealed':
        checks_def = SEALED_HEALTH_CHECKS
    else:
        strategy = STRATEGIES.get(strategy_key) or STRATEGIES[DEFAULT_STRATEGY]
        checks_def = strategy['health_checks']

    # Core pattern counters (same as before)
    _non_land = [r for r in rows if 'Land' not in (r.get('type_line') or '')]

    def _count(rx, source_rows=None):
        return sum(r['count'] for r in (source_rows or rows) if rx.search(r.get('oracle_text') or ''))

    core_counts = {
        'Removal': _count(REMOVAL_RX),
        'Card draw': _count(DRAW_RX),
        'Ramp': _count(RAMP_RX, _non_land),
        'Protection': _count(PROTECTION_RX),
        'Win cons': _count(WINCON_RX) + sum(
            r['count'] for r in rows
            if _is_big_threat(r.get('type_line'), r.get('cmc'), r.get('power'))
        ),
        'Creatures': sum(
            r['count'] for r in rows
            if 'Creature' in (r.get('type_line') or '')
        ),
    }

    # Extra counters for strategy-specific checks
    for label, fn in EXTRA_COUNTERS.items():
        core_counts[label] = fn(rows)

    tribes = detect_tribe(commander_type_line)
    core_counts['Type count'] = count_type_tribe(rows, tribes)
    core_counts['Typal payoffs'] = count_typal_payoffs(rows, tribes)

    result = []
    for label, thresholds in checks_def.items():
        count = core_counts.get(label, 0)
        ok, warn, tip = thresholds['ok'], thresholds['warn'], thresholds['tip']
        tag = 'ok' if count >= ok else ('warn' if count >= warn else 'low')
        result.append((label, count, tag, tip))

    return result


def compute_health_sids(rows, strategy_key, deck_format=None, commander_type_line=None):
    """Return dict of label -> [scryfall_ids] for click-to-filter."""
    import re as _re
    if deck_format == 'sealed':
        relevant = set(SEALED_HEALTH_CHECKS.keys())
    else:
        relevant = set(STRATEGIES.get(strategy_key, STRATEGIES[DEFAULT_STRATEGY])['health_checks'].keys())
    _non_land = [r for r in rows if 'Land' not in (r.get('type_line') or '')]

    def _sids(rx_pattern, source_rows=None, extra_pred=None):
        rx = _re.compile(rx_pattern, _re.IGNORECASE)
        out = []
        for r in (source_rows or rows):
            oracle = r.get('oracle_text') or ''
            if rx.search(oracle) or (extra_pred and extra_pred(r)):
                sid = r.get('scryfall_id')
                if sid and sid not in out:
                    out.append(sid)
        return out

    base = {
        'Removal': _sids(r"destroy target|exile target|deals \d+ damage to (any target|target creature|each creature)|-\d+/-\d+|return target .* to (its owner|their owner)'s hand|counter target (spell|creature|artifact|enchantment)"),
        'Card draw': _sids(r"draw (a|two|three|\d+) card|investigate|whenever .* draw|look at the top \d+ card|scry \d"),
        'Ramp': _sids(r"search your library for (a|up to \d+) (basic )?land|add \{[WUBRGC]\}|add (one|two|\d+) mana|add mana of any|untap target land", _non_land),
        'Protection': _sids(r"hexproof|indestructible|regenerate|shroud|ward|\bprotection\b|counter target spell|can't be countered"),
        'Win cons': _sids(r'players (lose|win) the game|you win the game|\binfinite\b'),
        'Creatures': [r.get('scryfall_id') for r in rows if 'Creature' in (r.get('type_line') or '') and r.get('scryfall_id')],
        'Pillowfort': _sids(r"can't attack you|can't attack unless|cost[s]? .* more to attack|goad|goaded|attacks each combat if able|opponent[s]? must attack|whenever .* attacks (you|a player other than)"),
        'Political': _sids(r"become[s]? the monarch|monarch|each player|each opponent|council['']s dilemma|will of the council|vote|hunted|tempting offer"),
        'Voltron': _sids(r'\bequip\b|\baura\b|enchant creature|attach', extra_pred=lambda r: 'Equipment' in (r.get('type_line') or '') or 'Aura' in (r.get('type_line') or '')),
        'Token gen': _sids(r'create[s]? \w+ [\w\s]+ token|put[s]? \w+ [\w\s]+ token|populate|create[s]? a token copy'),
        'Anthem': _sids(r'creatures you control get \+|creatures you control have|other creatures you control get \+'),
        'Stax pieces': _sids(r"spell[s]? cost[s]? .* more|each player (can only|can't|skips)|creatures (don't untap|can't attack)|opponents can't|players can't"),
        'Spells':        [r.get('scryfall_id') for r in rows if any(t in (r.get('type_line') or '') for t in ('Instant', 'Sorcery')) and r.get('scryfall_id')],
        'Spell payoffs': _sids(r'magecraft|whenever you cast an? (instant|sorcery)|whenever you cast a (noncreature|nonland)|prowess|storm'),
    }

    tribes = detect_tribe(commander_type_line)
    tribe_set = {t.lower() for t in tribes}
    base['Type count'] = [
        r.get('scryfall_id') for r in rows
        if r.get('scryfall_id') and 'Creature' in (r.get('type_line') or '')
        and _card_tribes(r.get('type_line')) & tribe_set
    ]
    tribe_pattern = _tribe_pattern(tribes)
    base['Typal payoffs'] = _sids(tribe_pattern) if tribe_pattern else []

    return {k: v for k, v in base.items() if k in relevant}


# ── Auto-detection ────────────────────────────────────────────────────────────

def detect_strategy(rows, commander=None, commander_type_line=None):
    """
    Analyse deck cards and return:
      {'strategy': str, 'confidence': float (0-1), 'reasoning': [str], 'scores': {str: float}}
    """
    total = max(sum(r['count'] for r in rows), 1)

    # Signal counts
    pillowfort  = count_pillowfort(rows)
    political   = count_political(rows)
    voltron     = count_voltron(rows)
    token_gen   = count_token_gen(rows)
    anthem      = count_anthem(rows)
    stax        = count_stax(rows)

    _non_land = [r for r in rows if 'Land' not in (r.get('type_line') or '')]
    import re as _re

    removal = sum(r['count'] for r in rows if _re.search(
        r'destroy target|exile target|deals \d+ damage to (any target|target creature|each creature)|'
        r'-\d+/-\d+|return target .* to (its owner|their owner)\'s hand|counter target (spell|creature|artifact|enchantment)',
        r.get('oracle_text') or '', _re.IGNORECASE))
    draw = sum(r['count'] for r in rows if _re.search(
        r'draw (a|two|three|\d+) card|investigate|whenever .* draw|look at the top \d+ card|scry \d',
        r.get('oracle_text') or '', _re.IGNORECASE))
    ramp = sum(r['count'] for r in _non_land if _re.search(
        r'search your library for (a|up to \d+) (basic )?land|add \{[WUBRGC]\}|add (one|two|\d+) mana|add mana of any',
        r.get('oracle_text') or '', _re.IGNORECASE))
    wincons = sum(r['count'] for r in rows if _re.search(
        r'you win the game|players lose the game|\binfinite\b', r.get('oracle_text') or '', _re.IGNORECASE))
    counter_spells = sum(r['count'] for r in rows if _re.search(
        r'^counter target|^counter all', r.get('oracle_text') or '', _re.IGNORECASE | _re.MULTILINE))
    creatures = sum(r['count'] for r in rows if 'Creature' in (r.get('type_line') or ''))
    low_curve = sum(r['count'] for r in rows if 'Land' not in (r.get('type_line') or '') and int(r.get('cmc') or 0) <= 2)
    avg_cmc = (sum(int(r.get('cmc') or 0) * r['count'] for r in _non_land) / max(sum(r['count'] for r in _non_land), 1))
    tutors = sum(r['count'] for r in rows if _re.search(r'search your library for .* card', r.get('oracle_text') or '', _re.IGNORECASE))

    reasoning = []
    scores = {}

    # Pillowfort signal: high pillowfort + political, modest removal, low explicit wincons
    pf_score = (pillowfort / total * 3.0) + (political / total * 2.0) - (wincons / total * 1.5)
    scores['pillowfort'] = max(pf_score, 0)
    if pillowfort >= 4:
        reasoning.append(f'Pillowfort: {pillowfort} cards discourage attacks or redirect them (e.g. "can\'t attack you", goad effects)')
    if political >= 4:
        reasoning.append(f'Politics: {political} cards manipulate opponents or benefit from group decisions (monarch, voting, "each opponent")')

    # Combo signal: high tutors + draw + ramp, few creatures, explicit wincons
    combo_score = (tutors / total * 2.5) + (draw / total * 1.5) + (ramp / total * 1.0) + (wincons / total * 2.0) - (creatures / total * 1.0)
    scores['combo'] = max(combo_score, 0)
    if tutors >= 5:
        reasoning.append(f'Combo: {tutors} tutors suggest assembling specific pieces')
    if wincons >= 3:
        reasoning.append(f'Combo/Win cons: {wincons} cards with explicit win conditions')

    # Aggro signal: low curve, lots of creatures, few draw/ramp
    aggro_score = (low_curve / total * 2.0) + (creatures / total * 1.5) - (draw / total * 1.0) - (ramp / total * 0.5)
    if avg_cmc < 2.5:
        aggro_score += 0.3
    scores['aggro'] = max(aggro_score, 0)
    if creatures >= 25 and avg_cmc < 3.0:
        reasoning.append(f'Aggro: {creatures} creatures with average CMC {avg_cmc:.1f} suggests fast pressure')

    # Tribal signal: commander has a creature type, and the deck backs it up
    # with a critical mass of that type plus payoffs that name it directly.
    tribes = detect_tribe(commander_type_line)
    type_count = count_type_tribe(rows, tribes)
    typal_payoffs = count_typal_payoffs(rows, tribes)
    tribal_score = 0
    if tribes:
        tribal_score = (type_count / total * 2.0) + (typal_payoffs / total * 3.0)
    scores['tribal'] = max(tribal_score, 0)
    if tribes and (type_count >= 12 or typal_payoffs >= 4):
        reasoning.append(
            f'Tribal: {type_count} {"/".join(tribes)} creatures and {typal_payoffs} '
            f'cards referencing the type by name'
        )

    # Voltron signal: high equipment/auras
    volt_score = voltron / total * 4.0
    scores['voltron'] = max(volt_score, 0)
    if voltron >= 8:
        reasoning.append(f'Voltron: {voltron} equipment/aura cards suggest suiting up one creature')

    # Tokens signal
    tok_score = (token_gen / total * 3.0) + (anthem / total * 2.0)
    scores['tokens'] = max(tok_score, 0)
    if token_gen >= 6:
        reasoning.append(f'Tokens: {token_gen} token-generating cards')
    if anthem >= 3:
        reasoning.append(f'Tokens: {anthem} anthem/buff-all-creatures effects')

    # Stax signal
    stax_score = stax / total * 4.0
    scores['stax'] = max(stax_score, 0)
    if stax >= 5:
        reasoning.append(f'Stax: {stax} resource-denial or taxing effects')

    # Spellslinger signal: high instant/sorcery count + payoffs
    spells      = count_spells(rows)
    payoffs     = count_spell_payoffs(rows)
    spell_score = (spells / total * 2.0) + (payoffs / total * 3.0) + (draw / total * 1.0) - (creatures / total * 0.5)
    scores['spellslinger'] = max(spell_score, 0)
    if spells >= 20:
        reasoning.append(f'Spellslinger: {spells} instants/sorceries')
    if payoffs >= 4:
        reasoning.append(f'Spellslinger: {payoffs} spell payoff cards (magecraft, prowess, etc.)')

    # Control signal: lots of removal + counterspells + draw, high curve
    ctrl_score = (removal / total * 1.5) + (counter_spells / total * 3.0) + (draw / total * 1.0)
    if avg_cmc > 3.5:
        ctrl_score += 0.2
    scores['control'] = max(ctrl_score, 0)
    if counter_spells >= 5:
        reasoning.append(f'Control: {counter_spells} counterspells')
    if removal >= 12:
        reasoning.append(f'Control: heavy removal suite ({removal} cards)')

    # Midrange is the baseline — score based on balanced distribution
    mid_score = 0.15  # baseline
    scores['midrange'] = mid_score

    best = max(scores, key=lambda k: scores[k])
    best_score = scores[best]
    total_score = sum(scores.values()) or 1
    confidence = min(best_score / total_score * len(scores), 1.0)

    if not reasoning:
        reasoning.append(f'No strong strategy signals detected — balanced distribution across categories ({creatures} creatures, {removal} removal, {draw} draw, {ramp} ramp)')
    else:
        reasoning.append(f'Overall: {creatures} creatures, avg CMC {avg_cmc:.1f}, {removal} removal, {draw} draw, {ramp} non-land ramp')

    return {
        'strategy': best,
        'confidence': round(confidence, 2),
        'reasoning': reasoning,
        'scores': {k: round(v, 3) for k, v in scores.items()},
    }


def save_detection(db, deck_id, detection):
    now = datetime.now(timezone.utc).isoformat()
    db.execute('''
        INSERT INTO deck_strategy_feedback
            (deck_id, detected_strategy, confidence, reasoning, detected_at)
        VALUES (?, ?, ?, ?, ?)
    ''', [deck_id, detection['strategy'], detection['confidence'],
          '\n'.join(detection['reasoning']), now])
    db.commit()


def rate_detection(db, deck_id, rating):
    """Update the most recent detection for a deck with a user rating (1=correct, 0=wrong)."""
    db.execute('''
        UPDATE deck_strategy_feedback SET user_rating = ?
        WHERE id = (
            SELECT id FROM deck_strategy_feedback WHERE deck_id = ? ORDER BY detected_at DESC LIMIT 1
        )
    ''', [rating, deck_id])
    db.commit()
