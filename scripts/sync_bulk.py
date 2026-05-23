#!/usr/bin/env python3
"""
Download Scryfall's default_cards bulk data and populate scryfall_bulk table.
Run once to seed, then daily via cron:
  0 2 * * * cd /home/woody/mtg-manager && /usr/bin/python3 scripts/sync_bulk.py >> data/bulk_sync.log 2>&1
"""
import sys
import json
import time
import requests
import sqlite3
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


def to_float(val):
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def format_card(c):
    image_uris = c.get('image_uris') or {}
    # card_faces fallback for split/transform cards
    if not image_uris and c.get('card_faces'):
        image_uris = c['card_faces'][0].get('image_uris') or {}
    oracle = c.get('oracle_text', '')
    if not oracle and c.get('card_faces'):
        oracle = '\n'.join(f.get('oracle_text', '') for f in c['card_faces'])
    flavor = c.get('flavor_text', '')
    if not flavor and c.get('card_faces'):
        flavor = c['card_faces'][0].get('flavor_text', '')
    mana_cost = c.get('mana_cost', '')
    if not mana_cost and c.get('card_faces'):
        mana_cost = c['card_faces'][0].get('mana_cost', '')
    prices = c.get('prices') or {}
    return (
        c['id'],
        c.get('name', ''),
        c.get('set', '').upper(),
        c.get('set_name', ''),
        c.get('collector_number', ''),
        mana_cost,
        float(c.get('cmc', 0) or 0),
        json.dumps(c.get('colors', [])),
        json.dumps(c.get('color_identity', [])),
        c.get('type_line', ''),
        oracle,
        flavor,
        c.get('power'),
        c.get('toughness'),
        c.get('rarity', '').upper(),
        json.dumps(c.get('legalities', {})),
        image_uris.get('normal'),
        image_uris.get('small'),
        c.get('artist', ''),
        to_float(prices.get('usd')),
        to_float(prices.get('usd_foil')),
        to_float(prices.get('eur')),
        to_float(prices.get('eur_foil')),
    )


def main():
    now = datetime.utcnow().isoformat()
    print(f'[{now}] Starting bulk sync…')

    session = requests.Session()
    session.headers['User-Agent'] = 'mtg-manager/1.0'

    # Get bulk data download URL
    print('  Fetching bulk data manifest…')
    resp = session.get('https://api.scryfall.com/bulk-data/default-cards', timeout=30)
    resp.raise_for_status()
    download_url = resp.json()['download_uri']
    print(f'  Downloading from {download_url}…')

    # Stream download to temp file
    tmp_path = config.DATA_DIR / 'scryfall_bulk.json.tmp'
    resp = session.get(download_url, stream=True, timeout=120)
    resp.raise_for_status()
    downloaded = 0
    with open(tmp_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            downloaded += len(chunk)
            if downloaded % (20 * 1024 * 1024) == 0:
                print(f'  {downloaded // 1024 // 1024}MB downloaded…')
    print(f'  Download complete ({downloaded // 1024 // 1024}MB)')

    # Parse and insert into DB
    db = sqlite3.connect(str(config.DB_PATH))
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=NORMAL')

    print('  Parsing and inserting into DB…')
    inserted = updated = skipped = 0
    batch = []
    BATCH_SIZE = 500

    with open(tmp_path, encoding='utf-8') as f:
        cards = json.load(f)

    total = len(cards)
    print(f'  {total} cards in bulk file')

    for i, card in enumerate(cards):
        # Skip tokens, emblems, art cards — no gameplay data
        if card.get('layout') in ('token', 'emblem', 'art_series', 'double_faced_token'):
            skipped += 1
            continue
        batch.append(format_card(card))
        if len(batch) >= BATCH_SIZE:
            db.executemany(
                '''INSERT INTO scryfall_bulk
                   (scryfall_id, name, set_code, set_name, collector_number, mana_cost, cmc,
                    colors, color_identity, type_line, oracle_text, flavor_text, power, toughness,
                    rarity, legalities, image_uri_normal, image_uri_small, artist,
                    price_usd, price_usd_foil, price_eur, price_eur_foil)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(scryfall_id) DO UPDATE SET
                       name=excluded.name, set_code=excluded.set_code, set_name=excluded.set_name,
                       collector_number=excluded.collector_number, mana_cost=excluded.mana_cost,
                       cmc=excluded.cmc, colors=excluded.colors, color_identity=excluded.color_identity,
                       type_line=excluded.type_line, oracle_text=excluded.oracle_text,
                       flavor_text=excluded.flavor_text, power=excluded.power, toughness=excluded.toughness,
                       rarity=excluded.rarity, legalities=excluded.legalities,
                       image_uri_normal=excluded.image_uri_normal, image_uri_small=excluded.image_uri_small,
                       artist=excluded.artist, price_usd=excluded.price_usd,
                       price_usd_foil=excluded.price_usd_foil, price_eur=excluded.price_eur,
                       price_eur_foil=excluded.price_eur_foil''',
                batch
            )
            inserted += len(batch)
            batch = []
            if (i + 1) % 50000 == 0:
                db.commit()
                print(f'  {i+1}/{total}…')

    if batch:
        db.executemany(
            '''INSERT INTO scryfall_bulk
               (scryfall_id, name, set_code, set_name, collector_number, mana_cost, cmc,
                colors, color_identity, type_line, oracle_text, flavor_text, power, toughness,
                rarity, legalities, image_uri_normal, image_uri_small, artist,
                price_usd, price_usd_foil, price_eur, price_eur_foil)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(scryfall_id) DO UPDATE SET
                   name=excluded.name, set_code=excluded.set_code, set_name=excluded.set_name,
                   collector_number=excluded.collector_number, mana_cost=excluded.mana_cost,
                   cmc=excluded.cmc, colors=excluded.colors, color_identity=excluded.color_identity,
                   type_line=excluded.type_line, oracle_text=excluded.oracle_text,
                   flavor_text=excluded.flavor_text, power=excluded.power, toughness=excluded.toughness,
                   rarity=excluded.rarity, legalities=excluded.legalities,
                   image_uri_normal=excluded.image_uri_normal, image_uri_small=excluded.image_uri_small,
                   artist=excluded.artist, price_usd=excluded.price_usd,
                   price_usd_foil=excluded.price_usd_foil, price_eur=excluded.price_eur,
                   price_eur_foil=excluded.price_eur_foil''',
            batch
        )
        inserted += len(batch)

    db.commit()
    db.close()
    tmp_path.unlink()

    print(f'  Done. {inserted} upserted, {skipped} skipped.')
    print(f'[{datetime.utcnow().isoformat()}] Bulk sync complete.')


if __name__ == '__main__':
    main()
