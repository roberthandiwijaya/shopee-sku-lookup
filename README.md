# Shopee Product SKU Lookup

REST API + web dashboard that syncs products from Shopee Open API v2.0 to a local PostgreSQL database and serves fast SKU-based lookups.

## Tech Stack

- **Python 3.11+** / **FastAPI**
- **PostgreSQL** / **SQLAlchemy 2.0** (async) / **Alembic**
- **httpx** for async Shopee API calls
- **APScheduler** for periodic product sync
- **Jinja2** + **htmx** + **Tailwind CSS** for the web dashboard (zero build tools)

## Quick Start (Docker)

The easiest way to run everything — PostgreSQL, pgAdmin, and the FastAPI app — with a single command:

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your Shopee credentials and API key:

```
API_KEY=your_random_secret_key
SHOPEE_PARTNER_ID=your_partner_id
SHOPEE_PARTNER_KEY=your_partner_key
SHOPEE_SHOP_ID=your_shop_id
```

### 2. Start everything

```bash
docker compose up --build
```

This starts all three services:

| Service | URL |
|---------|-----|
| **FastAPI app** | http://localhost:8000 |
| **pgAdmin** | http://localhost:5050 |
| **PostgreSQL** | `localhost:54558` |

Database migrations are applied automatically on startup.

> **Data safety:** `docker compose down` stops containers but preserves your data (the `pgdata` volume persists). Only `docker compose down -v` deletes volumes.

### 3. Open the dashboard

Visit `http://localhost:8000/` to access the web dashboard where you can:

- Monitor sync status and token health at a glance
- Browse and search products by SKU
- Expand products to view variants
- Trigger manual syncs
- Re-authorize your Shopee token

### 4. Authorize with Shopee

Click "Re-authorize" in the dashboard's auth panel, or open `http://localhost:8000/api/auth/login` — this returns a Shopee OAuth URL. Open it in your browser, authorize, and the callback will store your tokens.

### 5. Sync products

Click "Sync Now" in the dashboard, or via API:

```bash
curl -X POST -H "X-API-Key: your_api_key" http://localhost:8000/api/sync
```

Products also auto-sync every 60 minutes (configurable via `SYNC_INTERVAL_MINUTES`).

### 6. Query by SKU

```bash
# Single SKU
curl -H "X-API-Key: your_api_key" "http://localhost:8000/api/products?sku=YOUR_SKU"

# Multiple SKUs
curl -H "X-API-Key: your_api_key" "http://localhost:8000/api/products?sku=SKU1&sku=SKU2"

# Comma-separated
curl -H "X-API-Key: your_api_key" "http://localhost:8000/api/products?sku=SKU1,SKU2,SKU3"
```

## Local Development (without Docker)

If you prefer running the app outside of Docker:

```bash
# Start only PostgreSQL and pgAdmin
docker compose up -d postgres pgadmin

# Install dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Start the server with hot reload
uvicorn app.main:app --reload --port 8000
```

The `.env` file points to `localhost:54558` by default, which matches the PostgreSQL container's exposed port.

## Dashboard

The web dashboard at `/` provides three panels:

| Panel | Features |
|-------|----------|
| **Sync Status** | Product/variant counts, last sync time, token health indicator, "Sync Now" button. Auto-refreshes every 30s. |
| **Authentication** | Token status badge, expiry countdown, shop ID, "Re-authorize" button. Auto-refreshes every 30s. |
| **Products** | Searchable product table with SKU filtering (300ms debounce), pagination, and expandable variant rows. |

All interactions use htmx for seamless partial updates without full page reloads.

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/` | — | Web dashboard |
| GET | `/api/products?sku=...` | `X-API-Key` | SKU lookup (single, multiple, or comma-separated) |
| POST | `/api/sync` | `X-API-Key` | Trigger manual product sync |
| GET | `/api/sync/status` | — | Check last sync time and counts |
| GET | `/api/auth/login` | — | Get Shopee OAuth URL |
| GET | `/api/auth/callback` | — | OAuth callback (automatic) |
| GET | `/docs` | — | Swagger UI |

## Example Response

```json
{
  "products": [
    {
      "item_id": 801992659,
      "item_sku": "ABC",
      "item_name": "Product Name",
      "current_price": 150000.00,
      "currency": "IDR",
      "has_model": true,
      "models": [
        { "model_sku": "ABC-RED", "model_name": "Red", "stock": 25 },
        { "model_sku": "ABC-BLUE", "model_name": "Blue", "stock": 10 }
      ],
      "synced_at": "2026-02-14T10:00:00Z"
    }
  ],
  "count": 1,
  "skus_not_found": []
}
```

## pgAdmin Access

After `docker compose up -d`, open http://localhost:5050 and login:

- **Email:** `admin@admin.com`
- **Password:** `admin`

Add a server with host `postgres`, port `5432`, user/password `postgres`.

## Running Tests

```bash
pytest tests/ -v
```

Tests use SQLite (no PostgreSQL required).

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `API_KEY` | API key for protected endpoints (sent via `X-API-Key` header) | — |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+asyncpg://postgres:postgres@localhost:54558/shopee_products` |
| `SHOPEE_PARTNER_ID` | Shopee partner ID | — |
| `SHOPEE_PARTNER_KEY` | Shopee partner key | — |
| `SHOPEE_SHOP_ID` | Shopee shop ID | — |
| `SHOPEE_BASE_URL` | Shopee API base URL | `https://openplatform.sandbox.test-stable.shopee.sg` |
| `SHOPEE_REDIRECT_URL` | OAuth callback URL | `http://localhost:8000/api/auth/callback` |
| `SYNC_INTERVAL_MINUTES` | Auto-sync interval | `60` |
