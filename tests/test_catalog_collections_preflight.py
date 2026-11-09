from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from app.main import app
from app.models.catalog_collection import CatalogCollection
from app.models.catalog_export import CatalogExportJob
from app.models.product import DashboardUser, Product, TZ_GMT7
from app.services.dashboard_auth import hash_password
from tests.conftest import TestSession

pytestmark = pytest.mark.asyncio


async def csrf_headers(client):
    response = await client.get('/linked-products/collections')
    assert response.status_code == 200, response.text
    return {'X-CSRF-Token': response.json()['csrf_token']}


async def test_collection_crud_and_csrf(logged_in_client):
    client = logged_in_client
    payload = {'name': '  Favorites  ', 'family_codes': [' mp-8002 ', 'MP-8002'], 'show_prices': False}
    assert (await client.post('/linked-products/collections', json=payload)).status_code == 403
    headers = await csrf_headers(client)
    response = await client.post('/linked-products/collections', json=payload, headers=headers)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved['name'] == 'Favorites'
    assert saved['options']['family_codes'] == ['MP-8002']
    assert saved['options']['show_prices'] is False
    assert (await client.post('/linked-products/collections', json=payload, headers=headers)).status_code == 409
    listed = (await client.get('/linked-products/collections')).json()['collections']
    assert len(listed) == 1
    url = '/linked-products/collections/' + saved['id']
    payload['name'] = 'Updated'
    assert (await client.put(url, json=payload, headers=headers)).status_code == 200
    assert (await client.delete(url, headers=headers)).status_code == 200
    assert (await client.get('/linked-products/collections')).json()['collections'] == []


async def test_collections_private_across_users(logged_in_client):
    headers = await csrf_headers(logged_in_client)
    payload = {'name': 'Private', 'family_codes': ['MP-8002']}
    saved = (await logged_in_client.post('/linked-products/collections', json=payload, headers=headers)).json()
    password_hash, salt = hash_password('other-password')
    async with TestSession() as session:
        session.add(DashboardUser(username='other', password_hash=password_hash, salt=salt, must_change_password=False))
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as other:
        assert (await other.get('/linked-products/collections')).status_code == 401
        await other.post('/login', data={'username':'other', 'password':'other-password'})
        own_headers = await csrf_headers(other)
        assert (await other.get('/linked-products/collections')).json()['collections'] == []
        url = '/linked-products/collections/' + saved['id']
        assert (await other.put(url, json=payload, headers=own_headers)).status_code == 404
        assert (await other.delete(url, headers=own_headers)).status_code == 404


async def test_collection_invalid_and_limit(logged_in_client):
    headers = await csrf_headers(logged_in_client)
    for payload in [{'name':' ', 'family_codes':['A']}, {'name':'Valid','family_codes':[' ']},
                    {'name':'Valid','family_codes':['X'*101]}, {'name':'Valid','family_codes':[]}]:
        assert (await logged_in_client.post('/linked-products/collections', json=payload, headers=headers)).status_code == 422
    async with TestSession() as session:
        user_id = await session.scalar(select(DashboardUser.id))
        session.add_all([CatalogCollection(user_id=user_id, name=f'Collection {i}', options={}) for i in range(50)])
        await session.commit()
    response = await logged_in_client.post('/linked-products/collections', headers=headers,
                                           json={'name':'Too many','family_codes':['A']})
    assert response.status_code == 409


async def test_preflight_missing_excluded_and_no_job(client, seed_products):
    response = await client.post('/linked-products/exports/preflight', json={
        'family_codes':['mp-8002', 'ZZ-9001', 'MISSING'], 'min_stock':10})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['missing'] == ['MISSING']
    assert result['excluded'] == ['ZZ-9001']
    assert result['matching_rows'] == 1
    assert not result['can_export']
    assert response.headers['cache-control'] == 'no-store'
    async with TestSession() as session:
        assert await session.scalar(select(func.count()).select_from(CatalogExportJob)) == 0


async def test_preflight_matches_export_rows_and_freshness(client, seed_products):
    from app.services.catalog_pdf_service import get_export_cards
    codes = ['MP-8002', 'ZZ-9001']
    async with TestSession() as session:
        await session.execute(update(Product).where(Product.family_code == 'ZZ-9001').values(
            synced_at=datetime.now(TZ_GMT7) - timedelta(days=1)))
        await session.commit()
        cards, omitted = await get_export_cards(session, codes, stock_mode='all')
    response = await client.post('/linked-products/exports/preflight', json={'family_codes':codes, 'stock_mode':'all'})
    result = response.json()
    assert result['can_export']
    assert result['matching_rows'] == sum(len(p['rows']) for c in cards for p in c['products'])
    assert len(result['excluded']) == omitted
    assert result['source_oldest_synced_at']
    assert any('two hours' in warning for warning in result['warnings'])


async def test_preflight_empty_and_invalid(client, seed_products):
    result = (await client.post('/linked-products/exports/preflight', json={
        'family_codes':['ZZ-9001'], 'min_stock':999999})).json()
    assert not result['can_export'] and result['excluded'] == ['ZZ-9001']
    assert (await client.post('/linked-products/exports/preflight', json={'family_codes':[]})).status_code == 422


async def test_catalog_controls(client, logged_in_client, seed_products):
    page = await logged_in_client.get('/linked-products')
    assert 'collection-save' in page.text
    assert 'export-preflight' in page.text
    assert 'Confirm and generate PDF' in page.text
    assert 'textContent = message' in page.text
