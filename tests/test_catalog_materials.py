from html.parser import HTMLParser

import pytest
from sqlalchemy import update

from app.models.product import Product
from app.services import product_service
from tests.conftest import TestSession


class MaterialOptions(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.values = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'data-material' in attrs and attrs['data-material']:
            self.values.append(attrs['data-material'])


@pytest.mark.asyncio
async def test_catalog_materials_normalized_unique_and_sorted(seed_products):
    async with TestSession() as session:
        await session.execute(update(Product).where(Product.item_id == 1003).values(jenis_bahan='  katun   jepang  '))
        await session.execute(update(Product).where(Product.item_id == 1004).values(jenis_bahan='Not in catalog'))
        await session.execute(update(Product).where(Product.item_id == 1006).values(jenis_bahan='   '))
        await session.commit()
        options = await product_service.list_catalog_materials(session)
    assert options == ['KATUN JEPANG', 'MICROTEX', 'TENCEL']


@pytest.mark.asyncio
async def test_material_combobox_public_and_not_limited_by_filters(client, seed_products):
    response = await client.get('/linked-products?jenis_bahan=microtex&family_code=VR-JE9002&per_page=10')
    assert response.status_code == 200
    assert 'role="combobox"' in response.text
    assert 'id="material-toggle"' in response.text
    assert 'role="listbox"' in response.text
    assert '<datalist' not in response.text
    assert 'All materials (clear filter)' in response.text
    assert 'value="microtex"' in response.text
    parser = MaterialOptions()
    parser.feed(response.text)
    assert parser.values == ['KATUN JEPANG', 'MICROTEX', 'TENCEL']


@pytest.mark.asyncio
async def test_material_combobox_escapes_values_and_retains_custom_text(client, seed_products):
    async with TestSession() as session:
        await session.execute(update(Product).where(Product.item_id == 1003).values(jenis_bahan='<Silk & Cotton>'))
        await session.commit()
    response = await client.get('/linked-products', params={'jenis_bahan': 'Custom material'})
    parser = MaterialOptions()
    parser.feed(response.text)
    assert '<SILK & COTTON>' in parser.values
    assert '&lt;SILK &amp; COTTON&gt;' in response.text
    assert 'value="Custom material"' in response.text


@pytest.mark.asyncio
async def test_material_combobox_empty_catalog(client):
    response = await client.get('/linked-products')
    assert response.status_code == 200
    parser = MaterialOptions()
    parser.feed(response.text)
    assert parser.values == []
