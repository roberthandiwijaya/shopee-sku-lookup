# Shopee Product SKU Lookup

REST API + web dashboard that syncs products from Shopee Open API v2.0 to a local PostgreSQL database and serves fast SKU-based lookups.

## 🆕 What's New

- **🔥 Discount Management** — Edit ongoing discounts with live price pre-fill from Shopee API
- **📊 Stock Tracking** — View current inventory levels in discount edit page
- **🧪 Sandbox + Production** — Dual environment setup for safe development
- **🚀 Easy Deploy Script** — One-command deployment to sandbox or production

## Safe product reconciliation

Product sync discovers `NORMAL`, `UNLIST` and `BANNED` listings and also checks all locally known shop item IDs. Inactive products stay in the operations/SKU database with their actual Shopee status; public catalogs and PDFs include only `NORMAL` products.

Removed variants are archived (`product_models.is_active=false`), never automatically deleted. Two complete, validated matching Shopee model lists are required before archiving; a confirmed transition to a non-variant product likewise requires a second base response. Returned models reactivate automatically. Current variant relationships, filters, preflight and exports exclude archived models. Apply migration `014` before starting this version.

Errors, incomplete/duplicate responses, ambiguous empty model lists and unknown warnings do not authorize archival or freshen affected records. A missing item is not assumed deleted: failed batches are retried individually so healthy items continue, and the run reports an incomplete result with affected item IDs in logs. Previously successful batches remain committed. A PostgreSQL advisory lock prevents overlapping product syncs. Family codes and materials are never overwritten by upstream sync.

Limitations: an upstream item that cannot be confirmed remains stale and requires investigation; identical but silently incomplete upstream lists cannot be independently proven complete. Variant confirmation adds API requests. The legacy sync-status timestamp is still the newest per-product timestamp, not a durable full-run success record. Do not use it alone as proof that every record refreshed.

## Public PDF admission protection

Guest exports stay available through the background queue only. `/linked-products/export.pdf` is retired with HTTP 410; it no longer validates bodies, fetches images or renders PDFs. API clients first GET `/linked-products/export-session` with cookies enabled, then submit `X-Export-CSRF` and an allowed Origin/Referer on preflight and job creation. Missing prior sessions are not admitted. Existing capability-protected status, cancel and download routes retain their authorization contract.

Default configurable limits: one active export/session; 30-second acceptance cooldown; five accepted exports and 500 requested families/session/hour; 20 accepted exports and 2,000 requested families/client network/hour; global queue limit ten. Family charges use normalized selected codes, not worker-filtered results. Accepted failed/cancelled jobs count; rejected requests and worker retries do not create charges. HTTP 429 includes `Retry-After`, `reason` and `retry_after`; capacity retries are advisory. Preflight has separate fixed-minute limits (10/session, 60/network). PostgreSQL serializes admission across processes.

Migration `015` adds immutable quota metadata and bounded preflight buckets. Network linkage is HMAC-hashed (IPv4 /32, IPv6 /64), cleared after 24 hours independently of file expiry; no raw addresses are stored in quota state. Network limiting needs a dedicated shared `CATALOG_EXPORT_NETWORK_SECRET` of at least 32 characters. Without it, preflight/creation fail closed with 503; browsing and existing downloads remain available. Set `CATALOG_EXPORT_ALLOWED_ORIGINS` to a JSON array of exact permitted origins. Cookies are HTTPS-only by default (`SESSION_COOKIE_SECURE=false` only for deliberate local HTTP development).

**Deployment prerequisite, not performed automatically:** Uvicorn starts with `--no-proxy-headers`; configure `CATALOG_EXPORT_TRUSTED_PROXY_IPS` as a JSON array of exact stable Traefik peers, verify Traefik sanitizes forwarding headers, and restrict direct application-port access to the intended management interface. Never trust `*` or an entire shared Docker subnet. Untrusted forwarding headers are ignored. Untrusted private proxy/gateway peers fail closed with 503 rather than collapsing all visitors into a falsely verified shared bucket. Shared Traefik/firewall/port changes require separate approval. No production proxy or secret changes are included in this source update.

A dedicated renderer lock and delayed terminal cancellation keep the slot occupied until render-thread/cleanup work drains. Keep one worker replica initially. Python threads cannot be forcibly killed by the job deadline; a lost database connection can release the lock while a thread survives. This is bounded accidental/simple-abuse protection, not DDoS protection or a promise that resource exhaustion is impossible.

## Tech Stack

- **Python 3.11+** / **FastAPI**
- **PostgreSQL** / **SQLAlchemy 2.0** (async) / **Alembic**
- **httpx** for async Shopee API calls
- **APScheduler** for periodic product sync
- **Jinja2** + **htmx** + **Tailwind CSS** for the web dashboard (zero build tools)

## 🚀 Quick Start (Docker)

The easiest way to run everything — PostgreSQL and the FastAPI app — with a single command:

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

This starts the services:

| Service | URL |
|---------|-----|
| **FastAPI app** | http://localhost:8000 |
| **PostgreSQL** | `localhost:5432` (internal) |

Database migrations are applied automatically on startup.

> **Data safety:** `docker compose down` stops containers but preserves your data (the `pgdata` volume persists). Only `docker compose down -v` deletes volumes.

### 3. Login to the dashboard

Visit `http://localhost:8000/` — you'll be redirected to the login page.

- **Default credentials:** `admin` / `admin`
- On first login you'll be prompted to change your password

After login you can:

- Monitor sync status and token health at a glance
- Browse and search products by SKU
- Expand products to view variants
- **Manage discounts** — View, edit, and delete Shopee discounts
- Trigger manual syncs
- Re-authorize your Shopee token

### 4. Authorize with Shopee

Click **"Re-authorize"** in the dashboard's auth panel, or open `http://localhost:8000/api/auth/login` — this returns a Shopee OAuth URL. Open it in your browser, authorize, and the callback will store your tokens.

### 5. Sync products

Click **"Sync Now"** in the dashboard, or via API:

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

## 🏭 Production Deployment with Sandbox

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for a complete guide to deploy **both Sandbox and Production** environments on a VPS.

### Quick Deploy

```bash
# Deploy to sandbox (port 8001)
./deploy.sh sandbox

# Deploy to production (port 8000)
./deploy.sh production
```

## 💰 Discount Management

The dashboard includes a powerful **Discount Management** feature:

### Features
- **View all discounts** — Active, upcoming, and expired
- **Edit ongoing discounts** — Modify prices, end dates, purchase limits
- **Live price pre-fill** — Automatically fetches current promotion prices from Shopee API
- **Stock display** — See current inventory levels while editing
- **Per-model editing** — Edit individual product variants separately

### How to Use
1. Go to **Discounts** page
2. Click on any discount
3. Click **"Edit"** button
4. Modify prices, end date, or purchase limits
5. Click **"Save Changes"**

## 📊 Dashboard Features

The web dashboard provides several panels:

| Panel | Features |
|-------|----------|
| **Sync Status** | Product/variant counts, last sync time, token health indicator, "Sync Now" button. Auto-refreshes every 30s. |
| **Authentication** | Token status badge, expiry countdown, shop ID, "Re-authorize" button. Auto-refreshes every 30s. |
| **Products** | Searchable product table with SKU filtering (300ms debounce), pagination, and expandable variant rows. |
| **Discounts** | List all discounts with status, date range, item count. Edit ongoing discounts with live price sync. |

All interactions use htmx for seamless partial updates without full page reloads.

## 🌐 API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/` | Session | Web dashboard |
| GET | `/login` | — | Login page |
| POST | `/login` | — | Login submit |
| GET | `/logout` | — | Logout (clears session) |
| GET | `/change-password` | Session | Change password page |
| POST | `/change-password` | Session | Change password submit |
| GET | `/discounts` | Session | Discounts list page |
| GET | `/discounts/{id}/edit` | Session | Edit discount page |
| POST | `/discounts/{id}/edit` | Session | Submit discount edits |
| POST | `/discounts/{id}/delete` | Session | Delete discount |
| GET | `/api/products?sku=...` | `X-API-Key` | SKU lookup (single, multiple, or comma-separated) |
| GET | `/api/orders/by-tracking-number?tracking_number=...` | `X-API-Key` | Look up synchronized package and item details by courier tracking number |
| GET | `/api/v2/order/get_order_detail?order_sn_list=...` | `X-API-Key` | Temporary Shopee order-detail proxy (deprecated) |
| GET | `/api/v2/order/get_package_detail?package_number_list=...` | `X-API-Key` | Temporary Shopee package-detail proxy (deprecated) |
| POST | `/api/sync` | `X-API-Key` | Trigger manual product sync |
| GET | `/api/sync/status` | — | Check last sync time and counts |
| GET | `/api/auth/login` | — | Get Shopee OAuth URL |
| GET | `/api/auth/callback` | — | OAuth callback (automatic) |
| GET | `/docs` | — | Swagger UI |

## 📦 Example Response

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

### Tracking Number Lookup

Order and package data is synchronized locally so callers can look up a shipment
using only its courier tracking number:

```bash
curl -G 'http://localhost:8000/api/orders/by-tracking-number' \
  -H 'X-API-Key: your-api-key' \
  --data-urlencode 'tracking_number=YOUR-TRACKING-NUMBER'
```

The response contains the Shopee order and package identifiers, carrier, and
purchase-time item details. During the initial 90-day backfill, an unknown
tracking number returns HTTP 503 rather than a false 404. After backfill,
unknown tracking numbers return HTTP 404.

Operators can run or resume the backfill with:

```bash
python -m app.tasks.order_backfill --days 90
```

The Shopee-shaped proxies remain temporarily during cutover and will be removed
after the synchronized tracking index has been validated.

### Order and Package Detail Proxies (Deprecated)

Fetch an order by its Shopee `order_sn` and request its package information:

```bash
curl -G 'http://localhost:8000/api/v2/order/get_order_detail' \
  -H 'X-API-Key: your-api-key' \
  --data-urlencode 'order_sn_list=ORDER-1,ORDER-2' \
  --data-urlencode 'response_optional_fields=package_list,item_list'
```

Both `order_sn_list` and `response_optional_fields` may also be repeated query
parameters. Use an actual `order_sn`, not a logistics `booking_sn` or tracking
number. The returned `package_list` can contain the `package_number` needed by
the package-detail endpoint.

Callers can then fetch Shopee package details through the configured shop using
the same `X-API-Key` authentication:

```bash
curl -G 'http://localhost:8000/api/v2/order/get_package_detail' \
  -H 'X-API-Key: your-api-key' \
  --data-urlencode 'package_number_list=PKG-1,PKG-2'
```

The query parameter can also be repeated:

```text
?package_number_list=PKG-1&package_number_list=PKG-2
```

The app signs the request with its configured Shopee credentials and forwards
it to `SHOPEE_BASE_URL`. The decoded Shopee JSON response is returned unchanged,
including Shopee error payloads.

## 🧪 Running Tests

```bash
pytest tests/ -v
```

Tests use SQLite (no PostgreSQL required).

## ⚙️ Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `API_KEY` | API key for protected endpoints (sent via `X-API-Key` header) | — |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+asyncpg://postgres:postgres@localhost:5432/shopee_products` |
| `SHOPEE_PARTNER_ID` | Shopee partner ID | — |
| `SHOPEE_PARTNER_KEY` | Shopee partner key | — |
| `SHOPEE_SHOP_ID` | Shopee shop ID | — |
| `SHOPEE_BASE_URL` | Shopee API base URL | `https://partner.shopeemobile.com` |
| `SHOPEE_REDIRECT_URL` | OAuth callback URL | `http://localhost:8000/api/auth/callback` |
| `SESSION_SECRET_KEY` | Secret key for session cookies | `change-me-in-production` |
| `SYNC_INTERVAL_MINUTES` | Product auto-sync interval | `60` |
| `ORDER_SYNC_ENABLED` | Enable order tracking synchronization | `true` |
| `ORDER_SYNC_INTERVAL_MINUTES` | Incremental order-sync interval | `5` |
| `ORDER_SYNC_BACKFILL_DAYS` | Initial tracking history window | `90` |
| `ORDER_SYNC_OVERLAP_MINUTES` | Incremental update overlap | `30` |

## 🛠️ Local Development (without Docker)

If you prefer running the app outside of Docker:

```bash
# Start only PostgreSQL
docker compose up -d postgres

# Install dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Start the server with hot reload
uvicorn app.main:app --reload --port 8000
```

## 📝 License

MIT © [Your Name]
