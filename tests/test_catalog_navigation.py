"""Navigation destinations must follow layout, not row or family counts."""
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfReader

from app.config import settings
from app.services import catalog_pdf_service as pdf
from app.tasks import catalog_export_worker as worker_module
from tests.conftest import TestSession


def card(code, rows=1):
    return {
        'family_code': code, 'jenis_bahan': 'Cotton', 'primary_image_url': None,
        'products': [{'name': f'Product {code}', 'rows': [
            {'name': f'Variant {i:03}', 'price': 987654, 'stock': 37}
            for i in range(rows)
        ]}],
    }


def assert_destinations(reader, codes, expected_pages):
    outlines = reader.outline
    assert [item.title for item in outlines] == codes
    assert [reader.get_destination_page_number(item) for item in outlines] == expected_pages
    for item, code in zip(outlines, codes):
        page = reader.pages[reader.get_destination_page_number(item)]
        assert f'Product {code}' in page.extract_text()
        assert f'{code} (continued)' not in page.extract_text()
    assert reader.trailer['/Root']['/PageMode'] == '/UseOutlines'


def test_direct_bookmarks_follow_long_family_continuations():
    codes = ['LONG-&<001>', 'NEXT-002', 'LAST-003']
    reader = PdfReader(io.BytesIO(pdf.render_catalog_pdf([
        card(codes[0], 85), card(codes[1], 45), card(codes[2]),
    ])))
    texts = [page.extract_text() for page in reader.pages]
    starts = [next(i for i, text in enumerate(texts)
                   if f'Product {code}' in text) for code in codes]
    assert starts[1] > 2
    assert starts[2] > starts[1] + 1
    assert_destinations(reader, codes, starts)
    assert f'{codes[0]} (continued)' in texts[1]
    assert 'Variant 084' in '\n'.join(texts[:starts[1]])


def test_merge_remaps_reused_local_bookmark_keys(tmp_path):
    groups = [[card('FIRST', 85), card('SECOND')], [card('THIRD', 45), card('FOURTH')]]
    paths, codes, expected_pages = [], [], []
    page_offset = 0
    for index, cards in enumerate(groups):
        data = pdf.render_catalog_pdf(cards, show_prices=False, show_stock=False)
        source = PdfReader(io.BytesIO(data))
        codes.extend(item.title for item in source.outline)
        expected_pages.extend(page_offset + source.get_destination_page_number(item)
                              for item in source.outline)
        page_offset += len(source.pages)
        path = tmp_path / f'part-{index}.pdf'
        path.write_bytes(data)
        paths.append(path)
    destination = tmp_path / 'merged.pdf'
    worker_module.CatalogExportWorker.merge(paths, destination)
    reader = PdfReader(destination)
    assert len(reader.pages) == page_offset
    assert_destinations(reader, codes, expected_pages)
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert '987.654' not in text and 'PRICE' not in text and '37 pcs' not in text
    assert 'Available' in text


@pytest.mark.asyncio
@pytest.mark.parametrize('output_mode', ['single', 'split'])
async def test_worker_navigation_in_final_volumes_and_zip(monkeypatch, tmp_path, output_mode):
    # Exercise the actual 50-family split boundary with a continuation before
    # the boundary, so both page offsets and volume-local destinations matter.
    cards = [card(f'FAMILY-{i:03}', 45 if i in (0, 50) else 1) for i in range(51)]
    monkeypatch.setattr(settings, 'catalog_export_dir', str(tmp_path))
    monkeypatch.setattr(worker_module, 'async_session', TestSession)
    monkeypatch.setattr(pdf, 'get_export_cards', AsyncMock(return_value=(cards, 0)))
    monkeypatch.setattr(pdf, 'fetch_catalog_images', AsyncMock(return_value={}))
    worker = worker_module.CatalogExportWorker()
    worker.update = AsyncMock()
    worker.check_cancellation = AsyncMock()  # layout test uses a synthetic job, not a durable queue row
    monkeypatch.setattr(worker, 'check_disk', lambda: None)
    directory = tmp_path / 'job'
    directory.mkdir()
    job = SimpleNamespace(options={
        'family_codes': [item['family_code'] for item in cards],
        'stock_mode': 'all', 'min_stock': 1, 'show_stock': False,
        'show_prices': False, 'output_mode': output_mode,
    })
    await worker.process(job, directory)
    names = ['catalog.pdf'] if output_mode == 'single' else ['catalog-01.pdf', 'catalog-02.pdf']
    for index, name in enumerate(names):
        selected = cards if output_mode == 'single' else cards[index * 50:(index + 1) * 50]
        reader = PdfReader(directory / name)
        codes = [item['family_code'] for item in selected]
        texts = [page.extract_text() for page in reader.pages]
        starts = [next(i for i, text in enumerate(texts)
                       if f'Product {code}' in text) for code in codes]
        assert_destinations(reader, codes, starts)
        assert starts[0] == 0
        assert len(reader.pages) > len(selected)
        assert '987.654' not in '\n'.join(texts)
        assert '37 pcs' not in '\n'.join(texts)
    if output_mode == 'split':
        with zipfile.ZipFile(directory / 'catalog-volumes.zip') as archive:
            assert archive.namelist() == names
            for name in names:
                assert archive.read(name) == (directory / name).read_bytes()
    assert not list(directory.glob('part-*.pdf'))
    assert worker.update.call_args.kwargs['status'] == 'ready'
