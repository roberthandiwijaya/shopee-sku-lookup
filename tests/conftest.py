from datetime import datetime, timezone

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_session
from app.main import app
from app.models.product import Base, Product, ProductModel

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _override_get_session():
    async with TestSession() as session:
        yield session


app.dependency_overrides[get_session] = _override_get_session


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seed_products():
    """Seed the test database with sample products."""
    now = datetime.now(timezone.utc)
    async with TestSession() as session:
        p1 = Product(
            shop_id=1,
            item_id=1001,
            item_sku="PARENT-SKU-1",
            item_name="Test Product 1",
            item_status="NORMAL",
            currency="IDR",
            current_price=150000,
            has_model=True,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        p2 = Product(
            shop_id=1,
            item_id=1002,
            item_sku="PARENT-SKU-2",
            item_name="Test Product 2",
            item_status="NORMAL",
            currency="IDR",
            current_price=200000,
            has_model=False,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add_all([p1, p2])
        await session.flush()

        m1 = ProductModel(
            product_id=p1.id,
            item_id=1001,
            model_id=5001,
            model_sku="CHILD-SKU-1A",
            model_name="Red / S",
            current_price=155000,
            stock=10,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        m2 = ProductModel(
            product_id=p1.id,
            item_id=1001,
            model_id=5002,
            model_sku="CHILD-SKU-1B",
            model_name="Blue / M",
            current_price=160000,
            stock=5,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add_all([m1, m2])
        await session.commit()
