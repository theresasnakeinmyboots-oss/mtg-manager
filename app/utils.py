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

# Mana symbol mapping
MANA_SYMBOL_MAP = {
    '{0}': '0', '{1}': '1', '{2}': '2', '{3}': '3', '{4}': '4', '{5}': '5',
    '{6}': '6', '{7}': '7', '{8}': '8', '{9}': '9', '{10}': '10',
    '{W}': 'w', '{U}': 'u', '{B}': 'b', '{R}': 'r', '{G}': 'g',
    '{WU}': 'wu', '{WB}': 'wb', '{WR}': 'wr', '{WG}': 'wg',
    '{UB}': 'ub', '{UR}': 'ur', '{UG}': 'ug',
    '{BR}': 'br', '{BG}': 'bg', '{RG}': 'rg',
    '{WUB}': 'wub', '{WUR}': 'wur', '{WUG}': 'wug', '{WBR}': 'wbr',
    '{WBG}': 'wbg', '{WRG}': 'wrg', '{UBR}': 'ubr', '{UBG}': 'ubg',
    '{URG}': 'urg', '{BRG}': 'brg', '{WUBRG}': 'wubrg',
    '{X}': 'x', '{S}': 's', '{P}': 'p',
}

def format_mana_cost(mana_cost: str) -> str:
    """Convert mana cost string to HTML with Keyrune symbols."""
    if not mana_cost:
        return ''

    # Split by mana symbols (e.g., "{W}{U}{2}" -> ["{W}", "{U}", "{2}"])
    symbols = re.findall(r'\{[^}]+\}', mana_cost.upper())
    html = []

    for symbol in symbols:
        keyrune_code = MANA_SYMBOL_MAP.get(symbol.upper())
        if keyrune_code:
            html.append(f'<i class="ms ms-{keyrune_code}"></i>')
        else:
            html.append(symbol)

    return ''.join(html)

def format_set_symbol(set_code: str) -> str:
    """Convert set code to Keyrune set symbol."""
    if not set_code:
        return ''

    upper_code = set_code.upper()
    keyrune_code = SET_CODE_MAP.get(upper_code)

    if keyrune_code:
        return f'<i class="ss ss-{keyrune_code}"></i>'

    # Fallback: try lowercase version for unmapped codes
    lower_code = upper_code.lower()
    return f'<i class="ss ss-{lower_code}"></i>'

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
