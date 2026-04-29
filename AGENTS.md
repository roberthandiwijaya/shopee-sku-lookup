# Repository Guidelines

## Project Structure & Module Organization
Core application code lives in `app/`. Keep HTTP endpoints in `app/routers/`, database models in `app/models/`, Pydantic schemas in `app/schemas/`, integration and business logic in `app/services/`, scheduled jobs in `app/tasks/`, and Jinja templates in `app/templates/` with reusable fragments under `app/templates/partials/`. Database migration files belong in `alembic/versions/`. Tests live in `tests/` and mirror feature areas such as `test_auth.py` and `test_products_api.py`.

## Build, Test, and Development Commands
Use Docker for the default local stack:

```bash
docker compose up --build
```

This starts PostgreSQL and the FastAPI app on port `8001` in this sandbox repo. For app-only work, install dependencies with `pip install -r requirements.txt`, then run:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
alembic upgrade head
pytest tests/ -v
```

Use `docker compose up -d postgres` when you only need the database.

## Coding Style & Naming Conventions
Follow existing Python style: 4-space indentation, `snake_case` for functions and variables, `PascalCase` for classes, and concise docstrings on public handlers or service functions. Keep routes thin and move data access or Shopee API behavior into `app/services/`. Match the current async-first design for FastAPI handlers and SQLAlchemy sessions. Template partials use descriptive lowercase names such as `product_table.html` and `sync_status.html`.

## Testing Guidelines
Tests use `pytest` with `pytest-asyncio` (`asyncio_mode = auto`). Prefer `test_*.py` files and descriptive async test names like `test_callback_success`. Reuse fixtures from `tests/conftest.py`; they already provide an ASGI client and SQLite-backed test database. Add coverage for new router behavior, service edge cases, and any migration-sensitive data changes.

## Commit & Pull Request Guidelines
Recent history follows short conventional messages: `feat: ...`, `fix: ...`, `docs: ...`. Keep commits focused and imperative. PRs should explain the user-visible change, note config or migration impact, link the relevant issue, and include screenshots when dashboard templates change.

## Security & Configuration Tips
Secrets are loaded from the repo-root `.env`; do not commit API keys, session secrets, or Shopee tokens. Double-check `SHOPEE_BASE_URL`, callback URLs, and exposed ports before deploying so sandbox and production stay isolated.
