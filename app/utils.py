import re

# Mapping of MTG set codes to Keyrune codes
SET_CODE_MAP = {
    'LEA': 'lea', 'LEB': 'leb', '2ED': '2ed', '3ED': '3ed', '4ED': '4ed', '5ED': '5ed',
    '6ED': '6ed', '7ED': '7ed', '8ED': '8ed', '9ED': '9ed', '10E': '10e',
    'M10': 'm10', 'M11': 'm11', 'M12': 'm12', 'M13': 'm13', 'M14': 'm14', 'M15': 'm15',
    'M19': 'm19', 'M20': 'm20', 'M21': 'm21',
    'MH1': 'mh1', 'MH2': 'mh2',
    'CH': 'ch', 'AI': 'ai', 'MI': 'mi', 'VI': 'vi', 'PK': 'pk', 'TE': 'te', 'SH': 'sh',
    'EX': 'ex', 'ST': 'st', 'WL': 'wl', 'TP': 'tp', 'JU': 'ju', 'JD': 'jd',
    'ON': 'on', 'LS': 'ls', 'SC': 'sc', 'NE': 'ne', 'PR': 'pr', 'IN': 'in',
    'PS': 'ps', 'AP': 'ap', 'OD': 'od', 'TR': 'tr', 'JG': 'jg', 'LE': 'le',
    'SC': 'sc', 'MI': 'mi', 'DS': 'ds', 'CHK': 'chk', 'BOK': 'bok', 'SOK': 'sok',
    'RAV': 'rav', 'GP': 'gp', 'DI': 'di', 'CS': 'cs', 'TSP': 'tsp', 'PC': 'pc',
    'FUT': 'fut', 'LRW': 'lrw', 'MOR': 'mor', 'SHM': 'shm', 'EVE': 'eve',
    'ALA': 'ala', 'CON': 'con', 'ARB': 'arb', 'ZEN': 'zen', 'WWK': 'wwk', 'ROE': 'roe',
    'SOM': 'som', 'MBS': 'mbs', 'NPH': 'nph', 'ISD': 'isd', 'DKA': 'dka', 'AVR': 'avr',
    'RTR': 'rtr', 'GTC': 'gtc', 'DGM': 'dgm', 'THS': 'ths', 'BNG': 'bng', 'JOU': 'jou',
    'KTK': 'ktk', 'FRF': 'frf', 'DTK': 'dtk', 'BFZ': 'bfz', 'OGW': 'ogw', 'SOI': 'soi',
    'EMN': 'emn', 'KLD': 'kld', 'AER': 'aer', 'AKH': 'akh', 'HOU': 'hou', 'XLN': 'xln',
    'RIX': 'rix', 'DOM': 'dom', 'GRN': 'grn', 'RNA': 'rna', 'WAR': 'war', 'M20': 'm20',
    'ELD': 'eld', 'THB': 'thb', 'IKO': 'iko', 'M21': 'm21', 'ZNR': 'znr', 'KHM': 'khm',
    'STX': 'stx', 'MH2': 'mh2', 'AFR': 'afr', 'MID': 'mid', 'VOW': 'vow', 'NEO': 'neo',
    'SNC': 'snc', 'NEC': 'nec', 'SIR': 'sir', 'DMU': 'dmu', 'BRO': 'bro', 'ONE': 'one',
    'MAT': 'mat', 'LCI': 'lci', 'OTJ': 'otj', 'TNCC': 'tncc',
    # Special/promotional
    'UNH': 'unh', 'UNM': 'unm', 'UGL': 'ugl',
}

_NAMED_SYMBOLS = {
    'T': 'tap', 'Q': 'untap', 'E': 'e', 'P': 'p',
    'PW': 'planeswalker', 'CHAOS': 'chaos',
    'A': 'acorn', 'TK': 'ticket',
}

def _symbol_to_ms_class(symbol: str) -> str:
    """
    Convert a single Scryfall mana symbol (without braces, uppercased) to
    the Mana library CSS class string.

    Scryfall uses slash notation for hybrid/Phyrexian: W/U, 2/W, W/P.
    The Mana library uses concatenated lowercase: wu, 2w, wp.
    All casting-cost symbols get ms-cost for the colored circle.
    """
    inner = symbol.strip('{}').upper()

    # Named non-mana symbols (tap, untap, energy, etc.)
    if inner in _NAMED_SYMBOLS:
        return f'ms ms-{_NAMED_SYMBOLS[inner]} ms-cost'

    # Generic numeric (0-20) and X, Y, Z, C, S
    if re.match(r'^(\d+|X|Y|Z|C|S)$', inner):
        return f'ms ms-{inner.lower()} ms-cost'

    # Single color
    if inner in ('W', 'U', 'B', 'R', 'G'):
        return f'ms ms-{inner.lower()} ms-cost'

    # Hybrid / Phyrexian: Scryfall format W/U, W/P, 2/W
    if '/' in inner:
        parts = inner.split('/')
        code = ''.join(p.lower() for p in parts)
        return f'ms ms-{code} ms-cost'

    # Fallback
    return f'ms ms-{inner.lower()} ms-cost'


def format_mana_cost(mana_cost: str) -> str:
    """Convert a pure Scryfall mana cost string (e.g. {1}{W}{U}) to mana symbol HTML."""
    if not mana_cost:
        return ''
    symbols = re.findall(r'\{[^}]+\}', mana_cost)
    return ''.join(f'<i class="{_symbol_to_ms_class(s)}"></i>' for s in symbols)


def format_oracle_text(text: str) -> str:
    """Replace {X} tokens inline within oracle text, preserving surrounding words."""
    if not text:
        return ''
    import html as _html
    safe = _html.escape(text)
    return re.sub(
        r'\{([^}]+)\}',
        lambda m: f'<i class="{_symbol_to_ms_class(m.group(0))}"></i>',
        safe
    )

RARITY_CLASS_MAP = {
    'COMMON': 'ss-common',
    'UNCOMMON': 'ss-uncommon',
    'RARE': 'ss-rare',
    'MYTHIC': 'ss-mythic',
    'BONUS': 'ss-mythic',
    'SPECIAL': 'ss-rare',
}

def format_set_symbol(set_code: str, rarity: str = '') -> str:
    """Convert set code to a Keyrune set symbol, optionally coloured by rarity."""
    if not set_code:
        return ''

    lower_code = SET_CODE_MAP.get(set_code.upper(), set_code.lower())
    rarity_class = RARITY_CLASS_MAP.get((rarity or '').upper(), '')

    classes = f'ss ss-{lower_code}'
    if rarity_class:
        classes += f' {rarity_class}'

    return f'<i class="{classes}"></i>'

def format_color_symbols(colors: str) -> str:
    """Convert color array to colored dots."""
    if not colors or colors == '[]':
        return '<span class="color-neutral">Colorless</span>'

    color_map = {'W': '⚪', 'U': '🔵', 'B': '⚫', 'R': '🔴', 'G': '💚'}
    try:
        import json
        color_list = json.loads(colors)
        return ''.join(color_map.get(c, c) for c in color_list)
    except:
        return colors
