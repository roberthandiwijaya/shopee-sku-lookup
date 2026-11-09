import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import auth, bulk_products, catalog_tools, dashboard, discounts, login, orders, products, public, webhook
from app.services.dashboard_auth import LoginRequired
from app.tasks.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Shopee Product SKU Lookup API",
    description="REST API for looking up Shopee products by SKU",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key)


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


app.include_router(login.router)
app.include_router(bulk_products.router)
app.include_router(catalog_tools.router)
app.include_router(public.router)
app.include_router(dashboard.router)
app.include_router(products.router)
app.include_router(discounts.router)
app.include_router(orders.router)
app.include_router(auth.router)
app.include_router(webhook.router)
