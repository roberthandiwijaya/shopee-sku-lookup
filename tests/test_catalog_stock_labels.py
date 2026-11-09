"""Customer stock labels must not disclose exact inventory quantities."""
import io

import pytest
from pypdf import PdfReader

from app.services import catalog_pdf_service as pdf


NOTE = (
    "Stok dapat berubah sewaktu-waktu. Mohon konfirmasi ketersediaan stok "
    "sebelum memesan."
)
STOCKS = [0, 1, 2, 3, 5, 6, None]
LABELS = [
    "Stok Habis", "Stok Terbatas", "Stok Terbatas", "Stok Tersedia",
    "Stok Tersedia", "Stok Banyak", "Stok Belum Dikonfirmasi",
]


def cards(stocks):
    return [{
        "family_code": "LABEL-TEST", "jenis_bahan": "Katun", "primary_image_url": None,
        "products": [{"name": "Produk uji", "rows": [
            {"name": f"Varian {chr(65 + index % 26)}", "stock": stock, "price": 123456}
            for index, stock in enumerate(stocks)
        ]}],
    }]


def read_pdf(stocks, **options):
    reader = PdfReader(io.BytesIO(pdf.render_catalog_pdf(cards(stocks), **options)))
    text = " ".join(" ".join(page.extract_text().split()) for page in reader.pages)
    return reader, text


@pytest.mark.parametrize("stock,label", zip(STOCKS, LABELS))
@pytest.mark.parametrize("show_prices", [False, True])
def test_customer_labels_cover_boundaries_and_unknown(stock, label, show_prices):
    _, text = read_pdf([stock], show_stock=False, show_prices=show_prices)
    assert label in text
    assert "pcs" not in text
    assert "Unknown" not in text and "Available" not in text and "Out of stock" not in text
    assert NOTE in text
    assert ("Rp123.456" in text) is show_prices
    if stock is None:
        assert "Stok Habis" not in text


def test_renderer_defaults_to_labels_and_hides_filter_thresholds():
    _, text = read_pdf(STOCKS, min_stock=987654, omitted=321)
    for label in set(LABELS):
        assert label in text
    assert "pcs" not in text
    assert "987654" not in text and "321" not in text
    assert "Minimum stock" not in text and "Stok minimum" not in text
    assert NOTE in text


@pytest.mark.parametrize("stock", STOCKS)
def test_explicit_exact_quantities_keep_confirmation_note(stock):
    _, text = read_pdf([stock], show_stock=True)
    if stock is None:
        assert "Stok Belum Dikonfirmasi" in text
        assert "0 pcs" not in text and "None pcs" not in text
    else:
        assert f"{stock} pcs" in text
    assert NOTE in text


@pytest.mark.parametrize("stock_mode", ["all", "available"])
def test_confirmation_note_in_every_stock_filter_mode(stock_mode):
    _, text = read_pdf([6], stock_mode=stock_mode, show_stock=False)
    assert NOTE in text
    assert "pcs" not in text


def test_label_style_is_bold_legible_and_at_most_two_lines_on_a5(monkeypatch):
    captured = []
    original = pdf.LongTable

    def capture_table(rows, **kwargs):
        captured.append((rows, kwargs["colWidths"]))
        return original(rows, **kwargs)

    monkeypatch.setattr(pdf, "LongTable", capture_table)
    read_pdf(STOCKS, show_stock=False, show_prices=True)
    rows, widths = captured[0]
    colors = []
    for row in rows[2:]:
        paragraph = row[-1]
        assert paragraph.style.fontName == "Helvetica-Bold"
        assert paragraph.style.fontSize == 8
        _, height = paragraph.wrap(widths[-1] - 14, 1000)
        assert height <= 2 * paragraph.style.leading
        color = paragraph.style.textColor
        colors.append(color.hexval())
        # WCAG relative luminance: text must contrast with both row backgrounds.
        def luminance(channels):
            linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
                      for c in channels]
            return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))

        foreground = luminance((color.red, color.green, color.blue))
        for background in [(1, 1, 1), (239 / 255, 244 / 255, 240 / 255)]:
            assert (luminance(background) + .05) / (foreground + .05) >= 4.5
    assert len(set(colors)) == 5


@pytest.mark.parametrize("show_prices", [False, True])
def test_label_mode_keeps_pagination_bookmarks_and_price_visibility(show_prices):
    reader, text = read_pdf([6] * 90, show_stock=False, show_prices=show_prices)
    assert len(reader.pages) > 1
    assert reader.outline[0].title == "LABEL-TEST"
    assert reader.get_destination_page_number(reader.outline[0]) == 0
    assert "pcs" not in text
    assert NOTE in text
    for page in reader.pages:
        page_text = page.extract_text()
        if "Varian" in page_text:
            assert "Stok Banyak" in page_text
            assert ("Rp123.456" in page_text) is show_prices
            assert ("PRICE" in page_text) is show_prices
