from app.edhrec import _name_to_slug


def test_slug_plain_name():
    assert _name_to_slug('Krenko, Mob Boss') == 'krenko-mob-boss'


def test_slug_strips_apostrophes_and_periods():
    assert _name_to_slug("Kykar, Wind's Fury") == 'kykar-winds-fury'
    assert _name_to_slug('Dr. Julius Jumblemorph') == 'dr-julius-jumblemorph'


def test_slug_transliterates_accented_characters():
    # Regression: accented letters used to be dropped entirely (é -> '')
    # instead of transliterated (é -> e), producing a slug that 403s
    # against EDHREC's real URL instead of matching it.
    assert _name_to_slug('Bartolomé del Presidio') == 'bartolome-del-presidio'
    assert _name_to_slug('Emiel the Blessed') == 'emiel-the-blessed'  # unaffected baseline
    assert _name_to_slug('Tuvasa the Sunlit') == 'tuvasa-the-sunlit'


def test_slug_handles_double_faced_cards():
    # Only the front face name is used for the slug.
    assert _name_to_slug('Valki, God of Lies // Tibalt, Cosmic Impostor') == 'valki-god-of-lies'
