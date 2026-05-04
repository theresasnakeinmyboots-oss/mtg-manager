# Phase 1 Implementation Summary

## Completed

✅ **Project Structure** — Fully scaffolded Flask app with Docker support
✅ **Database Design** — 3 tables (cards, collection, scryfall_cache) with proper indexes
✅ **CSV Import** — Idempotent importer with Deckbox format parsing and condition normalisation
✅ **Scryfall Integration** — Rate-limited API client (100ms/request) with JSON caching
✅ **Collection Browser UI** — Server-side rendered with filters (colour, type, set, condition, foil) and search
✅ **Card Detail Page** — Shows collection metadata + enriched Scryfall data (when available)
✅ **Test Coverage** — 25 unit tests (importer, Scryfall, routes), all passing
✅ **Docker Deployment** — `docker-compose.yml` with Flask app + nginx reverse proxy

## Key Design Decisions

1. **Flask over FastAPI** — simpler async model, less overhead on Raspberry Pi
2. **SQLite** — no external DB dependency, good for modest collections
3. **Server-side rendering** — Jinja2 templates, no heavy JS frameworks
4. **Rate-limited caching** — 100ms between Scryfall API calls, JSON cache in DB prevents redundant fetches
5. **Condition normalisation** — Deckbox formats (Mint, Near Mint, etc.) → short codes (M, NM, LP, PL, HP, D)
6. **Idempotent import** — deduplicates by (name, edition, card_number, condition, foil)

## Files

| Path | Purpose |
|---|---|
| `app/__init__.py` | Flask app factory |
| `app/database.py` | SQLite connection, schema init |
| `app/importer.py` | CSV parser, normaliser |
| `app/scryfall.py` | Rate-limited API client + cache |
| `app/routes/collection.py` | Browse, detail, import, enrich routes |
| `app/templates/` | Jinja2 templates |
| `static/style.css` | Responsive grid layout |
| `docker/Dockerfile` | Python 3.12 slim image |
| `docker/docker-compose.yml` | Flask + nginx services |
| `docker/nginx.conf` | Reverse proxy config |
| `tests/` | 25 unit tests (mocked API, temp DB) |
| `config.py` | Env var config |
| `wsgi.py` | Gunicorn entrypoint |
| `requirements.txt` | Flask, requests, gunicorn, pytest |
| `migrations/001_init.sql` | DB schema |

## Running Locally

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export DATA_DIR=/tmp/mtg-data
python wsgi.py
# Visit http://localhost:5000
```

## Running in Docker

```bash
docker compose -f docker/docker-compose.yml up --build
# Visit http://localhost (port 80)
```

## Testing

```bash
pytest tests/ -v
# All 25 tests pass
```

## Workflow

1. Upload CSV file via "Import CSV" button
2. Click "Enrich Cards (N)" to fetch Scryfall data (rate-limited)
3. Browse, filter, search collection
4. Click card name to view details

## Ready for Phase 2

- Deck builder routes stubbed in nav (disabled)
- Ready to add deck model, suggestion engine, deck editor UI
