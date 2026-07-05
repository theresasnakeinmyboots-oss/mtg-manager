"""Shared MTG domain constants.

Basic land names were defined (in slightly different forms) in decks.py,
deck_importer.py, and auto_fill.py. Centralised here. Both a title-case set
(matching card names as stored) and a lowercase set (for case-insensitive
membership tests) are provided so callers don't each re-lowercase.
"""

BASIC_LANDS = {
    'Plains', 'Island', 'Swamp', 'Mountain', 'Forest', 'Wastes',
    'Snow-Covered Plains', 'Snow-Covered Island', 'Snow-Covered Swamp',
    'Snow-Covered Mountain', 'Snow-Covered Forest',
}

BASIC_LANDS_LOWER = {n.lower() for n in BASIC_LANDS}

# Single-colour basic land for each WUBRG colour (used when filling mana bases).
COLOR_BASIC = {'W': 'Plains', 'U': 'Island', 'B': 'Swamp', 'R': 'Mountain', 'G': 'Forest'}
