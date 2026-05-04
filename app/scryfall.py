import requests
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any
import config

class ScryfallClient:
    def __init__(self, db):
        self.db = db
        self.session = requests.Session()
        self.base_url = config.SCRYFALL_API_BASE
        self.last_request_time = 0
        self.rate_limit_ms = config.SCRYFALL_RATE_LIMIT_MS

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
        return {
            'scryfall_id': card_data.get('id'),
            'name': card_data.get('name', ''),
            'set_code': card_data.get('set', '').upper(),
            'set_name': card_data.get('set_name', ''),
            'collector_number': card_data.get('collector_number', ''),
            'mana_cost': card_data.get('mana_cost', ''),
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
        }

    def get_card_by_name_and_set(self, name: str, set_code: str) -> Optional[Dict[str, Any]]:
        cache_key = f'named:{name.lower()}:{set_code.lower()}'
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        self._rate_limit()
        try:
            endpoint = f'{self.base_url}/cards/named'
            params = {'exact': name, 'set': set_code}
            response = self.session.get(endpoint, params=params, timeout=10)

            if response.status_code == 404:
                self._set_cache(cache_key, {'error': f'Card {name} not found in set {set_code}'})
                return None

            if response.status_code == 429:
                return None

            response.raise_for_status()
            card_data = response.json()
            formatted = self._format_card(card_data)
            self._set_cache(cache_key, formatted)
            return formatted

        except requests.exceptions.RequestException:
            return None

    def get_card_by_name_fuzzy(self, name: str) -> Optional[Dict[str, Any]]:
        cache_key = f'fuzzy:{name.lower()}'
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        self._rate_limit()
        try:
            endpoint = f'{self.base_url}/cards/named'
            params = {'fuzzy': name}
            response = self.session.get(endpoint, params=params, timeout=10)

            if response.status_code == 404:
                self._set_cache(cache_key, {'error': f'Card {name} not found'})
                return None

            if response.status_code == 429:
                return None

            response.raise_for_status()
            card_data = response.json()
            formatted = self._format_card(card_data)
            self._set_cache(cache_key, formatted)
            return formatted

        except requests.exceptions.RequestException:
            return None

    def enrich_card(self, collection_id: int, name: str, edition: str, set_code: str, max_retries: int = 1) -> bool:
        for attempt in range(max_retries):
            try:
                card_data = self.get_card_by_name_and_set(name, set_code)
                if not card_data or 'error' in card_data:
                    card_data = self.get_card_by_name_fuzzy(name)

                if not card_data or 'error' in card_data:
                    if attempt < max_retries - 1:
                        time.sleep(0.5)
                        continue
                    return False

                cursor = self.db.execute(
                    'SELECT id FROM cards WHERE scryfall_id = ?',
                    (card_data['scryfall_id'],)
                )
                row = cursor.fetchone()
                card_id = row[0] if row else None

                if not card_id:
                    now = datetime.utcnow().isoformat() + 'Z'
                    card_data['enriched_at'] = now
                    cursor = self.db.execute(
                        '''INSERT INTO cards
                           (scryfall_id, name, set_code, set_name, collector_number, mana_cost, cmc,
                            colors, color_identity, type_line, oracle_text, power, toughness, rarity,
                            legalities, image_uri_normal, image_uri_small, enriched_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (card_data['scryfall_id'], card_data['name'], card_data['set_code'],
                         card_data['set_name'], card_data['collector_number'], card_data['mana_cost'],
                         card_data['cmc'], card_data['colors'], card_data['color_identity'],
                         card_data['type_line'], card_data['oracle_text'], card_data['power'],
                         card_data['toughness'], card_data['rarity'], card_data['legalities'],
                         card_data['image_uri_normal'], card_data['image_uri_small'], now)
                    )
                    card_id = cursor.lastrowid

                self.db.execute(
                    'UPDATE collection SET card_id = ?, scryfall_id = ? WHERE id = ?',
                    (card_id, card_data['scryfall_id'], collection_id)
                )
                self.db.commit()
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
                return False

        return False
