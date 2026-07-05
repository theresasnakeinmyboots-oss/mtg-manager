# MTG Collection Manager

A Flask web application for managing Magic: The Gathering card collections and building decks. Deployable on Raspberry Pi via Docker + nginx.

## Features

### Collections
- **Multiple named collections** — organize cards into separate collections (e.g. "Mine", "Trade binder")
- **CSV import** — import collections exported from Deckbox
- **Delver Lens import/export** — import `.dlens` exports, and export a collection back to a `.dlens` file so corrections made here can flow back into Delver Lens
- **Staging & reconciliation** — imports land in a staging area first; choose to **add** (merge additively into an existing collection) or **replace contents** (overwrite to match the import exactly, preserving row IDs) with a preview before committing
- **Scryfall enrichment** — fetch card data (mana cost, type, colours, legalities, images, prices) and cache locally; bulk-enrich from the UI
- **Collection browser** — card-grid or list view, full filtering (colour, type, set, condition, foil), multi-sort, infinite scroll, Scryfall-style search syntax

### Set View
- Browse all Scryfall sets with owned/unowned stats per set; click a set to filter the card grid to it, with filters for colour, type, rarity, and foil status

### Deck Builder
- **Formats**: Standard, Modern, Pioneer, Legacy, Commander/EDH, and Sealed (40-card minimum)
- **List vs physical decks** — a *list* deck is a theoretical decklist (cards tracked as game objects; you don't need to own them), while a *physical* deck is built from specific owned cards stored separately from the collection. Physical decks are backed by **allocations**: auto-allocation reserves owned copies (preferring the exact printing, falling back to any printing), and a copy allocated to one physical deck can't be claimed by another
- Create decks from scratch, import from a text decklist, import from a `.dlens` file, clone an existing deck, or seed a sealed deck from an owned collection
- Add/remove cards with autocomplete search; manage main/side/commander boards; card-grid or list view (list view shows edition, collector number, and per-row counts)
- **Ownership tracking** — see owned count per card; cards you own link to their exact collection row, unowned cards link to a read-only reference page
- **Printing reconciliation** — when a deck specifies a printing you don't own under that exact printing (but do own a different printing of the same card), a reconciliation page lets you resolve the mismatch either by updating the collection row to match the deck, or updating the deck to match what you own
- **Analysis** — mana curve, colour distribution, type breakdown, mana sources (parsed from oracle text + basic land types), opening-hand probabilities
- **Deck health** — heuristic checks (removal, card draw, ramp, protection, win conditions, etc.) scored against a detected or chosen strategy (Aggro, Midrange, Control, Combo, Ramp, Voltron, Tokens, Stax); individual warnings can be muted
- **Commander tools** (using cached EDHREC data) — auto-detect/promote commander, EDHREC-based card suggestions by category, cut-candidate suggestions, and combo detection (complete combos and near-misses) for the active commander
- **Auto-fill** — automatically fill a commander deck from an owned collection using synergy + health scoring

### Card Detail Pages
- Owned cards open a mutable collection-row page (edit condition/foil/count, switch between owned printings)
- Unowned cards open a read-only reference page (legalities, price, all printings, total owned count, decks containing the card)

### Admin
- Bulk Scryfall data sync, Delver Lens APK `data.db` fetch/upload, per-commander EDHREC cache refresh, and app settings, with live log tails for long-running jobs

## Architecture

- **Backend**: Flask + Gunicorn + SQLite
- **Frontend**: Jinja2 server-side templates + minimal CSS/JS (no heavy frameworks)
- **Deployment**: Docker Compose (Flask app + nginx reverse proxy)
- **External data**: Scryfall bulk data + API cache, EDHREC commander/combo cache, Delver Lens APK `data.db` for `.dlens` import/export

## Quick Start (Local Development)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/  # Run tests
export DATA_DIR=/tmp/mtg-data
python wsgi.py          # Run at http://localhost:5000
```

## Docker Deployment

```bash
docker compose -f docker/docker-compose.yml up --build
# Visit http://localhost (port 80, served through nginx)
```

Data persists in a named volume at `/data`.

## Database Schema (high level)

- **collections** — named collections (including staging collections for in-progress imports)
- **collection** — owned cards, scoped to a collection; unenriched rows have `card_id = NULL` until Scryfall data is fetched
- **cards** — canonical Scryfall card data referenced by owned rows
- **scryfall_bulk** — full Scryfall bulk dataset (all printings, prices, images)
- **scryfall_cache** — raw Scryfall API response cache
- **decks** / **deck_cards** — deck metadata (including list/physical kind) and composition (main/side/commander boards)
- **deck_allocations** — which owned collection rows physically back each card in a physical deck
- **edhrec_cache** / **edhrec_cards** / **combo_cache** / **combo_details_cache** — cached EDHREC commander data, suggestions, and combos
- **deck_health_mutes** — muted health-check warnings per deck
- **deck_strategy_feedback** — strategy auto-detection history and user feedback
- **app_settings** — persisted app settings (e.g. suggestions per type)

## Environment Variables

- `DATA_DIR` — Path to persistent data directory (default `/data`); holds the SQLite databases, Delver Lens `data.db`, imports, and logs
- `FLASK_DEBUG` — Enable Flask debug mode (default `false`)
- `SECRET_KEY` — Flask session secret (set a real value in production)

## Testing

```bash
python -m pytest tests/ -v
```

Tests use temporary SQLite databases and mock Scryfall API responses.

## License

MIT
