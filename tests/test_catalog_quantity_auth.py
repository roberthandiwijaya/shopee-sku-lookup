import io
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader
from sqlalchemy import select, func

from app.config import settings
from app.main import app
from app.models.catalog_export import CatalogExportJob
from app.models.product import DashboardUser
from app.tasks import catalog_export_worker as worker
from app.services import catalog_pdf_service as pdf
from tests.conftest import TestSession

pytestmark = pytest.mark.asyncio


async def test_guest_defaults_to_labels_and_cannot_force_pcs(client, seed_products):
    bootstrap = await client.get('/linked-products/export-session')
    assert bootstrap.json()['can_export_quantities'] is False
    payload={'family_codes':['ZZ-9001'],'show_stock':True,'quantity_admin_user_id':1}
    for url in ['/linked-products/exports','/linked-products/exports/preflight']:
        assert (await client.post(url,json=payload)).status_code==403
    async with TestSession() as session:
        assert await session.scalar(select(func.count()).select_from(CatalogExportJob))==0
    payload.pop('show_stock')
    response=await client.post('/linked-products/exports',json=payload)
    assert response.status_code==202,response.text
    async with TestSession() as session:
        job=await session.get(CatalogExportJob,response.json()['id'])
        assert job.options['show_stock'] is False
        assert 'quantity_admin_user_id' not in job.options


async def test_admin_pcs_accepted_and_rendered(logged_in_client,seed_products,monkeypatch,tmp_path):
    client=logged_in_client
    assert (await client.get('/linked-products/export-session')).json()['can_export_quantities'] is True
    monkeypatch.setattr(settings,'catalog_export_dir',str(tmp_path))
    monkeypatch.setattr(worker,'async_session',TestSession)
    monkeypatch.setattr(pdf,'fetch_catalog_images',AsyncMock(return_value={}))
    response=await client.post('/linked-products/exports',json={'family_codes':['ZZ-9001'],'show_stock':True})
    assert response.status_code==202,response.text
    job=response.json()
    async with TestSession() as session:
        stored=await session.get(CatalogExportJob,job['id'])
        assert type(stored.options['quantity_admin_user_id']) is int
    await worker.CatalogExportWorker().run_once()
    result=await client.post(f"/linked-products/exports/{job['id']}/files/catalog.pdf/download",data={'token':job['token']})
    assert result.status_code==200
    text='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(result.content)).pages)
    assert '8 pcs' in text
    assert 'Mohon konfirmasi ketersediaan stok sebelum memesan.' in ' '.join(text.split())
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as guest:
        denied=await guest.post(f"/linked-products/exports/{job['id']}/files/catalog.pdf/download",data={'token':job['token']})
        assert denied.status_code==403


async def test_nonadmin_and_password_change_required_rejected(logged_in_client,seed_products,monkeypatch):
    monkeypatch.setattr(settings,'catalog_export_admin_usernames',['different-admin'])
    response=await logged_in_client.post('/linked-products/exports',json={'family_codes':['ZZ-9001'],'show_stock':True})
    assert response.status_code==403
    monkeypatch.setattr(settings,'catalog_export_admin_usernames',['admin'])
    async with TestSession() as session:
        user=await session.scalar(select(DashboardUser).where(DashboardUser.username=='admin'))
        user.must_change_password=True
        await session.commit()
    assert (await logged_in_client.post('/linked-products/exports',json={'family_codes':['ZZ-9001'],'show_stock':True})).status_code==403


async def test_legacy_pcs_job_without_admin_grant_renders_labels(logged_in_client,seed_products,monkeypatch,tmp_path):
    client = logged_in_client
    from app.services import catalog_export_service as jobs
    monkeypatch.setattr(settings,'catalog_export_dir',str(tmp_path))
    monkeypatch.setattr(worker,'async_session',TestSession)
    monkeypatch.setattr(pdf,'fetch_catalog_images',AsyncMock(return_value={}))
    options={'family_codes':['ZZ-9001'],'stock_mode':'all','min_stock':1,'show_stock':True,'show_prices':True,'output_mode':'single'}
    async with TestSession() as session:
        job,token=await jobs.create_job(session,options,'legacy-owner')
        job_id=job.id
    await worker.CatalogExportWorker().run_once()
    result=await client.post(f'/linked-products/exports/{job_id}/files/catalog.pdf/download',data={'token':token})
    text='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(result.content)).pages)
    assert '8 pcs' not in text and 'Stok Banyak' in text


async def test_checkbox_default_private_and_admin_enabled(client, logged_in_client, seed_products):
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as guest:
        page=(await guest.get('/linked-products')).text
        assert 'data-can-export-quantities="false" disabled' in page
        assert 'Masuk sebagai admin' in page
    page=(await logged_in_client.get('/linked-products')).text
    assert 'data-can-export-quantities="true"' in page
    assert 'id="export-show-stock" type="checkbox" checked' not in page
