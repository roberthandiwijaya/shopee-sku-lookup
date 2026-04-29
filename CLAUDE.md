# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

- Start the full local stack (app + PostgreSQL in this sandbox repo on port 8001): `docker compose up --build`
- Start only PostgreSQL for local app development: `docker compose up -d postgres`
- Install Python dependencies locally: `pip install -r requirements.txt`
- Apply database migrations: `alembic upgrade head`
- Run the app locally with reload (outside Docker): `uvicorn app.main:app --reload --port 8000`
- Run all tests: `pytest tests/ -v`
- Run a single test file: `pytest tests/test_products_api.py -v`
- Run a single test case: `pytest tests/test_products_api.py -k <test_name> -v`

## Architecture overview

This is a FastAPI app that serves two surfaces from the same codebase:
- a session-authenticated Jinja2 + htmx dashboard for operations workflows
- an API-key-protected JSON API for SKU lookup and sync triggers

The application entrypoint is `app/main.py`. It wires in all routers, adds Starlette session middleware for the dashboard login flow, and starts the APScheduler job in the FastAPI lifespan so product sync runs automatically while the app process is alive.

## Main subsystems

### Shopee integration

`app/services/shopee_client.py` is the low-level Shopee API client. It is responsible for request signing, authenticated/public request construction, and retrying transient HTTP failures.

`app/services/shopee_auth.py` manages OAuth token exchange/refresh and persists tokens in the database. Most Shopee-writing paths first call `get_valid_token()` and then delegate to service methods.

### Product sync and SKU lookup

`app/services/sync_service.py` owns the product ingestion flow. It paginates Shopee item IDs, fetches item details in batches, and upserts both parent products and variant models into the local database. Manual syncs and scheduled syncs both call this same service.

`app/services/product_service.py` is the read-side service for dashboard/API product queries, sync stats, token status, alerts, and shop info.

The local product catalog lives in two tables modeled in `app/models/product.py`:
- `Product` for Shopee items
- `ProductModel` for variants/models

SKU lookup reads from the local database, not directly from Shopee. That means API/dashboard correctness often depends on whether sync has run recently.

### Discounts

Discount data is split between local reads and Shopee writes:
- `app/services/discount_service.py` syncs discount state into local tables and prepares enriched discount views for the dashboard.
- `app/routers/discounts.py` exposes the JSON API under `/api/discounts`.
- `app/routers/dashboard.py` serves the dashboard discount pages and edit form.

The dashboard edit flow for ongoing discounts may fetch live promotion prices from Shopee before rendering, then submits updates back to Shopee and redirects to the dashboard list.

Local discount storage is modeled in `app/models/discount.py` with `Discount` and `DiscountItem`.

### Dashboard and auth

Dashboard HTML is server-rendered with Jinja templates in `app/templates/`, with htmx partials under `app/templates/partials/` for polling, pagination, and expandable rows.

`app/routers/login.py` handles the session login/change-password/logout flow using `DashboardUser` rows stored in the database. Password hashing logic and the `require_login` dependency live in `app/services/dashboard_auth.py`.

`app/routers/dashboard.py` is the main dashboard controller. It serves full pages for `/` and `/discounts`, plus many htmx fragment endpoints under `/partials/...`. If an htmx request hits a protected route without a session, the custom exception handler in `app/main.py` returns an `HX-Redirect` header instead of a normal redirect.

## Data and persistence

- Runtime configuration comes from `.env` via `app/config.py`.
- Async SQLAlchemy engine/session setup lives in `app/database.py`.
- PostgreSQL is the main runtime database.
- The models use a JSON/JSONB compatibility pattern so tests can run on SQLite while production uses PostgreSQL JSONB.
- Schema changes are managed with Alembic in `alembic/versions/`; the latest repo migration adds discount tables.

## Testing notes

Tests are in `tests/` and use `pytest` + `pytest-asyncio`.

`tests/conftest.py` overrides the app database dependency to use `sqlite+aiosqlite:///./test.db`, creates tables directly from SQLAlchemy metadata for each test, and provides an `httpx.AsyncClient` against the ASGI app. Because tests do not run through Alembic migrations, changes to models sometimes require updating test assumptions separately from migration code.

## Environment-specific workflow

This sandbox repo is configured to run on port 8001 in Docker (`docker-compose.yml`). The accompanying docs (`DEPLOYMENT.md`, `WORKFLOW_GUIDE.md`) describe a dual-environment VPS workflow where sandbox and production are separate directories with separate databases and Shopee credentials. When editing deploy or environment config, preserve that separation.
