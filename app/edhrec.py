"""
EDHREC client — fetches and caches commander recommendation data.

Usage:
    from app.edhrec import get_edhrec_data, CACHE_TTL_DAYS
    data = get_edhrec_data(db, commander_name)
    # data is None if fetch fails, otherwise a dict with keys:
    #   slug, num_decks, cards (list of dicts with name/scryfall_id/tag/synergy/inclusion)
"""
import json
import time
import re
import unicodedata
import requests
from datetime import datetime, timedelta

CACHE_TTL_DAYS = 30
_SESSION = None


def _session():
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers['User-Agent'] = 'mtg-manager/1.0 (personal collection tool)'
    return _SESSION


def _name_to_slug(name: str) -> str:
    """Convert a card name to an EDHREC URL slug."""
    # Handle DFCs — EDHREC uses only the front face name
    name = name.split(' // ')[0]
    # Transliterate accented characters to ASCII (é -> e, ñ -> n, …) before
    # slugifying — EDHREC's own slugs do this, so without it any accented
    # name (Bartolomé, Krenko's cousin whoever) silently drops the letter
    # entirely and 403s against the real URL instead of matching it.
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    slug = name.lower()
    slug = re.sub(r"[',.]", '', slug)
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug


def _fetch_edhrec(slug: str) -> dict | None:
    """Fetch raw EDHREC JSON for a commander slug. Returns None on failure."""
    url = f'https://json.edhrec.com/pages/commanders/{slug}.json'
    try:
        resp = _session().get(url, timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _parse_edhrec(raw: dict) -> list[dict]:
    """Extract a flat list of card dicts from raw EDHREC JSON."""
    cards = []
    try:
        cardlists = raw['container']['json_dict']['cardlists']
    except (KeyError, TypeError):
        return cards

    for cl in cardlists:
        tag = cl.get('tag', 'unknown')
        for cv in cl.get('cardviews', []):
            cards.append({
                'name':            cv.get('name', ''),
                'sanitized':       cv.get('sanitized', ''),
                'scryfall_id':     cv.get('id', ''),
                'tag':             tag,
                'synergy':         float(cv.get('synergy', 0) or 0),
                'inclusion':       int(cv.get('inclusion', 0) or 0),
                'num_decks':       int(cv.get('num_decks', 0) or 0),
                'potential_decks': int(cv.get('potential_decks', 0) or 0),
            })
    return cards


def _store(db, slug: str, raw: dict, cards: list[dict]):
    """Persist fetched data to edhrec_cache and edhrec_cards."""
    num_decks = 0
    try:
        num_decks = raw['container']['json_dict']['card']['num_decks']
    except (KeyError, TypeError):
        pass

    now = datetime.utcnow().isoformat()
    db.execute('DELETE FROM edhrec_cards WHERE slug = ?', [slug])
    db.execute(
        'INSERT OR REPLACE INTO edhrec_cache (slug, data, num_decks, fetched_at) VALUES (?,?,?,?)',
        [slug, json.dumps(raw), num_decks, now]
    )
    for c in cards:
        db.execute(
            '''INSERT INTO edhrec_cards
               (slug, scryfall_id, name, sanitized, tag, synergy, inclusion, num_decks, potential_decks)
               VALUES (?,?,?,?,?,?,?,?,?)''',
            [slug, c['scryfall_id'], c['name'], c['sanitized'],
             c['tag'], c['synergy'], c['inclusion'], c['num_decks'], c['potential_decks']]
        )
    db.commit()


def _is_stale(fetched_at: str) -> bool:
    try:
        age = datetime.utcnow() - datetime.fromisoformat(fetched_at)
        return age > timedelta(days=CACHE_TTL_DAYS)
    except Exception:
        return True


def get_commander_combos(db, commander_name: str, force_refresh: bool = False) -> list[dict]:
    """
    Return up to 100 combos for a commander from EDHREC, cached in combo_cache.
    Each combo dict: comboId, href, cards (list of {name, scryfall_id}),
                     results (list of str), count, bracket.
    Returns [] on failure or if no combo data.
    """
    slug = _name_to_slug(commander_name)

    if not force_refresh:
        row = db.execute(
            'SELECT combos_json, fetched_at FROM combo_cache WHERE slug=?', [slug]
        ).fetchone()
        if row and not _is_stale(row['fetched_at']):
            return json.loads(row['combos_json'])

    url = f'https://json.edhrec.com/pages/combos/{slug}.json'
    try:
        resp = _session().get(url, timeout=15)
        if resp.status_code != 200:
            return []
        raw = resp.json()
    except Exception:
        return []

    combos = []
    try:
        cardlists = raw['container']['json_dict']['cardlists']
    except (KeyError, TypeError):
        cardlists = []

    for entry in cardlists:
        combo = entry.get('combo', {})
        if not combo:
            continue
        combos.append({
            'comboId': combo.get('comboId', ''),
            'href':    entry.get('href', ''),
            'cards':   [{'name': cv['name'], 'scryfall_id': cv.get('id', '')}
                        for cv in entry.get('cardviews', [])],
            'results': combo.get('results', []),
            'count':   combo.get('count', 0),
            'bracket': (combo.get('comboVote') or {}).get('bracket', ''),
        })

    now = datetime.utcnow().isoformat()
    db.execute(
        'INSERT OR REPLACE INTO combo_cache (slug, combos_json, fetched_at) VALUES (?,?,?)',
        [slug, json.dumps(combos), now]
    )
    db.commit()
    return combos


def fetch_combo_detail(db, combo_id: str, href: str) -> dict | None:
    """
    Fetch prerequisites + steps for a single combo, cached indefinitely.
    Returns dict with keys: prerequisites (list of str), steps (list of str), results (list of str)
    """
    row = db.execute(
        'SELECT detail_json FROM combo_details_cache WHERE combo_id=?', [combo_id]
    ).fetchone()
    if row:
        return json.loads(row['detail_json'])

    # href looks like /combos/golgari/2973-3966 — prepend /pages and append .json
    url = f'https://json.edhrec.com/pages{href}.json'
    try:
        resp = _session().get(url, timeout=15)
        if resp.status_code != 200:
            return None
        raw = resp.json()
    except Exception:
        return None

    combo = raw.get('combo', {})
    detail = {
        'prerequisites': [p['description'] for p in combo.get('prerequisites', [])],
        'steps':         combo.get('steps', []),
        'results':       combo.get('results', []),
    }
    db.execute(
        'INSERT OR REPLACE INTO combo_details_cache (combo_id, detail_json, fetched_at) VALUES (?,?,?)',
        [combo_id, json.dumps(detail), datetime.utcnow().isoformat()]
    )
    db.commit()
    return detail


def get_edhrec_data(db, commander_name: str, force_refresh: bool = False) -> dict | None:
    """
    Return EDHREC recommendation data for a commander, using cache if fresh.
    Returns dict with keys: slug, num_decks, fetched_at, cards[]
    Returns None if EDHREC has no data for this commander.
    """
    slug = _name_to_slug(commander_name)

    # Check cache
    if not force_refresh:
        row = db.execute(
            'SELECT slug, num_decks, fetched_at FROM edhrec_cache WHERE slug = ?', [slug]
        ).fetchone()
        if row and not _is_stale(row['fetched_at']):
            cards = db.execute(
                'SELECT * FROM edhrec_cards WHERE slug = ? ORDER BY synergy DESC',
                [slug]
            ).fetchall()
            return {
                'slug':       row['slug'],
                'num_decks':  row['num_decks'],
                'fetched_at': row['fetched_at'],
                'cards':      [dict(c) for c in cards],
            }

    # Fetch from EDHREC
    raw = _fetch_edhrec(slug)
    if raw is None:
        return None

    cards = _parse_edhrec(raw)
    _store(db, slug, raw, cards)

    cards_out = db.execute(
        'SELECT * FROM edhrec_cards WHERE slug = ? ORDER BY synergy DESC', [slug]
    ).fetchall()

    num_decks = 0
    try:
        num_decks = raw['container']['json_dict']['card']['num_decks']
    except (KeyError, TypeError):
        pass

    return {
        'slug':       slug,
        'num_decks':  num_decks,
        'fetched_at': datetime.utcnow().isoformat(),
        'cards':      [dict(c) for c in cards_out],
    }
