from datetime import datetime

from app.models.product import TZ_GMT7

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_session
from app.models.product import Base, DashboardUser, Product, ProductModel
from app.services.dashboard_auth import hash_password

TEST_API_KEY = "test-api-key-for-testing"
settings.api_key = TEST_API_KEY
settings.session_cookie_secure = False
settings.catalog_export_allowed_origins = ["http://test"]
settings.catalog_export_network_secret = "test-only-export-network-secret-32-bytes"
from app.main import app  # configure test cookie policy before middleware construction

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
    async with AsyncClient(transport=transport, base_url="http://test", headers={"Origin": "http://test"}) as c:
        session = await c.get('/linked-products/export-session')
        c.headers['X-Export-CSRF'] = session.json()['csrf_token']
        yield c


@pytest_asyncio.fixture
async def seed_products():
    """Seed the test database with sample products."""
    now = datetime.now(TZ_GMT7)
    async with TestSession() as session:
        p1 = Product(
            shop_id=1,
            item_id=1001,
            item_sku="PARENT-SKU-1",
            item_name="SPREI Aurora",
            item_status="NORMAL",
            currency="IDR",
            current_price=150000,
            stock=12,
            images=["https://example.com/sprei-aurora-1.jpg", "https://example.com/sprei-aurora-2.jpg"],
            family_code="MP-8002",
            jenis_bahan="Katun Jepang",
            has_model=True,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        p2 = Product(
            shop_id=1,
            item_id=1002,
            item_sku="PARENT-SKU-2",
            item_name="BEDCOVER Aurora",
            item_status="NORMAL",
            currency="IDR",
            current_price=200000,
            stock=4,
            images=["https://example.com/bedcover-aurora-1.jpg"],
            family_code="MP-8002",
            jenis_bahan="Microtex",
            has_model=False,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        p3 = Product(
            shop_id=1,
            item_id=1003,
            item_sku="SOLO-SKU-3",
            item_name="Solo Product ZZ-9001",
            item_status="NORMAL",
            currency="IDR",
            current_price=175000,
            stock=8,
            images=["https://example.com/solo-zz-9001.jpg"],
            family_code="ZZ-9001",
            has_model=False,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        p4 = Product(
            shop_id=1,
            item_id=1004,
            item_sku="PLAIN-SKU-4",
            item_name="Plain Product",
            item_status="NORMAL",
            currency="IDR",
            current_price=99000,
            stock=2,
            has_model=False,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        p5 = Product(
            shop_id=1,
            item_id=1005,
            item_sku="PARENT-SKU-5",
            item_name="BEDCOVER Blossom",
            item_status="NORMAL",
            currency="IDR",
            current_price=210000,
            stock=6,
            images=["https://example.com/bedcover-blossom-1.jpg"],
            family_code="BC-1001",
            jenis_bahan="Tencel",
            has_model=False,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        p6 = Product(
            shop_id=1,
            item_id=1006,
            item_sku="PARENT-SKU-6",
            item_name="Bantal Blossom",
            item_status="NORMAL",
            currency="IDR",
            current_price=90000,
            stock=15,
            images=["https://example.com/bantal-blossom-1.jpg"],
            family_code="BC-1001",
            has_model=False,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        p7 = Product(
            shop_id=1,
            item_id=1007,
            item_sku="PARENT-SKU-7",
            item_name="Sprei Microtex Deluxe",
            item_status="NORMAL",
            currency="IDR",
            current_price=180000,
            stock=None,
            images=["https://example.com/sprei-microtex-1.jpg"],
            family_code="VR-JE9002",
            jenis_bahan="Microtex",
            has_model=True,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        p8 = Product(
            shop_id=1,
            item_id=1008,
            item_sku="PARENT-SKU-8",
            item_name="Bedcover Microtex Deluxe",
            item_status="NORMAL",
            currency="IDR",
            current_price=230000,
            stock=None,
            images=["https://example.com/bedcover-microtex-1.jpg"],
            family_code="VR-JE9002",
            jenis_bahan="Microtex",
            has_model=True,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add_all([p1, p2, p3, p4, p5, p6, p7, p8])
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
        m3 = ProductModel(
            product_id=p7.id,
            item_id=1007,
            model_id=5003,
            model_sku="CHILD-SKU-7A",
            model_name="100x200",
            current_price=185000,
            stock=1,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        m4 = ProductModel(
            product_id=p7.id,
            item_id=1007,
            model_id=5004,
            model_sku="CHILD-SKU-7B",
            model_name="120x200",
            current_price=190000,
            stock=3,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        m5 = ProductModel(
            product_id=p8.id,
            item_id=1008,
            model_id=5005,
            model_sku="CHILD-SKU-8A",
            model_name="160x240",
            current_price=235000,
            stock=1,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        m6 = ProductModel(
            product_id=p8.id,
            item_id=1008,
            model_id=5006,
            model_sku="CHILD-SKU-8B",
            model_name="180x200",
            current_price=240000,
            stock=0,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add_all([m1, m2, m3, m4, m5, m6])
        await session.commit()


@pytest_asyncio.fixture
async def dashboard_user():
    now = datetime.now(TZ_GMT7)
    password_hash, salt = hash_password("secret123")
    async with TestSession() as session:
        user = DashboardUser(
            username="admin",
            password_hash=password_hash,
            salt=salt,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.commit()


@pytest_asyncio.fixture
async def logged_in_client(client, dashboard_user):
    resp = await client.post("/login", data={"username": "admin", "password": "secret123"}, follow_redirects=False)
    assert resp.status_code == 303
    yield client
