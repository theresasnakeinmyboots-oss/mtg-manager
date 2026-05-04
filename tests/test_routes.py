import pytest
import json
import tempfile
import os
from pathlib import Path
from app import create_app
from app.database import get_db, init_db
from app.importer import import_csv

@pytest.fixture
def temp_data_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        import config
        original_data_dir = config.DATA_DIR
        original_db_path = config.DB_PATH
        config.DATA_DIR = Path(tmpdir)
        config.DB_PATH = Path(tmpdir) / 'test.db'
        init_db()
        yield tmpdir
        config.DATA_DIR = original_data_dir
        config.DB_PATH = original_db_path

@pytest.fixture
def client(temp_data_dir):
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

@pytest.fixture
def sample_csv(temp_data_dir):
    csv_file = Path(temp_data_dir) / 'sample.csv'
    csv_file.write_text('''Count,Tradelist Count,Name,Edition,Card Number,Condition,Language,Foil,Signed,Artist Proof,Altered Art,Misprint,Promo,Textless,My Price
1,0,Black Lotus,LEA,1,Mint,English,,No,No,No,No,No,No,10000.00
2,1,Lightning Bolt,LEA,39,Near Mint,English,foil,No,No,No,No,No,No,50.00
3,0,Counterspell,2ED,65,Good (Lightly Played),English,,No,No,No,No,No,No,25.00
''')
    return str(csv_file)

def test_index_redirect(client):
    response = client.get('/')
    assert response.status_code == 200

def test_collection_browse_empty(client):
    response = client.get('/collection')
    assert response.status_code == 200
    assert b'Collection Browser' in response.data
    assert b'No cards found' in response.data

def test_collection_browse_with_cards(client, sample_csv):
    db = get_db()
    import_csv(sample_csv, db)
    db.close()

    response = client.get('/collection')
    assert response.status_code == 200
    assert b'Black Lotus' in response.data
    assert b'Lightning Bolt' in response.data
    assert b'Counterspell' in response.data

def test_collection_filter_by_name(client, sample_csv):
    db = get_db()
    import_csv(sample_csv, db)
    db.close()

    response = client.get('/collection?q=Black')
    assert response.status_code == 200
    assert b'Black Lotus' in response.data
    assert b'Lightning Bolt' not in response.data

def test_collection_filter_by_edition(client, sample_csv):
    db = get_db()
    import_csv(sample_csv, db)
    db.close()

    response = client.get('/collection?set=LEA')
    assert response.status_code == 200
    assert b'Black Lotus' in response.data
    assert b'Counterspell' not in response.data

def test_collection_filter_by_foil(client, sample_csv):
    db = get_db()
    import_csv(sample_csv, db)
    db.close()

    response = client.get('/collection?foil=foil')
    assert response.status_code == 200
    assert b'Lightning Bolt' in response.data
    assert b'Black Lotus' not in response.data

def test_collection_sort_by_count(client, sample_csv):
    db = get_db()
    import_csv(sample_csv, db)
    db.close()

    response = client.get('/collection?sort=count')
    assert response.status_code == 200

def test_card_detail_not_found(client):
    response = client.get('/collection/99999')
    assert response.status_code == 404

def test_card_detail_found(client, sample_csv):
    db = get_db()
    import_csv(sample_csv, db)
    cursor = db.execute('SELECT id FROM collection LIMIT 1')
    card_id = cursor.fetchone()[0]
    db.close()

    response = client.get(f'/collection/{card_id}')
    assert response.status_code == 200
    assert b'Black Lotus' in response.data or b'Lightning Bolt' in response.data

def test_import_csv_success(client, sample_csv):
    with open(sample_csv, 'rb') as f:
        response = client.post(
            '/import',
            data={'file': (f, 'test.csv')},
            content_type='multipart/form-data'
        )
    assert response.status_code == 200
    # Response is now SSE stream, check for complete status
    response_text = response.data.decode('utf-8')
    assert 'complete' in response_text
    assert 'inserted' in response_text

def test_import_csv_no_file(client):
    response = client.post('/import')
    assert response.status_code == 400

def test_import_csv_invalid_extension(client, temp_data_dir):
    txt_file = Path(temp_data_dir) / 'test.txt'
    txt_file.write_text('test')

    with open(txt_file, 'rb') as f:
        response = client.post(
            '/import',
            data={'file': (f, 'test.txt')},
            content_type='multipart/form-data'
        )
    assert response.status_code == 400

def test_import_auto_enriches(client, sample_csv):
    with open(sample_csv, 'rb') as f:
        response = client.post(
            '/import',
            data={'file': (f, 'test.csv')},
            content_type='multipart/form-data'
        )
    assert response.status_code == 200
    # Response is now SSE stream
    response_text = response.data.decode('utf-8')
    assert 'complete' in response_text
    assert 'enriched' in response_text
