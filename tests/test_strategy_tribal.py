from app.strategy import (
    detect_tribe, count_type_tribe, count_typal_payoffs,
    compute_health, compute_health_sids, detect_strategy, STRATEGIES,
)


def _card(name, type_line='', oracle_text='', count=1, cmc=0, power=None, scryfall_id=None):
    return {
        'name': name, 'type_line': type_line, 'oracle_text': oracle_text,
        'count': count, 'cmc': cmc, 'power': power,
        'scryfall_id': scryfall_id or f'sid-{name.lower().replace(" ", "-")}',
    }


# ── detect_tribe ────────────────────────────────────────────────────────────

def test_detect_tribe_single_type():
    assert detect_tribe('Legendary Creature — Dragon') == ['dragon']


def test_detect_tribe_multi_type():
    assert detect_tribe('Legendary Creature — Elf Warrior') == ['elf', 'warrior']


def test_detect_tribe_non_creature_returns_empty():
    assert detect_tribe('Legendary Planeswalker — Garruk') == []
    assert detect_tribe('Legendary Artifact') == []


def test_detect_tribe_no_subtype_returns_empty():
    assert detect_tribe('Legendary Creature') == []


def test_detect_tribe_none_input():
    assert detect_tribe(None) == []


# ── count_type_tribe / count_typal_payoffs ──────────────────────────────────

def test_count_type_tribe():
    rows = [
        _card('Dragon A', type_line='Creature — Dragon', count=1),
        _card('Dragon B', type_line='Legendary Creature — Dragon Noble', count=2),
        _card('Bear', type_line='Creature — Bear', count=1),
        _card('Fog', type_line='Instant', count=3),
    ]
    assert count_type_tribe(rows, ['dragon']) == 3  # Dragon A (1) + Dragon B (2)
    assert count_type_tribe(rows, []) == 0


def test_count_typal_payoffs_matches_name_in_oracle_text():
    rows = [
        _card('Lord', type_line='Creature — Dragon',
              oracle_text='Other Dragons you control get +1/+1.', count=1),
        _card('Tutor', type_line='Sorcery',
              oracle_text='Search your library for a Dragon card.', count=1),
        _card('Unrelated', type_line='Sorcery', oracle_text='Draw a card.', count=5),
    ]
    assert count_typal_payoffs(rows, ['dragon']) == 2
    assert count_typal_payoffs(rows, []) == 0


# ── compute_health / compute_health_sids with the Tribal strategy ──────────

def test_tribal_strategy_registered():
    assert 'tribal' in STRATEGIES
    checks = STRATEGIES['tribal']['health_checks']
    assert 'Type count' in checks
    assert 'Typal payoffs' in checks


def test_compute_health_tribal_counts():
    rows = [
        _card('Commander', type_line='Legendary Creature — Dragon', count=1),
        _card('Dragon Lord', type_line='Creature — Dragon',
              oracle_text='Other Dragons you control get +1/+1.', count=1),
        _card('Dragon Tutor', type_line='Sorcery',
              oracle_text='Search your library for a Dragon card.', count=1),
        _card('Vanilla Bear', type_line='Creature — Bear', count=10),
    ]
    result = compute_health(rows, 'tribal', True, commander_type_line='Legendary Creature — Dragon')
    by_label = {label: (count, tag) for label, count, tag, _tip in result}
    # Commander + Dragon Lord both have the Dragon subtype
    assert by_label['Type count'][0] == 2
    # Dragon Lord's anthem text + the tutor both name "Dragon"
    assert by_label['Typal payoffs'][0] == 2


def test_compute_health_no_commander_type_line_is_inert():
    rows = [_card('Bear', type_line='Creature — Bear', count=10)]
    result = compute_health(rows, 'tribal', True, commander_type_line=None)
    by_label = {label: count for label, count, _tag, _tip in result}
    assert by_label['Type count'] == 0
    assert by_label['Typal payoffs'] == 0


def test_compute_health_sids_tribal():
    rows = [
        _card('Commander', type_line='Legendary Creature — Dragon', count=1, scryfall_id='sid-cmd'),
        _card('Dragon Lord', type_line='Creature — Dragon',
              oracle_text='Other Dragons you control get +1/+1.', count=1, scryfall_id='sid-lord'),
        _card('Vanilla Bear', type_line='Creature — Bear', count=1, scryfall_id='sid-bear'),
    ]
    sids = compute_health_sids(rows, 'tribal', commander_type_line='Legendary Creature — Dragon')
    assert set(sids['Type count']) == {'sid-cmd', 'sid-lord'}
    assert sids['Typal payoffs'] == ['sid-lord']


# ── detect_strategy tribal signal ───────────────────────────────────────────

def test_detect_strategy_favours_tribal_with_strong_signal():
    rows = [_card(f'Dragon {i}', type_line='Creature — Dragon',
                  oracle_text='Other Dragons you control get +1/+1 and flying.', count=1)
            for i in range(20)]
    detection = detect_strategy(rows, commander='Test Dragon',
                                 commander_type_line='Legendary Creature — Dragon')
    assert detection['strategy'] == 'tribal'
    assert any('Tribal' in r for r in detection['reasoning'])


def test_detect_strategy_no_tribe_never_picks_tribal():
    rows = [_card('Bear', type_line='Creature — Bear', count=10)]
    detection = detect_strategy(rows, commander='Generic Commander', commander_type_line=None)
    assert detection['scores']['tribal'] == 0
