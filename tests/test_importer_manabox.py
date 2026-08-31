import pytest
import tempfile
from pathlib import Path
from app.importer_manabox import (
    import_manabox, is_manabox_csv, normalize_condition, normalize_foil, normalize_language,
    resolve_manabox_for_deck,
)
from app.database import get_db, init_db

BLACK_LOTUS_ID = '40c202f1-6e0d-42f4-a41e-e0be3362d585'
BOLT_ID = 'ab96b656-100e-491a-a8c9-94dbb9482c4d'


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
        db.execute(
            '''INSERT INTO scryfall_bulk (scryfall_id, name, set_code, set_name, collector_number)
               VALUES (?, ?, ?, ?, ?)''',
            (BLACK_LOTUS_ID, 'Black Lotus', 'MSH', 'Marvel Super Heroes', '101')
        )
        db.execute(
            '''INSERT INTO scryfall_bulk (scryfall_id, name, set_code, set_name, collector_number)
               VALUES (?, ?, ?, ?, ?)''',
            (BOLT_ID, 'Lightning Bolt', 'MSH', 'Marvel Super Heroes', '44')
        )
        db.commit()
        yield db
        db.close()
        config.DATA_DIR = original_data_dir
        config.DB_PATH = original_db_path


@pytest.fixture
def sample_manabox_csv(tmp_path):
    csv_file = tmp_path / 'manabox.csv'
    csv_file.write_text(
        'Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,'
        'Purchase price,Misprint,Altered,Condition,Language,Purchase price currency,Added\n'
        f'HYDRA Troopers,MSH,Marvel Super Heroes,101,foil,common,1,113410,{BLACK_LOTUS_ID},'
        '0.09,false,false,near_mint,en,GBP,2026-07-26T11:42:27.550Z\n'
        f'A.I.M. Scientists,MSH,Marvel Super Heroes,44,normal,common,3,113422,{BOLT_ID},'
        '0.03,false,false,lightly_played,en,GBP,2026-07-26T13:11:31.924Z\n'
    )
    return str(csv_file)


def test_is_manabox_csv(sample_manabox_csv, tmp_path):
    assert is_manabox_csv(sample_manabox_csv)

    deckbox_csv = tmp_path / 'deckbox.csv'
    deckbox_csv.write_text('Count,Name,Edition,Card Number,Condition,Foil\n1,Black Lotus,Alpha,1,Mint,\n')
    assert not is_manabox_csv(str(deckbox_csv))


def test_normalize_helpers():
    assert normalize_condition('near_mint') == 'NM'
    assert normalize_condition('lightly_played') == 'LP'
    assert normalize_condition('') == 'NM'
    assert normalize_foil('foil') == 'foil'
    assert normalize_foil('normal') == ''
    assert normalize_foil('') == ''
    assert normalize_language('en') == 'English'
    assert normalize_language('') == 'English'
    assert normalize_language('xx') == 'xx'


def test_import_manabox_basic(temp_db, sample_manabox_csv):
    inserted, skipped, missing = import_manabox(sample_manabox_csv, temp_db)
    assert inserted == 2
    assert skipped == 0
    assert missing == []

    cursor = temp_db.execute('SELECT COUNT(*) FROM collection')
    assert cursor.fetchone()[0] == 2


def test_import_manabox_fields(temp_db, sample_manabox_csv):
    import_manabox(sample_manabox_csv, temp_db)

    row = dict(temp_db.execute(
        'SELECT * FROM collection WHERE scryfall_id = ?', (BLACK_LOTUS_ID,)
    ).fetchone())
    assert row['name'] == 'Black Lotus'
    assert row['edition'] == 'Marvel Super Heroes'
    assert row['card_number'] == '101'
    assert row['count'] == 1
    assert row['condition'] == 'NM'
    assert row['foil'] == 'foil'
    assert row['language'] == 'English'
    assert row['card_id'] is not None

    row2 = dict(temp_db.execute(
        'SELECT * FROM collection WHERE scryfall_id = ?', (BOLT_ID,)
    ).fetchone())
    assert row2['count'] == 3
    assert row2['condition'] == 'LP'
    assert row2['foil'] == ''


def test_import_manabox_deduplication(temp_db, sample_manabox_csv):
    inserted1, skipped1, _ = import_manabox(sample_manabox_csv, temp_db)
    assert inserted1 == 2
    assert skipped1 == 0

    inserted2, skipped2, _ = import_manabox(sample_manabox_csv, temp_db)
    assert inserted2 == 0
    assert skipped2 == 2

    total = temp_db.execute(
        'SELECT SUM(count) FROM collection WHERE scryfall_id = ?', (BLACK_LOTUS_ID,)
    ).fetchone()[0]
    assert total == 2


def test_import_manabox_missing_scryfall_id(temp_db, tmp_path):
    csv_file = tmp_path / 'unknown.csv'
    unknown_id = '00000000-0000-0000-0000-000000000000'
    csv_file.write_text(
        'Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,'
        'Purchase price,Misprint,Altered,Condition,Language,Purchase price currency,Added\n'
        f'Nonexistent Card,XXX,Unknown Set,1,normal,common,1,1,{unknown_id},'
        '0.01,false,false,near_mint,en,GBP,2026-07-26T11:42:27.550Z\n'
    )
    inserted, skipped, missing = import_manabox(str(csv_file), temp_db)
    assert inserted == 0
    assert skipped == 0
    assert len(missing) == 1
    assert missing[0]['scryfall_id'] == unknown_id


def test_import_manabox_missing_file():
    import sqlite3
    db = sqlite3.connect(':memory:')
    with pytest.raises(FileNotFoundError):
        import_manabox('/nonexistent/file.csv', db)


def test_resolve_manabox_for_deck(temp_db, sample_manabox_csv):
    entries, warnings = resolve_manabox_for_deck(sample_manabox_csv, temp_db)
    assert warnings == []
    assert len(entries) == 2
    by_id = {e['scryfall_id']: e for e in entries}
    assert by_id[BLACK_LOTUS_ID]['count'] == 1
    assert by_id[BOLT_ID]['count'] == 3
