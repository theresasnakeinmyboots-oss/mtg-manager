import pytest
import sqlite3
import tempfile
from pathlib import Path
from app.importer import import_csv, normalize_condition
from app.database import get_db, init_db
import os

@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        import config
        original_data_dir = config.DATA_DIR
        original_db_path = config.DB_PATH
        config.DATA_DIR = Path(tmpdir)
        config.DB_PATH = Path(tmpdir) / 'test.db'
        init_db()
        db = get_db()
        yield db
        db.close()
        config.DATA_DIR = original_data_dir
        config.DB_PATH = original_db_path

@pytest.fixture
def sample_csv(tmp_path):
    csv_file = tmp_path / 'sample.csv'
    csv_file.write_text('''Count,Tradelist Count,Name,Edition,Card Number,Condition,Language,Foil,Signed,Artist Proof,Altered Art,Misprint,Promo,Textless,My Price
1,0,Black Lotus,Alpha,1,Mint,English,,No,No,No,No,No,No,10000.00
2,1,Lightning Bolt,Beta,39,Near Mint,English,foil,No,No,No,No,No,No,50.00
3,0,Counterspell,Unlimited,65,Good (Lightly Played),English,,No,No,No,No,No,No,25.00
''')
    return str(csv_file)

def test_normalize_condition():
    assert normalize_condition('Mint') == 'M'
    assert normalize_condition('Near Mint') == 'NM'
    assert normalize_condition('Good (Lightly Played)') == 'LP'
    assert normalize_condition('Played') == 'PL'
    assert normalize_condition('Heavily Played') == 'HP'
    assert normalize_condition('Poor') == 'D'
    assert normalize_condition('') == 'NM'

def test_import_csv_basic(temp_db, sample_csv):
    inserted, skipped = import_csv(sample_csv, temp_db)
    assert inserted == 3
    assert skipped == 0

    cursor = temp_db.execute('SELECT COUNT(*) FROM collection')
    assert cursor.fetchone()[0] == 3

def test_import_csv_deduplication(temp_db, sample_csv):
    inserted1, skipped1 = import_csv(sample_csv, temp_db)
    assert inserted1 == 3
    assert skipped1 == 0

    # Re-import same CSV — should increment counts, not insert new rows
    inserted2, skipped2 = import_csv(sample_csv, temp_db)
    assert inserted2 == 0
    assert skipped2 == 3

    # Check that counts doubled
    cursor = temp_db.execute('SELECT SUM(count) FROM collection WHERE name = "Black Lotus"')
    total = cursor.fetchone()[0]
    assert total == 2  # Was 1, now 1+1=2

def test_import_csv_fields(temp_db, sample_csv):
    import_csv(sample_csv, temp_db)

    cursor = temp_db.execute('SELECT * FROM collection WHERE name = "Black Lotus"')
    row = dict(cursor.fetchone())
    assert row['name'] == 'Black Lotus'
    assert row['edition'] == 'Alpha'
    assert row['card_number'] == '1'
    assert row['count'] == 1
    assert row['condition'] == 'M'
    assert row['foil'] == ''
    assert row['my_price'] == 10000.0

    cursor = temp_db.execute('SELECT * FROM collection WHERE name = "Lightning Bolt"')
    row = dict(cursor.fetchone())
    assert row['count'] == 2
    assert row['tradelist_count'] == 1
    assert row['foil'] == 'foil'

def test_import_csv_missing_file():
    db = sqlite3.connect(':memory:')
    with pytest.raises(FileNotFoundError):
        import_csv('/nonexistent/file.csv', db)
