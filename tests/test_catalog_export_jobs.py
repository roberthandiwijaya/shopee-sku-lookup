import io
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfReader
from sqlalchemy import update

from app.config import settings
from app.models.catalog_export import CatalogExportJob
from app.models.product import Product
from app.services import catalog_export_service as jobs, catalog_pdf_service as pdf
from app.tasks import catalog_export_worker as worker_module
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
def export_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'catalog_export_dir', str(tmp_path))
    monkeypatch.setattr(worker_module, 'async_session', TestSession)
    monkeypatch.setattr(pdf, 'fetch_catalog_images', AsyncMock(return_value={}))


async def create(client, codes=None, **options):
    response = await client.post('/linked-products/exports', json={
        'family_codes': codes or ['ZZ-9001'], 'stock_mode':'all', **options})
    assert response.status_code == 202, response.text
    data = response.json()
    return data['id'], {'Authorization': 'Bearer ' + data['token']}


@pytest.mark.asyncio
async def test_worker_download_capability_and_persistence(client, seed_products):
    id, headers = await create(client)
    assert (await client.get(f'/linked-products/exports/{id}')).status_code == 404
    assert (await client.get(f'/linked-products/exports/{id}', headers={'Authorization':'Bearer wrong'})).status_code == 404
    assert (await worker_module.CatalogExportWorker().run_once()) is True
    response = await client.get(f'/linked-products/exports/{id}', headers=headers)
    assert response.json()['status'] == 'ready', response.text
    assert response.json()['progress'] == 1
    assert response.json()['warnings']
    download = await client.get(f'/linked-products/exports/{id}/files/catalog.pdf', headers=headers)
    assert download.status_code == 200
    assert 'Solo Product' in PdfReader(io.BytesIO(download.content)).pages[0].extract_text()
    assert (await client.get(f'/linked-products/exports/{id}/files/other.pdf', headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_native_post_download_preserves_security_and_streaming(client, seed_products):
    id, headers = await create(client)
    url = f'/linked-products/exports/{id}/files/catalog.pdf/download'
    token = headers['Authorization'].removeprefix('Bearer ')
    assert (await client.post(url, data={'token': token})).status_code == 404
    await worker_module.CatalogExportWorker().run_once()
    assert (await client.post(url, data={'token': 'wrong'})).status_code == 404
    assert (await client.post(url, data={})).status_code == 422
    assert (await client.get(url, params={'token': token})).status_code == 405
    response = await client.post(url, data={'token': token})
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    assert 'attachment' in response.headers['content-disposition']
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert int(response.headers['content-length']) == len(response.content)
    assert 'Solo Product' in PdfReader(io.BytesIO(response.content)).pages[0].extract_text()
    unknown = f'/linked-products/exports/{id}/files/missing.pdf/download'
    assert (await client.post(unknown,data={'token':token})).status_code == 404
    async with TestSession() as session:
        await session.execute(update(CatalogExportJob).where(CatalogExportJob.id==id).values(expires_at=jobs.now()-timedelta(seconds=1)))
        await session.commit()
    assert (await client.post(url,data={'token':token})).status_code == 410


@pytest.mark.asyncio
async def test_download_ui_does_not_buffer_files(client, seed_products):
    response = await client.get('/linked-products')
    assert "nativeForm.method = 'POST'" in response.text
    assert 'nativeForm.submit()' in response.text
    assert 'response.blob()' not in response.text
    assert 'URL.createObjectURL' not in response.text


@pytest.mark.asyncio
async def test_split_after_filter_and_zip(client, seed_products):
    id, headers = await create(client, ['ZZ-9001','BC-1001'], output_mode='split', show_stock=False)
    await worker_module.CatalogExportWorker().run_once()
    state = (await client.get(f'/linked-products/exports/{id}', headers=headers)).json()
    assert state['status'] == 'ready', state
    assert [file['name'] for file in state['files']] == ['catalog-01.pdf','catalog-volumes.zip']
    response = await client.post(f'/linked-products/exports/{id}/files/catalog-volumes.zip/download',
                                data={'token': headers['Authorization'].removeprefix('Bearer ')})
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/zip'
    import zipfile
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ['catalog-01.pdf']
        text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(archive.read('catalog-01.pdf'))).pages)
        assert ' pcs' not in text


@pytest.mark.asyncio
async def test_cancel_and_expiry_cleanup(client, seed_products):
    id, headers = await create(client)
    await client.post(f'/linked-products/exports/{id}/cancel', headers=headers)
    assert await worker_module.CatalogExportWorker().run_once() is False
    async with TestSession() as session:
        await session.execute(update(CatalogExportJob).where(CatalogExportJob.id==id).values(expires_at=jobs.now()-timedelta(seconds=1)))
        await session.commit()
        directory = jobs.safe_directory(id)
        directory.mkdir()
        (directory/'test').write_bytes(b'test')
        await jobs.cleanup_jobs(session)
        assert not directory.exists()
    assert (await client.get(f'/linked-products/exports/{id}',headers=headers)).status_code == 410


@pytest.mark.asyncio
async def test_reclaim_fences_old_worker(client, seed_products):
    id, _ = await create(client)
    async with TestSession() as session:
        first = await jobs.claim_job(session,'first')
        assert first.id == id
        assert await jobs.claim_job(session,'second') is None
        await session.execute(update(CatalogExportJob).where(CatalogExportJob.id==id).values(lease_until=jobs.now()-timedelta(seconds=1)))
        await session.commit()
        second = await jobs.claim_job(session,'second')
        assert second.attempts == 2
        assert not await jobs.update_job(session,id,'first',status='ready')
        assert await jobs.update_job(session,id,'second',status='failed')


@pytest.mark.asyncio
async def test_queue_per_owner_limit(client, seed_products):
    await create(client)
    await create(client)
    response = await client.post('/linked-products/exports', json={'family_codes':['ZZ-9001']})
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_limits_and_worker_failure(client, seed_products):
    assert (await client.post('/linked-products/exports',json={'family_codes':['A']*251})).status_code == 422
    id, headers = await create(client,['MISSING'])
    await worker_module.CatalogExportWorker().run_once()
    state = (await client.get(f'/linked-products/exports/{id}',headers=headers)).json()
    assert state['status'] == 'failed'
    assert 'no longer exist' in state['error']


@pytest.mark.asyncio
async def test_fifty_one_families_make_two_volumes(client, seed_products):
    async with TestSession() as session:
        for i in range(51):
            session.add(Product(shop_id=1,item_status='NORMAL',item_id=20000+i,item_name=f'Family {i}',family_code=f'B-{i:03}',stock=3,current_price=1000))
        await session.commit()
    id, headers = await create(client,[f'B-{i:03}' for i in range(51)],output_mode='split')
    await worker_module.CatalogExportWorker().run_once()
    state = (await client.get(f'/linked-products/exports/{id}',headers=headers)).json()
    assert state['status'] == 'ready', state
    assert [file['name'] for file in state['files']] == ['catalog-01.pdf','catalog-02.pdf','catalog-volumes.zip']
    for name, pages in [('catalog-01.pdf',50),('catalog-02.pdf',1)]:
        response = await client.get(f'/linked-products/exports/{id}/files/{name}',headers=headers)
        assert len(PdfReader(io.BytesIO(response.content)).pages) == pages


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [10, 100, 250])
async def test_large_exports_complete_all_families(client, seed_products, monkeypatch, count):
    import time
    from PIL import Image
    image = io.BytesIO()
    Image.new('RGB', (1200, 1200), '#236653').save(image, 'JPEG')
    async def photo(cards):
        return {'https://cf.shopee.co.id/test': image.getvalue()}
    monkeypatch.setattr(pdf, 'fetch_catalog_images', photo)
    async with TestSession() as session:
        for i in range(count):
            session.add(Product(shop_id=1,item_status='NORMAL',item_id=30000+i,item_name=f'Benchmark family {i}',
                family_code=f'L-{i:03}',stock=3,current_price=1000,
                images=['https://cf.shopee.co.id/test']))
        await session.commit()
    id, headers = await create(client,[f'L-{i:03}' for i in range(count)])
    started = time.monotonic()
    await worker_module.CatalogExportWorker().run_once()
    state = (await client.get(f'/linked-products/exports/{id}',headers=headers)).json()
    assert state['status'] == 'ready', state
    response = await client.get(f'/linked-products/exports/{id}/files/catalog.pdf',headers=headers)
    reader = PdfReader(io.BytesIO(response.content))
    assert len(reader.pages) == count
    assert all(len(page.images) == 1 for page in reader.pages)
    assert f'Benchmark family {count-1}' in reader.pages[-1].extract_text()
    print(f'BENCHMARK families={count} seconds={time.monotonic()-started:.2f} bytes={len(response.content)}')
