import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routers import auth, products
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

app.include_router(products.router)
app.include_router(auth.router)


@app.get("/")
async def root():
    return {"message": "Shopee SKU Lookup API", "docs": "/docs"}
