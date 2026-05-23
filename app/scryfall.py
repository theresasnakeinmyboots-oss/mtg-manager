import requests
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any
import config

# Map Deckbox/CSV full edition names → Scryfall set codes
EDITION_TO_SET_CODE = {
    "Adventures in the Forgotten Realms": "afr",
    "Aetherdrift Commander": "dft",
    "Alara Reborn": "arb",
    "Anthologies": "ath",
    "Apocalypse": "apc",
    "Archenemy": "arc",
    "Archenemy: Nicol Bolas": "e01",
    "Avacyn Restored": "avr",
    "Battle for Zendikar": "bfz",
    "Battlebond": "bbd",
    "Bloomburrow": "blb",
    "Champions of Kamigawa": "chk",
    "Commander": "cmd",
    "Commander 2013": "c13",
    "Commander 2016": "c16",
    "Commander 2017": "c17",
    "Commander 2018": "c18",
    "Commander 2020": "c20",
    "Commander 2021": "c21",
    "Commander Anthology": "cma",
    "Commander Anthology Volume II": "cm2",
    "Commander Legends": "cmr",
    "Commander Legends: Battle for Baldur's Gate": "clb",
    "Commander Masters": "cmm",
    "Conflux": "con",
    "Conspiracy": "cns",
    "Conspiracy: Take the Crown": "cn2",
    "Core Set 2019": "m19",
    "Core Set 2019 Promos": "pm19",
    "Core Set 2020": "m20",
    "Core Set 2021": "m21",
    "Dark Ascension": "dka",
    "Darksteel": "dst",
    "Dissension": "dis",
    "Doctor Who": "who",
    "Dominaria": "dom",
    "Dominaria United": "dmu",
    "Dominaria United Commander": "dmc",
    "Double Masters": "2xm",
    "Double Masters 2022": "2x2",
    "Dragons of Tarkir": "dtk",
    "Duel Decks Anthology: Divine vs. Demonic": "ddd",
    "Duel Decks: Ajani vs. Nicol Bolas": "dvd",
    "Duel Decks: Elves vs. Inventors": "evi",
    "Duel Decks: Merfolk vs. Goblins": "ddm",
    "Duel Decks: Zendikar vs. Eldrazi": "dze",
    "Duels of the Planeswalkers": "dpa",
    "Duskmourn: House of Horror Promos": "pdsk",
    "Edge of Eternities Commander": "eoc",
    "Eternal Masters": "ema",
    "Eventide": "eve",
    "Exodus": "exo",
    "Fate Reforged": "frf",
    "Fifth Edition": "5ed",
    "Final Fantasy": "fft",
    "Final Fantasy Commander": "ffc",
    "Forgotten Realms Commander": "afc",
    "Foundations Commander": "fdc",
    "GRN Guild Kit": "gk1",
    "Game Night": "gnt",
    "Game Night 2019": "gn2",
    "Gatecrash": "gtc",
    "Global Series Jiang Yanggu & Mu Yanling": "gs1",
    "Guildpact": "gpt",
    "Guilds of Ravnica": "grn",
    "Hour of Devastation": "hou",
    "Iconic Masters": "ima",
    "Ikoria: Lair of Behemoths": "iko",
    "Innistrad Remastered": "inr",
    "Innistrad: Crimson Vow": "vow",
    "Innistrad: Midnight Hunt": "mid",
    "Invasion": "inv",
    "Ixalan": "xln",
    "Journey into Nyx": "jou",
    "Judgment": "jud",
    "Jumpstart": "jmp",
    "Jumpstart 2022": "j22",
    "Kaladesh": "kld",
    "Kaldheim": "khm",
    "Kaldheim Commander": "khc",
    "Kamigawa: Neon Dynasty": "neo",
    "Kamigawa: Neon Dynasty Promos": "pneo",
    "Khans of Tarkir": "ktk",
    "Legions": "lgn",
    "Lorwyn": "lrw",
    "Lorwyn Eclipsed": "lor",
    "Lorwyn Eclipsed Commander": "ecc",
    "M19 Gift Pack": "g18",
    "Magic 2011": "m11",
    "Magic 2012": "m12",
    "Magic 2013": "m13",
    "Magic 2014 Core Set": "m14",
    "Magic 2015 Core Set": "m15",
    "Magic Origins": "ori",
    "MagicFest 2026": "mf26",
    "March of the Machine": "mom",
    "March of the Machine Commander": "moc",
    "March of the Machine: The Aftermath": "mat",
    "Masters 25": "a25",
    "Mercadian Masques": "mmq",
    "Mirage": "mir",
    "Mirrodin": "mrd",
    "Modern Horizons": "mh1",
    "Modern Horizons 2": "mh2",
    "Modern Masters 2015 Edition": "mm2",
    "Morningtide": "mor",
    "Murders at Karlov Manor": "mkm",
    "Murders at Karlov Manor Commander": "mkc",
    "Mystery Booster 2": "mb2",
    "Nemesis": "nem",
    "Neon Dynasty Commander": "nec",
    "New Capenna Commander": "ncc",
    "New Phyrexia": "nph",
    "Odyssey": "ody",
    "Outlaws of Thunder Junction": "otj",
    "Outlaws of Thunder Junction Commander": "otc",
    "Phyrexia: All Will Be One": "one",
    "Planar Chaos": "plc",
    "Planechase": "hop",
    "Planechase Anthology": "pca",
    "Planeshift": "pls",
    "Portal": "por",
    "Portal Second Age": "p02",
    "Premium Deck Series: Slivers": "pds",
    "Prophecy": "pcy",
    "RNA Guild Kit": "gk2",
    "Ravnica Allegiance": "rna",
    "Ravnica: City of Guilds": "rav",
    "Ravnica: Clue Edition": "clu",
    "Return to Ravnica": "rtr",
    "Rise of the Eldrazi": "roe",
    "Rivals of Ixalan": "rix",
    "Scars of Mirrodin": "som",
    "Scourge": "scg",
    "Secret Lair Drop": "sld",
    "Secrets of Strixhaven": "sos",
    "Secrets of Strixhaven Commander": "soc",
    "Secrets of Strixhaven Mystical Archive": "soa",
    "Shadowmoor": "shm",
    "Shards of Alara": "ala",
    "Starter Commander Decks": "scd",
    "Streets of New Capenna": "snc",
    "Strixhaven: School of Mages": "stx",
    "Stronghold": "sth",
    "Tarkir: Dragonstorm": "tdt",
    "Tarkir: Dragonstorm Commander": "tdc",
    "Teenage Mutant Ninja Turtles": "tmt",
    "Teenage Mutant Ninja Turtles Eternal": "tme",
    "Teenage Mutant Ninja Turtles Source Material": "tms",
    "Tempest": "tmp",
    "Tenth Edition": "10e",
    "The Brothers' War": "bro",
    "The Brothers' War Commander": "brc",
    "The List": "plst",
    "The Lost Caverns of Ixalan": "lci",
    "Theros": "ths",
    "Theros Beyond Death": "thb",
    "Throne of Eldraine": "eld",
    "Time Spiral": "tsp",
    "Torment": "tor",
    "Universes Within": "slx",
    "Urza's Legacy": "ulg",
    "Urza's Saga": "usg",
    "Visions": "vis",
    "War of the Spark": "war",
    "Weatherlight": "wth",
    "Worldwake": "wwk",
    "Zendikar": "zen",
    "Zendikar Rising": "znr",
    "Zendikar Rising Commander": "znc",
}

def _to_float(val):
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


class ScryfallClient:
    def __init__(self, db):
        self.db = db
        self.session = requests.Session()
        self.base_url = config.SCRYFALL_API_BASE
        self.last_request_time = 0
        self.rate_limit_ms = config.SCRYFALL_RATE_LIMIT_MS
        # Build set name → code map from bulk data, overriding hardcoded entries
        self._set_name_map = dict(EDITION_TO_SET_CODE)
        try:
            rows = db.execute(
                'SELECT DISTINCT set_name, set_code FROM scryfall_bulk'
            ).fetchall()
            for r in rows:
                self._set_name_map[r['set_name']] = r['set_code'].lower()
        except Exception:
            pass

    def _rate_limit(self):
        elapsed = (time.time() - self.last_request_time) * 1000
        if elapsed < self.rate_limit_ms:
            time.sleep((self.rate_limit_ms - elapsed) / 1000)
        self.last_request_time = time.time()

    def _get_cached(self, cache_key: str) -> Optional[Dict[str, Any]]:
        cursor = self.db.execute(
            'SELECT response FROM scryfall_cache WHERE cache_key = ?',
            (cache_key,)
        )
        row = cursor.fetchone()
        return json.loads(row[0]) if row else None

    def _set_cache(self, cache_key: str, response: Dict[str, Any]):
        now = datetime.utcnow().isoformat() + 'Z'
        self.db.execute(
            'INSERT OR REPLACE INTO scryfall_cache (cache_key, response, fetched_at) VALUES (?, ?, ?)',
            (cache_key, json.dumps(response), now)
        )
        self.db.commit()

    def _format_card(self, card_data: Dict[str, Any]) -> Dict[str, Any]:
        faces = card_data.get('card_faces', [])
        mana_cost = card_data.get('mana_cost', '') or (faces[0].get('mana_cost', '') if faces else '')
        return {
            'scryfall_id': card_data.get('id'),
            'name': card_data.get('name', ''),
            'set_code': card_data.get('set', '').upper(),
            'set_name': card_data.get('set_name', ''),
            'collector_number': card_data.get('collector_number', ''),
            'mana_cost': mana_cost,
            'cmc': float(card_data.get('cmc', 0)),
            'colors': json.dumps(card_data.get('colors', [])),
            'color_identity': json.dumps(card_data.get('color_identity', [])),
            'type_line': card_data.get('type_line', ''),
            'oracle_text': card_data.get('oracle_text', ''),
            'power': card_data.get('power'),
            'toughness': card_data.get('toughness'),
            'rarity': card_data.get('rarity', '').upper(),
            'legalities': json.dumps(card_data.get('legalities', {})),
            'image_uri_normal': card_data.get('image_uris', {}).get('normal'),
            'image_uri_small': card_data.get('image_uris', {}).get('small'),
            'flavor_text': card_data.get('flavor_text', ''),
            'artist': card_data.get('artist', ''),
            'price_usd': _to_float(card_data.get('prices', {}).get('usd')),
            'price_usd_foil': _to_float(card_data.get('prices', {}).get('usd_foil')),
            'price_eur': _to_float(card_data.get('prices', {}).get('eur')),
            'price_eur_foil': _to_float(card_data.get('prices', {}).get('eur_foil')),
        }

    def _get(self, url: str, params: dict) -> Optional[requests.Response]:
        """Rate-limited GET with automatic 429 back-off (up to 3 retries)."""
        for attempt in range(3):
            self._rate_limit()
            try:
                response = self.session.get(url, params=params, timeout=10)
                if response.status_code == 429:
                    wait = int(response.headers.get('Retry-After', 10))
                    time.sleep(max(wait, 5))
                    continue
                return response
            except requests.exceptions.RequestException:
                return None
        return None

    def get_card_from_bulk(self, name: str, set_code: str, collector_number: str = None) -> Optional[Dict[str, Any]]:
        """Look up a card in the local bulk table. Returns formatted card dict or None."""
        # Most specific: name + set + collector number
        if collector_number:
            row = self.db.execute(
                'SELECT * FROM scryfall_bulk WHERE name = ? AND set_code = ? AND collector_number = ? LIMIT 1',
                (name, set_code.upper(), str(collector_number))
            ).fetchone()
            if row:
                return self._format_bulk_row(dict(row))
        # name + set
        row = self.db.execute(
            'SELECT * FROM scryfall_bulk WHERE name = ? AND set_code = ? LIMIT 1',
            (name, set_code.upper())
        ).fetchone()
        if not row:
            # Fallback: any printing with this name
            row = self.db.execute(
                'SELECT * FROM scryfall_bulk WHERE name = ? LIMIT 1', (name,)
            ).fetchone()
        if not row:
            return None
        return self._format_bulk_row(dict(row))

    def _format_bulk_row(self, row: dict) -> Dict[str, Any]:
        return {
            'scryfall_id': row['scryfall_id'],
            'name': row['name'],
            'set_code': row['set_code'],
            'set_name': row['set_name'],
            'collector_number': row['collector_number'],
            'mana_cost': row['mana_cost'],
            'cmc': row['cmc'],
            'colors': row['colors'],
            'color_identity': row['color_identity'],
            'type_line': row['type_line'],
            'oracle_text': row['oracle_text'],
            'flavor_text': row['flavor_text'] or '',
            'power': row['power'],
            'toughness': row['toughness'],
            'rarity': row['rarity'],
            'legalities': row['legalities'],
            'image_uri_normal': row['image_uri_normal'],
            'image_uri_small': row['image_uri_small'],
            'artist': row['artist'] or '',
            'price_usd': row['price_usd'],
            'price_usd_foil': row['price_usd_foil'],
            'price_eur': row['price_eur'],
            'price_eur_foil': row['price_eur_foil'],
        }

    def _bulk_available(self) -> bool:
        try:
            count = self.db.execute('SELECT COUNT(*) FROM scryfall_bulk').fetchone()[0]
            return count > 0
        except Exception:
            return False

    def get_card_by_name_and_set(self, name: str, set_code: str) -> Optional[Dict[str, Any]]:
        cache_key = f'named:{name.lower()}:{set_code.lower()}'
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        response = self._get(f'{self.base_url}/cards/named', {'exact': name, 'set': set_code})
        if response is None:
            return None

        if response.status_code == 404:
            self._set_cache(cache_key, {'error': f'Not found in set {set_code}'})
            return None

        if not response.ok:
            return None

        formatted = self._format_card(response.json())
        self._set_cache(cache_key, formatted)
        return formatted

    def get_card_by_name_fuzzy(self, name: str) -> Optional[Dict[str, Any]]:
        cache_key = f'fuzzy:{name.lower()}'
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        response = self._get(f'{self.base_url}/cards/named', {'fuzzy': name})
        if response is None:
            return None

        if response.status_code == 404:
            self._set_cache(cache_key, {'error': f'Not found on Scryfall'})
            return None

        if not response.ok:
            return None

        formatted = self._format_card(response.json())
        self._set_cache(cache_key, formatted)
        return formatted

    def enrich_card(self, collection_id: int, name: str, edition: str, set_code: str, max_retries: int = 1, force_update: bool = False, collector_number: str = None) -> tuple[bool, str]:
        # Resolve full edition name → Scryfall set code if needed
        resolved_code = self._set_name_map.get(edition, set_code)
        last_error = 'Not found on Scryfall'

        # Try bulk table first (fast, no rate limits)
        if self._bulk_available():
            card_data = self.get_card_from_bulk(name, resolved_code, collector_number=collector_number)
            if card_data:
                try:
                    return self._write_card(card_data, collection_id, force_update), ''
                except Exception as e:
                    last_error = str(e)
            else:
                last_error = f'Not in bulk data for {resolved_code}'
            # Don't fall through to API during bulk imports — caller can use force_update for live re-fetch
            if not force_update:
                return False, last_error

        for attempt in range(max_retries):
            try:
                card_data = self.get_card_by_name_and_set(name, resolved_code)
                if not card_data or 'error' in card_data:
                    last_error = f'Not found in set {resolved_code}, trying fuzzy…'
                    card_data = self.get_card_by_name_fuzzy(name)

                if not card_data or 'error' in card_data:
                    last_error = card_data.get('error', 'Not found on Scryfall') if card_data else 'Not found on Scryfall'
                    if attempt < max_retries - 1:
                        time.sleep(0.5)
                        continue
                    return False, last_error

                return self._write_card(card_data, collection_id, force_update), ''
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
                return False, last_error

        return False, last_error

    def _write_card(self, card_data: Dict[str, Any], collection_id: int, force_update: bool = False) -> bool:
        now = datetime.utcnow().isoformat() + 'Z'
        cursor = self.db.execute(
            'SELECT id FROM cards WHERE scryfall_id = ?', (card_data['scryfall_id'],)
        )
        row = cursor.fetchone()
        card_id = row[0] if row else None

        # Check if the existing cards row is a stub (no enriched_at) that needs filling in
        is_stub = card_id and not self.db.execute(
            'SELECT enriched_at FROM cards WHERE id = ? AND enriched_at IS NOT NULL', (card_id,)
        ).fetchone()

        if card_id and (force_update or is_stub):
            self.db.execute(
                '''UPDATE cards SET name=?, set_code=?, set_name=?, collector_number=?,
                   mana_cost=?, cmc=?, colors=?, color_identity=?, type_line=?,
                   oracle_text=?, power=?, toughness=?, rarity=?, legalities=?,
                   image_uri_normal=?, image_uri_small=?, flavor_text=?, artist=?,
                   price_usd=?, price_usd_foil=?, price_eur=?, price_eur_foil=?,
                   prices_updated_at=?, enriched_at=? WHERE id=?''',
                (card_data['name'], card_data['set_code'], card_data['set_name'],
                 card_data['collector_number'], card_data['mana_cost'], card_data['cmc'],
                 card_data['colors'], card_data['color_identity'], card_data['type_line'],
                 card_data['oracle_text'], card_data['power'], card_data['toughness'],
                 card_data['rarity'], card_data['legalities'], card_data['image_uri_normal'],
                 card_data['image_uri_small'], card_data.get('flavor_text', ''),
                 card_data.get('artist', ''), card_data.get('price_usd'),
                 card_data.get('price_usd_foil'), card_data.get('price_eur'),
                 card_data.get('price_eur_foil'), now, now, card_id)
            )
        elif not card_id:
            cursor = self.db.execute(
                '''INSERT INTO cards
                   (scryfall_id, name, set_code, set_name, collector_number, mana_cost, cmc,
                    colors, color_identity, type_line, oracle_text, power, toughness, rarity,
                    legalities, image_uri_normal, image_uri_small, flavor_text, artist,
                    price_usd, price_usd_foil, price_eur, price_eur_foil, prices_updated_at, enriched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (card_data['scryfall_id'], card_data['name'], card_data['set_code'],
                 card_data['set_name'], card_data['collector_number'], card_data['mana_cost'],
                 card_data['cmc'], card_data['colors'], card_data['color_identity'],
                 card_data['type_line'], card_data['oracle_text'], card_data['power'],
                 card_data['toughness'], card_data['rarity'], card_data['legalities'],
                 card_data['image_uri_normal'], card_data['image_uri_small'],
                 card_data.get('flavor_text', ''), card_data.get('artist', ''),
                 card_data.get('price_usd'), card_data.get('price_usd_foil'),
                 card_data.get('price_eur'), card_data.get('price_eur_foil'), now, now)
            )
            card_id = cursor.lastrowid

        self.db.execute(
            'UPDATE collection SET card_id = ?, scryfall_id = ? WHERE id = ?',
            (card_id, card_data['scryfall_id'], collection_id)
        )
        self.db.commit()
        return True
