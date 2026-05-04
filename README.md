# MTG Collection Manager

A Flask web application for managing Magic: The Gathering card collections. Deployable on Raspberry Pi via Docker + nginx.

## Features (Phase 1)

- **CSV Import** — Import collections exported from Deckbox
- **Scryfall Enrichment** — Fetch card data (mana cost, type, colours, legalities, images) and cache locally
- **Collection Browser** — Filter by colour, type, set, condition, foil status; sort by name, set, or count
- **Card Search** — Full-text search by name
- **Responsive UI** — Server-side rendered, no heavy JS frameworks

## Architecture

- **Backend**: Flask + Gunicorn + SQLite
- **Frontend**: Jinja2 server-side templates + minimal CSS
- **Deployment**: Docker Compose (Flask app + nginx reverse proxy)
- **API Cache**: Scryfall responses cached locally to minimize API calls

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

## Database Schema

### `collection` — User's physical cards (from CSV import)
- name, edition, card_number, condition, foil, count, tradelist_count, language, special attributes
- Unenriched cards have `card_id = NULL` until Scryfall data is fetched

### `cards` — Canonical Scryfall card data
- scryfall_id, name, set_code, mana_cost, type_line, oracle_text, colours, image URIs, legalities, etc.
- One row per unique printing; shared across the collection

### `scryfall_cache` — API response cache
- Keyed by lookup string (e.g., `named:black lotus:lea`), stores raw JSON to avoid redundant API calls

## Workflow

1. **Import CSV** — Upload Deckbox export file; cards inserted into `collection` table
2. **Enrich Cards** — Click "Enrich Cards (N)" to fetch Scryfall data for unenriched cards (rate-limited at 100ms/request)
3. **Browse** — Filter and search the collection, view card details with Scryfall images

## Environment Variables

- `DATA_DIR` — Path to persistent data directory (default `/data`)
- `FLASK_DEBUG` — Enable Flask debug mode (default `false`)

## Testing

```bash
python -m pytest tests/ -v
```

Tests use temporary SQLite databases and mock Scryfall API responses.

## Phase 2 & 3 (Future)

- **Phase 2** — Deck builder UI, card suggestion engine based on play style
- **Phase 3** — Rules-lite game engine, match simulation, win rate analysis

## License

MIT
