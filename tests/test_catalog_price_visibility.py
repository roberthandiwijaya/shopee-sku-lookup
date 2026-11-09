import io
import zipfile
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfReader
from sqlalchemy import update

from app.config import settings
from app.models.catalog_export import CatalogExportJob
from app.services import catalog_pdf_service as pdf
from app.tasks import catalog_export_worker as worker_module
from tests.conftest import TestSession


def text_of(data):
    return '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


@pytest.mark.parametrize('show_stock', [True, False])
@pytest.mark.parametrize('show_prices', [True, False])
def test_price_visibility_on_all_pages(show_stock, show_prices):
    cards = [{'family_code': 'TEST-001', 'jenis_bahan': 'Cotton', 'primary_image_url': None,
              'products': [{'name': 'Long product with wrapped variant names', 'rows': [
                  {'name': f'Variant {i:03d} with a longer descriptive size', 'price': 123456, 'stock': 4}
                  for i in range(85)
              ]}]}]
    reader = PdfReader(io.BytesIO(pdf.render_catalog_pdf(cards, show_prices=show_prices, show_stock=show_stock)))
    assert len(reader.pages) > 1
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert 'Variant 084' in text
    assert ('4 pcs' if show_stock else 'Available') in text
    for page in reader.pages:
        page_text = page.extract_text()
        if show_prices:
            if 'Variant' in page_text:
                assert 'PRICE' in page_text and 'Rp123.456' in page_text
        else:
            assert 'PRICE' not in page_text and 'Rp123.456' not in page_text
            assert 'Prices and stock' not in page_text


@pytest.fixture
def job_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'catalog_export_dir', str(tmp_path))
    monkeypatch.setattr(worker_module, 'async_session', TestSession)
    monkeypatch.setattr(pdf, 'fetch_catalog_images', AsyncMock(return_value={}))


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['single', 'split'])
async def test_background_price_free_catalog(client, seed_products, job_environment, mode):
    response = await client.post('/linked-products/exports', json={
        'family_codes':['ZZ-9001'], 'show_prices':False, 'output_mode':mode})
    assert response.status_code == 202
    job = response.json()
    async with TestSession() as session:
        row = await session.get(CatalogExportJob, job['id'])
        assert row.options['show_prices'] is False
    await worker_module.CatalogExportWorker().run_once()
    headers = {'Authorization':'Bearer '+job['token']}
    state = (await client.get('/linked-products/exports/'+job['id'],headers=headers)).json()
    assert state['status'] == 'ready', state
    for file in state['files']:
        response = await client.post(f"/linked-products/exports/{job['id']}/files/{file['name']}/download",data={'token':job['token']})
        assert response.status_code == 200
        if file['name'].endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                texts = [text_of(archive.read(name)) for name in archive.namelist()]
        else:
            texts = [text_of(response.content)]
        for text in texts:
            assert 'PRICE' not in text and 'Rp175.000' not in text
            assert '8 pcs' in text and 'Solo Product' in text


@pytest.mark.asyncio
async def test_old_job_without_option_keeps_prices(client, seed_products, job_environment):
    response = await client.post('/linked-products/exports', json={'family_codes':['ZZ-9001']})
    job = response.json()
    async with TestSession() as session:
        row = await session.get(CatalogExportJob, job['id'])
        options = dict(row.options)
        assert options.pop('show_prices') is True
        await session.execute(update(CatalogExportJob).where(CatalogExportJob.id==job['id']).values(options=options))
        await session.commit()
    await worker_module.CatalogExportWorker().run_once()
    response = await client.post(f"/linked-products/exports/{job['id']}/files/catalog.pdf/download",data={'token':job['token']})
    assert response.status_code == 200
    assert 'Rp175.000' in text_of(response.content)


@pytest.mark.asyncio
async def test_sync_export_can_hide_prices_and_defaults_to_shown(client, seed_products, monkeypatch):
    monkeypatch.setattr(pdf, 'fetch_catalog_images', AsyncMock(return_value={}))
    for option in [None, False]:
        payload = {'family_codes':['ZZ-9001']}
        if option is not None:
            payload['show_prices'] = option
        response = await client.post('/linked-products/export.pdf',json=payload)
        assert response.status_code == 200
        assert ('Rp175.000' in text_of(response.content)) is (option is None)


@pytest.mark.asyncio
async def test_price_checkbox_checked_and_payload_present(client, seed_products):
    text = (await client.get('/linked-products')).text
    assert 'id="export-show-prices" type="checkbox" checked' in text
    assert "show_prices:byId('export-show-prices').checked" in text
