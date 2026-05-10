#!/usr/bin/env python3
"""
Daily price refresh — updates price_usd/foil/eur/foil on all enriched cards.
Run via cron: 0 3 * * * /usr/bin/python3 /path/to/mtg-manager/scripts/refresh_prices.py
"""
import sys
import time
import requests
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from app.database import get_db

RATE_LIMIT_S = 0.1  # 100ms between requests


def _to_float(val):
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def refresh_prices():
    db = get_db()
    session = requests.Session()
    last_request = 0.0

    cards = db.execute(
        'SELECT id, scryfall_id FROM cards WHERE scryfall_id IS NOT NULL ORDER BY id'
    ).fetchall()

    total = len(cards)
    updated = 0
    failed = 0
    now = datetime.utcnow().isoformat() + 'Z'

    print(f"[{now}] Refreshing prices for {total} cards...")

    for i, card in enumerate(cards):
        elapsed = time.time() - last_request
        if elapsed < RATE_LIMIT_S:
            time.sleep(RATE_LIMIT_S - elapsed)

        try:
            resp = session.get(
                f'{config.SCRYFALL_API_BASE}/cards/{card["scryfall_id"]}',
                timeout=10
            )
            last_request = time.time()

            if resp.status_code == 200:
                data = resp.json()
                prices = data.get('prices', {})
                db.execute(
                    '''UPDATE cards SET
                       price_usd=?, price_usd_foil=?, price_eur=?, price_eur_foil=?,
                       prices_updated_at=?
                       WHERE id=?''',
                    (_to_float(prices.get('usd')), _to_float(prices.get('usd_foil')),
                     _to_float(prices.get('eur')), _to_float(prices.get('eur_foil')),
                     now, card['id'])
                )
                updated += 1
            elif resp.status_code == 429:
                print(f"  Rate limited at card {i+1}, sleeping 10s...")
                time.sleep(10)
                failed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  Error on card {card['scryfall_id']}: {e}")
            failed += 1
            last_request = time.time()

        if (i + 1) % 50 == 0:
            db.commit()
            print(f"  {i+1}/{total} done...")

    db.commit()
    db.close()
    print(f"Done. Updated: {updated}, Failed: {failed}")


if __name__ == '__main__':
    refresh_prices()
