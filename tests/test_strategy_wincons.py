from app.strategy import find_win_cons


def _card(name, type_line='', oracle_text='', count=1, cmc=0, power=None, scryfall_id=None):
    return {
        'name': name, 'type_line': type_line, 'oracle_text': oracle_text,
        'count': count, 'cmc': cmc, 'power': power,
        'scryfall_id': scryfall_id or f'sid-{name.lower().replace(" ", "-")}',
    }


def test_finds_explicit_win_text():
    rows = [
        _card('Coalition Victory', oracle_text='You win the game.', cmc=8),
        _card('Bear', type_line='Creature — Bear', cmc=2, power='2'),
    ]
    out = find_win_cons(rows)
    assert [c['name'] for c in out] == ['Coalition Victory']
    assert out[0]['is_explicit'] is True
    assert out[0]['is_threat'] is False


def test_finds_big_threat_without_explicit_text():
    rows = [_card('Massive Beater', type_line='Creature — Giant', cmc=6, power='7')]
    out = find_win_cons(rows)
    assert len(out) == 1
    assert out[0]['is_explicit'] is False
    assert out[0]['is_threat'] is True


def test_ignores_small_creatures_and_lands():
    rows = [
        _card('Small Guy', type_line='Creature — Bear', cmc=6, power='2'),
        _card('Big Land', type_line='Land', cmc=0, power=None),
    ]
    assert find_win_cons(rows) == []


def test_dedupes_by_scryfall_id_and_sorts_by_cmc_desc():
    rows = [
        _card('Cheap Wincon', oracle_text='Players lose the game.', cmc=2, scryfall_id='a'),
        _card('Expensive Wincon', oracle_text='You win the game.', cmc=9, scryfall_id='b'),
        _card('Expensive Wincon', oracle_text='You win the game.', cmc=9, scryfall_id='b'),  # dup row
    ]
    out = find_win_cons(rows)
    assert [c['name'] for c in out] == ['Expensive Wincon', 'Cheap Wincon']


def test_card_can_be_both_explicit_and_threat():
    rows = [_card('Overkill Titan', type_line='Creature — Titan',
                  oracle_text='You win the game.', cmc=7, power='9')]
    out = find_win_cons(rows)
    assert out[0]['is_explicit'] is True
    assert out[0]['is_threat'] is True
