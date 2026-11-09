import hashlib
import json

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.config import settings
from app.main import app
from app.services import catalog_identity as identity


def request(peer='8.8.4.4', forwarded=None):
    headers=[] if forwarded is None else [(b'x-forwarded-for',forwarded.encode())]
    return Request({'type':'http','method':'GET','path':'/','headers':headers,'client':(peer,123),'server':('test',80),'scheme':'http'})


def test_untrusted_forwarding_ignored(monkeypatch):
    monkeypatch.setattr(settings,'catalog_export_trusted_proxy_ips',['172.22.0.5'])
    assert identity.client_network(request(forwarded='1.2.3.4'))=='8.8.4.4/32'
    assert identity.client_network(request(forwarded='not-an-ip'))=='8.8.4.4/32'


def test_trusted_chain_walk_and_ipv6_grouping(monkeypatch):
    monkeypatch.setattr(settings,'catalog_export_trusted_proxy_ips',['172.22.0.5'])
    assert identity.client_network(request('172.22.0.5','8.8.8.8, 203.0.113.9'))=='203.0.113.9/32'
    assert identity.client_network(request('2606:4700:abcd:1::1234'))=='2606:4700:abcd:1::/64'
    assert identity.client_network(request('::ffff:8.8.4.4'))=='8.8.4.4/32'
    with pytest.raises(HTTPException):identity.client_network(request('172.22.0.5','bad'))


def test_private_untrusted_gateway_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, 'catalog_export_trusted_proxy_ips', [])
    with pytest.raises(HTTPException) as exc:
        identity.client_network(request('172.22.0.5', '8.8.8.8'))
    assert exc.value.status_code == 503


def test_network_hash_keyed_and_fail_closed(monkeypatch):
    monkeypatch.setattr(settings,'catalog_export_network_secret','x'*32)
    hashed=identity.network_hash(request())
    assert len(hashed)==64 and '203.0.113' not in hashed
    assert hashed!=hashlib.sha256(b'8.8.4.4/32').hexdigest()
    monkeypatch.setattr(settings,'catalog_export_network_secret','')
    with pytest.raises(HTTPException) as exc:identity.network_hash(request())
    assert exc.value.status_code==503


@pytest.mark.asyncio
async def test_cookie_bootstrap_csrf_and_origin_are_required(seed_products):
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        payload={'family_codes':['ZZ-9001']}
        assert (await client.post('/linked-products/exports',json=payload)).status_code==409
        boot=await client.get('/linked-products/export-session')
        assert boot.headers['cache-control']=='no-store'
        assert (await client.post('/linked-products/exports',json=payload)).status_code==403
        headers={'X-Export-CSRF':boot.json()['csrf_token'],'Origin':'https://evil.invalid'}
        assert (await client.post('/linked-products/exports',json=payload,headers=headers)).status_code==403
        headers['Origin']='http://test'
        assert (await client.post('/linked-products/exports',json=payload,headers={**headers,'Sec-Fetch-Site':'cross-site'})).status_code==403
        response=await client.post('/linked-products/exports',json=payload,headers=headers)
        assert response.status_code==202,response.text
        denied=await client.post('/linked-products/exports',json=payload,headers=headers)
        assert denied.status_code==429
        assert int(denied.headers['retry-after'])>0
        assert denied.json()['reason'] in ['owner_active','owner_cooldown']
        job=response.json()
        # Possessing the capability still allows status and cancellation during cooldown.
        auth={'Authorization':'Bearer '+job['token']}
        assert (await client.get('/linked-products/exports/'+job['id'],headers=auth)).status_code==200
        assert (await client.post('/linked-products/exports/'+job['id']+'/cancel',headers=auth)).status_code==200


@pytest.mark.asyncio
async def test_preflight_limited_separately(client,seed_products,monkeypatch):
    monkeypatch.setattr(settings,'catalog_export_preflight_owner_per_minute',1)
    payload={'family_codes':['ZZ-9001']}
    assert (await client.post('/linked-products/exports/preflight',json=payload)).status_code==200
    response=await client.post('/linked-products/exports/preflight',json=payload)
    assert response.status_code==429 and response.json()['reason']=='preflight_owner'
    assert (await client.post('/linked-products/exports',json=payload)).status_code==202
