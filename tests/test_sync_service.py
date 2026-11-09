"""Fail-closed catalog reconciliation; all Shopee traffic is mocked."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.product import Product, ProductModel, TZ_GMT7
from app.services import sync_service as sync
from tests.conftest import TestSession


def base(item_id=1001, has_model=True):
    return {"item_id": item_id, "has_model": has_model, "item_name": "Updated", "item_status": "NORMAL"}


def model(model_id=5001):
    return {"model_id": model_id, "model_sku": "NEW", "price_info": [
        {"original_price": 100, "current_price": 90}],
        "stock_info_v2": {"summary_info": {"total_available_stock": 3}}}


def envelope(field, records, **extra):
    return {"response": {field: records, **extra}}


@pytest.fixture(autouse=True)
def mock_session(monkeypatch):
    monkeypatch.setattr(sync, "async_session", TestSession)


async def snapshot():
    async with TestSession() as session:
        products = list(await session.scalars(select(Product).order_by(Product.item_id)))
        variants = list(await session.scalars(select(ProductModel).order_by(ProductModel.model_id)))
        return ([(p.item_id, p.item_name, p.synced_at, p.has_model) for p in products],
                [(m.model_id, m.model_sku, m.synced_at, m.is_active) for m in variants])


@pytest.mark.parametrize("bad", [
    {}, {"error": "denied", "response": {"item_list": []}},
    envelope("item_list", []), envelope("item_list", [base(), base()]),
    envelope("item_list", [base(999)]), envelope("item_list", [dict(base(), has_model=1)]),
    envelope("item_list", [dict(base(), has_model=None)]),
    envelope("item_list", [dict(base(), item_status=None)]),
    envelope("item_list", [dict(base(), item_status="UNKNOWN")]),
    {"response": {"item_list": None}},
    dict(envelope("item_list", [base()]), warning="partial response"),
])
async def test_invalid_base_leaves_entire_batch_untouched(seed_products, monkeypatch, bad):
    before = await snapshot()
    monkeypatch.setattr(sync.shopee_client, "shop_request", AsyncMock(return_value=bad))
    with pytest.raises(sync.SyncValidationError):
        await sync._sync_item_batch("secret", 1, [1001])
    assert await snapshot() == before


@pytest.mark.parametrize("bad_models", [
    {}, envelope("model", []), envelope("model", None),
    envelope("model", [model(), model()]), envelope("model", [{"model_id": 5001}]),
    envelope("model", [dict(model(), model_id="5001")]),
    envelope("model", [dict(model(), price_info=[])]),
    envelope("model", [dict(model(), stock_info_v2={})]),
    dict(envelope("model", [model()]), warning="partial"),
])
async def test_invalid_models_do_not_archive_or_freshen(seed_products, monkeypatch, bad_models):
    before = await snapshot()
    monkeypatch.setattr(sync.shopee_client, "shop_request", AsyncMock(side_effect=[
        envelope("item_list", [base()]), bad_models,
    ]))
    with pytest.raises(sync.SyncValidationError):
        await sync._sync_item_batch("secret", 1, [1001])
    assert await snapshot() == before


async def test_confirmed_models_archive_and_can_reactivate(seed_products, monkeypatch):
    request = AsyncMock(side_effect=[envelope("item_list", [base()]),
                                   envelope("model", [model()]), envelope("model", [model()])])
    monkeypatch.setattr(sync.shopee_client, "shop_request", request)
    await sync._sync_item_batch("secret", 1, [1001])
    async with TestSession() as session:
        p = await session.scalar(select(Product).where(Product.item_id == 1001))
        assert p.family_code == "MP-8002" and p.jenis_bahan == "Katun Jepang"
        assert p.current_price == 150000 and p.stock == 12
        variants = {m.model_id: m for m in await session.scalars(
            select(ProductModel).where(ProductModel.product_id == p.id))}
        assert variants[5001].is_active and variants[5001].current_price == 90
        assert not variants[5002].is_active
        old_synced = variants[5002].synced_at
    request.side_effect = [envelope("item_list", [base()]), envelope("model", [model(5002)]),
                           envelope("model", [model(5002)])]
    await sync._sync_item_batch("secret", 1, [1001])
    async with TestSession() as session:
        variants = {m.model_id: m for m in await session.scalars(select(ProductModel))}
        assert variants[5002].is_active and variants[5002].synced_at != old_synced
        assert not variants[5001].is_active


async def test_model_confirmation_must_match_exactly(seed_products, monkeypatch):
    before = await snapshot()
    monkeypatch.setattr(sync.shopee_client, "shop_request", AsyncMock(side_effect=[
        envelope("item_list", [base()]), envelope("model", [model()]),
        envelope("model", [model(5002)]),
    ]))
    with pytest.raises(sync.SyncValidationError):
        await sync._sync_item_batch("secret", 1, [1001])
    assert await snapshot() == before


@pytest.mark.parametrize("confirmation,success", [
    (envelope("item_list", [base(has_model=False)]), True),
    (envelope("item_list", [base(has_model=True)]), False),
    (envelope("item_list", []), False),
    ({"error": "oops"}, False),
])
async def test_false_has_model_requires_independent_confirmation(seed_products, monkeypatch, confirmation, success):
    before = await snapshot()
    request = AsyncMock(side_effect=[envelope("item_list", [base(has_model=False)]), confirmation])
    monkeypatch.setattr(sync.shopee_client, "shop_request", request)
    if not success:
        with pytest.raises(sync.SyncValidationError):
            await sync._sync_item_batch("secret", 1, [1001])
        assert await snapshot() == before
    else:
        await sync._sync_item_batch("secret", 1, [1001])
        async with TestSession() as session:
            variants = list(await session.scalars(select(ProductModel).where(ProductModel.item_id == 1001)))
            assert len(variants) == 2 and all(not m.is_active for m in variants)
    assert request.await_count == 2


async def test_failure_later_in_batch_rolls_back_all_products(seed_products, monkeypatch):
    before = await snapshot()
    monkeypatch.setattr(sync.shopee_client, "shop_request", AsyncMock(side_effect=[
        envelope("item_list", [base(), base(1002)]), envelope("model", [model()]),
        envelope("model", [model()]), envelope("model", []),
    ]))
    with pytest.raises(sync.SyncValidationError):
        await sync._sync_item_batch("secret", 1, [1001, 1002])
    assert await snapshot() == before


async def test_persistence_error_rolls_back_prior_writes_in_batch(seed_products, monkeypatch):
    before = await snapshot()
    invalid = dict(base(1002, has_model=False), image={"image_url_list": "not-a-list"})
    monkeypatch.setattr(sync.shopee_client, "shop_request", AsyncMock(side_effect=[
        envelope("item_list", [base(has_model=False), invalid]),
        envelope("item_list", [base(has_model=False), base(1002, has_model=False)]),
    ]))
    with pytest.raises(sync.SyncValidationError):
        await sync._sync_item_batch("secret", 1, [1001, 1002])
    assert await snapshot() == before


async def test_status_union_and_local_ids_are_directly_refetched(seed_products, monkeypatch):
    monkeypatch.setattr(sync.settings, "shopee_shop_id", 1)
    monkeypatch.setattr(sync, "get_valid_token", AsyncMock(return_value="secret"))
    calls = []

    async def request(path, **kwargs):
        calls.append(kwargs["params"])
        assert path.endswith("get_item_list")
        status = kwargs["params"]["item_status"]
        return envelope("item", [{"item_id": {"NORMAL": 1001, "UNLIST": 9001, "BANNED": 9002}[status]}], has_next_page=False)

    monkeypatch.setattr(sync.shopee_client, "shop_request", request)
    batch = AsyncMock()
    monkeypatch.setattr(sync, "_sync_item_batch", batch)
    await sync.run_sync()
    assert [p["item_status"] for p in calls] == ["NORMAL", "UNLIST", "BANNED"]
    assert batch.await_args.args[2] == list(range(1001, 1009)) + [9001, 9002]


@pytest.mark.parametrize("payload", [
    {}, envelope("item", [], has_next_page=True), envelope("item", [], has_next_page=0),
    envelope("item", [{"item_id": 1}, {"item_id": 1}], has_next_page=False),
])
async def test_invalid_listing_fails(monkeypatch, payload):
    monkeypatch.setattr(sync.shopee_client, "shop_request", AsyncMock(return_value=payload))
    with pytest.raises(sync.SyncValidationError):
        await sync._fetch_all_item_ids("secret", 1)


def test_only_exact_shipping_warning_is_allowed_on_base():
    warning = "fail to get channel estimated_shipping_fee for channel [8003];\nfail to get channel estimated_shipping_fee for channel [8005]"
    payload = dict(envelope("item_list", [base()]), warning=warning)
    assert sync._response(payload, allow_shipping_warning=True)
    with pytest.raises(sync.SyncValidationError):
        sync._response(payload)
    payload["warning"] += "\npartial result"
    with pytest.raises(sync.SyncValidationError):
        sync._response(payload, allow_shipping_warning=True)


async def test_local_metadata_edit_during_upstream_fetch_survives(seed_products, monkeypatch):
    calls = 0

    async def request(path, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return envelope("item_list", [base()])
        if calls == 3:
            async with TestSession() as session:
                product = await session.scalar(select(Product).where(Product.item_id == 1001))
                product.family_code = "MANUAL"
                product.jenis_bahan = "Manual material"
                await session.commit()
        return envelope("model", [model()])

    monkeypatch.setattr(sync.shopee_client, "shop_request", request)
    await sync._sync_item_batch("secret", 1, [1001])
    async with TestSession() as session:
        product = await session.scalar(select(Product).where(Product.item_id == 1001))
        assert product.family_code == "MANUAL"
        assert product.jenis_bahan == "Manual material"


async def test_pagination_rejects_repeated_page(monkeypatch):
    page = envelope("item", [{"item_id": 1}], has_next_page=True)
    request = AsyncMock(return_value=page)
    monkeypatch.setattr(sync.shopee_client, "shop_request", request)
    with pytest.raises(sync.SyncValidationError):
        await sync._fetch_all_item_ids("secret", 1)
    assert request.await_count == 2


async def test_missing_local_item_does_not_freshen_or_delete(seed_products, monkeypatch):
    before = await snapshot()
    monkeypatch.setattr(sync.settings, "shopee_shop_id", 1)
    monkeypatch.setattr(sync, "get_valid_token", AsyncMock(return_value="secret"))
    async def response(path, **kwargs):
        if path.endswith("get_item_list"):
            return envelope("item", [], has_next_page=False)
        return envelope("item_list", [])

    request = AsyncMock(side_effect=response)
    monkeypatch.setattr(sync.shopee_client, "shop_request", request)
    with pytest.raises(sync.SyncValidationError, match="8 item"):
        await sync.run_sync()
    assert request.await_args_list[3].kwargs["params"]["item_id_list"] == ",".join(map(str, range(1001, 1009)))
    assert await snapshot() == before


async def test_missing_item_does_not_starve_valid_items_or_later_batches(seed_products, monkeypatch):
    monkeypatch.setattr(sync.settings, "shopee_shop_id", 1)
    monkeypatch.setattr(sync, "ITEM_DETAIL_BATCH_SIZE", 2)
    monkeypatch.setattr(sync, "get_valid_token", AsyncMock(return_value="secret"))
    missing_before = (await snapshot())[0][0]
    seen = []

    async def request(path, **kwargs):
        if path.endswith("get_item_list"):
            return envelope("item", [], has_next_page=False)
        ids = list(map(int, kwargs["params"]["item_id_list"].split(",")))
        seen.append(ids)
        return envelope("item_list", [base(i, has_model=False) for i in ids if i != 1001])

    monkeypatch.setattr(sync.shopee_client, "shop_request", request)
    with pytest.raises(sync.SyncValidationError, match="1 item"):
        await sync.run_sync()
    after = (await snapshot())[0]
    assert after[0] == missing_before
    assert all(p[1] == "Updated" for p in after[1:])
    assert [1001] in seen and [1002] in seen and [1007, 1008] in seen


async def test_transport_error_does_not_expose_signed_url(monkeypatch):
    monkeypatch.setattr(sync.shopee_client, "shop_request", AsyncMock(side_effect=RuntimeError("https://api/?access_token=secret")))
    with pytest.raises(sync.SyncValidationError, match="^Shopee request failed$"):
        await sync._fetch_base("secret", 1, [1001])


async def test_sqlite_lock_serializes_and_releases_after_failure():
    entered = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def first():
        with pytest.raises(RuntimeError):
            async with sync._sync_lock(1):
                order.append(1)
                entered.set()
                await release.wait()
                raise RuntimeError("failed")

    async def second():
        await entered.wait()
        async with sync._sync_lock(1):
            order.append(2)

    task1 = asyncio.create_task(first())
    await entered.wait()
    task2 = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert order == [1]
    release.set()
    await asyncio.gather(task1, task2)
    assert order == [1, 2]
