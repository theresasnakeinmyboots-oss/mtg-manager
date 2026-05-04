import pytest
import sqlite3
import json
import time
from unittest.mock import patch, MagicMock
from app.scryfall import ScryfallClient
from app.database import init_db
from pathlib import Path
import os
import tempfile

@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        import config
        original_data_dir = config.DATA_DIR
        original_db_path = config.DB_PATH
        config.DATA_DIR = Path(tmpdir)
        config.DB_PATH = Path(tmpdir) / 'test.db'
        init_db()
        db = sqlite3.connect(str(config.DB_PATH))
        db.row_factory = sqlite3.Row
        yield db
        db.close()
        config.DATA_DIR = original_data_dir
        config.DB_PATH = original_db_path

@pytest.fixture
def scryfall_client(temp_db):
    return ScryfallClient(temp_db)

def test_format_card(scryfall_client):
    card_data = {
        'id': '1234-5678-9abc-def0',
        'name': 'Black Lotus',
        'set': 'LEA',
        'set_name': 'Limited Edition Alpha',
        'collector_number': '1',
        'mana_cost': '0',
        'cmc': 0,
        'colors': ['B'],
        'color_identity': ['B'],
        'type_line': 'Artifact',
        'oracle_text': 'Tap, Sacrifice Black Lotus: Add three mana of any one color to your mana pool.',
        'power': None,
        'toughness': None,
        'rarity': 'rare',
        'legalities': {'standard': 'banned', 'modern': 'banned'},
        'image_uris': {'normal': 'http://example.com/normal.jpg', 'small': 'http://example.com/small.jpg'}
    }

    formatted = scryfall_client._format_card(card_data)
    assert formatted['scryfall_id'] == '1234-5678-9abc-def0'
    assert formatted['name'] == 'Black Lotus'
    assert formatted['set_code'] == 'LEA'
    assert formatted['colors'] == '["B"]'
    assert formatted['rarity'] == 'RARE'

def test_cache_hit(scryfall_client, temp_db):
    cache_key = 'named:black lotus:lea'
    cached_data = {'name': 'Black Lotus', 'set_code': 'LEA', 'scryfall_id': '1234'}

    temp_db.execute(
        'INSERT INTO scryfall_cache (cache_key, response, fetched_at) VALUES (?, ?, ?)',
        (cache_key, json.dumps(cached_data), '2026-05-04T00:00:00Z')
    )
    temp_db.commit()

    result = scryfall_client._get_cached(cache_key)
    assert result == cached_data

def test_rate_limiting(scryfall_client):
    scryfall_client.last_request_time = time.time()
    start = time.time()
    scryfall_client._rate_limit()
    elapsed = (time.time() - start) * 1000
    assert elapsed >= scryfall_client.rate_limit_ms - 5

@patch('app.scryfall.requests.Session.get')
def test_get_card_by_name_and_set_success(mock_get, scryfall_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'id': '1234-5678',
        'name': 'Black Lotus',
        'set': 'LEA',
        'set_name': 'Limited Edition Alpha',
        'collector_number': '1',
        'mana_cost': '0',
        'cmc': 0,
        'colors': [],
        'color_identity': [],
        'type_line': 'Artifact',
        'oracle_text': 'Test',
        'rarity': 'rare',
        'legalities': {},
        'image_uris': {'normal': 'http://example.com/normal.jpg', 'small': 'http://example.com/small.jpg'}
    }
    mock_get.return_value = mock_response

    result = scryfall_client.get_card_by_name_and_set('Black Lotus', 'LEA')
    assert result is not None
    assert result['name'] == 'Black Lotus'
    assert result['scryfall_id'] == '1234-5678'

    cursor = scryfall_client.db.execute('SELECT * FROM scryfall_cache WHERE cache_key = ?', ('named:black lotus:lea',))
    cached = cursor.fetchone()
    assert cached is not None

@patch('app.scryfall.requests.Session.get')
def test_get_card_by_name_and_set_not_found(mock_get, scryfall_client):
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_get.return_value = mock_response

    result = scryfall_client.get_card_by_name_and_set('Nonexistent Card', 'LEA')
    assert result is None

@patch('app.scryfall.requests.Session.get')
def test_get_card_by_name_fuzzy(mock_get, scryfall_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'id': '5678-1234',
        'name': 'Lightning Bolt',
        'set': 'LEA',
        'set_name': 'Limited Edition Alpha',
        'collector_number': '39',
        'mana_cost': '{R}',
        'cmc': 1,
        'colors': ['R'],
        'color_identity': ['R'],
        'type_line': 'Instant',
        'oracle_text': 'Lightning Bolt deals 3 damage to any target.',
        'rarity': 'common',
        'legalities': {},
        'image_uris': {'normal': 'http://example.com/normal.jpg', 'small': 'http://example.com/small.jpg'}
    }
    mock_get.return_value = mock_response

    result = scryfall_client.get_card_by_name_fuzzy('lightning bolt')
    assert result is not None
    assert result['name'] == 'Lightning Bolt'

@patch('app.scryfall.requests.Session.get')
def test_enrich_card_creates_new_card(mock_get, scryfall_client, temp_db):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'id': '1234-5678',
        'name': 'Black Lotus',
        'set': 'LEA',
        'set_name': 'Limited Edition Alpha',
        'collector_number': '1',
        'mana_cost': '0',
        'cmc': 0,
        'colors': [],
        'color_identity': [],
        'type_line': 'Artifact',
        'oracle_text': 'Test',
        'rarity': 'rare',
        'legalities': {},
        'image_uris': {'normal': 'http://example.com/normal.jpg', 'small': 'http://example.com/small.jpg'}
    }
    mock_get.return_value = mock_response

    temp_db.execute(
        'INSERT INTO collection (name, edition, card_number, imported_at) VALUES (?, ?, ?, ?)',
        ('Black Lotus', 'LEA', '1', '2026-05-04T00:00:00Z')
    )
    temp_db.commit()

    cursor = temp_db.execute('SELECT id FROM collection LIMIT 1')
    collection_id = cursor.fetchone()[0]

    success = scryfall_client.enrich_card(collection_id, 'Black Lotus', 'LEA', 'LEA')
    assert success

    cursor = temp_db.execute('SELECT card_id FROM collection WHERE id = ?', (collection_id,))
    row = cursor.fetchone()
    assert row[0] is not None
