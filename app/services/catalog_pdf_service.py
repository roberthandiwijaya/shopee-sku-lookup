"""Bounded, mobile-readable PDF export of explicitly selected catalog families."""
import asyncio
import io
import warnings
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import httpx
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable, Image, LongTable, PageBreak, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.product import Product, TZ_GMT7
from app.services.product_service import _build_family_card, normalize_family_code

MAX_FAMILIES = 30
MAX_ROWS = 1500
MAX_IMAGE_BYTES = 4_000_000
IMAGE_HOSTS = frozenset({"cf.shopee.co.id", "cf.shopee.com", "down-id.img.susercontent.com"})


async def get_export_cards(session, family_codes, *, stock_mode="available", min_stock=1, max_families=MAX_FAMILIES, max_rows=MAX_ROWS):
    """Load selected families independently of catalog pagination; filter individual rows."""
    codes = list(dict.fromkeys(normalize_family_code(code) for code in family_codes))
    if not codes or any(not code or len(code) > 100 for code in codes):
        raise ValueError("Select at least one valid family code (maximum 100 characters each).")
    if len(codes) > max_families:
        raise ValueError(f"Export at most {max_families} families at a time.")
    if stock_mode not in {"all", "available"} or not 1 <= min_stock <= 1_000_000:
        raise ValueError("Invalid stock filter.")
    result = await session.execute(
        select(Product).options(selectinload(Product.models))
        .where(Product.family_code.in_(codes))
        .order_by(Product.family_code, Product.item_name, Product.item_id)
    )
    grouped = {}
    for product in result.scalars():
        grouped.setdefault(product.family_code, []).append(product)
    missing = set(codes) - grouped.keys()
    if missing:
        raise ValueError("Some selected families no longer exist. Clear the selection and select them again.")
    cards, row_count = [], 0
    for code in sorted(grouped):
        family = _build_family_card(code, grouped[code])
        products = []
        for product in family["products"]:
            if product.models:
                rows = [{"name": model.model_name or "-", "price": model.current_price, "stock": model.stock}
                        for model in sorted(product.models, key=lambda model: (model.model_name or "", model.model_id))]
            else:
                rows = [{"name": "Standard / no variants", "price": product.current_price,
                         "stock": product.display_stock}]
            if stock_mode == "available":
                rows = [row for row in rows if row["stock"] is not None and row["stock"] >= min_stock]
            if rows:
                row_count += len(rows)
                if row_count > max_rows:
                    raise ValueError(f"Export exceeds {max_rows} variant rows. Select fewer families.")
                products.append({"name": product.item_name, "rows": rows})
        if products:
            cards.append({"family_code": code, "jenis_bahan": family["jenis_bahan"],
                          "primary_image_url": family["primary_image_url"], "products": products})
    if not cards:
        raise ValueError("No products match the selected stock filter. Lower the minimum or include all stock.")
    return cards, len(codes) - len(cards)


def is_allowed_image_url(url):
    try:
        parsed = urlparse(url)
        return (parsed.scheme == "https" and parsed.hostname in IMAGE_HOSTS
                and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except (TypeError, ValueError):
        return False


async def fetch_catalog_images(cards):
    """Fetch only known Shopee CDNs; redirects, ambient proxies and large files are disallowed."""
    urls = {card["primary_image_url"] for card in cards if is_allowed_image_url(card.get("primary_image_url"))}
    images = {}
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
        async def fetch(url):
            async with semaphore:
                try:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        if not response.headers.get("content-type", "").lower().startswith("image/"):
                            return
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_IMAGE_BYTES:
                                return
                        images[url] = bytes(data)
                except (httpx.HTTPError, ValueError):
                    return
        try:
            async with asyncio.timeout(30):
                await asyncio.gather(*(fetch(url) for url in urls))
        except TimeoutError:
            pass  # Preserve completed images and use placeholders for the rest.
    return images


def _normalized_photo(data):
    if not data:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", PILImage.DecompressionBombWarning)
            with PILImage.open(io.BytesIO(data)) as photo:
                if photo.width * photo.height > 20_000_000:
                    return None
                photo.thumbnail((1200, 1200))
                normalized = io.BytesIO()
                photo.convert("RGB").save(normalized, "JPEG", quality=85, optimize=True)
                normalized.seek(0)
                return normalized
    except (OSError, ValueError, PILImage.DecompressionBombError, PILImage.DecompressionBombWarning):
        return None


class _FamilyMarker(Flowable):
    """Set family context before any content; continuation pages inherit it."""
    def __init__(self, code, bookmark_key):
        super().__init__()
        self.code = code
        self.bookmark_key = bookmark_key
        self.width = self.height = 0
        self.keepWithNext = True

    def draw(self):
        self.canv._catalog_family = self.code
        self.canv._catalog_family_start_page = self.canv.getPageNumber()
        # Resolve the destination only when laid out, never by estimating row counts:
        # a family's tables can span any number of continuation pages.
        self.canv.bookmarkPage(self.bookmark_key)
        self.canv.addOutlineEntry(str(self.code), self.bookmark_key, level=0, closed=False)
        self.canv.showOutline()


def render_catalog_pdf(cards, *, show_stock=True, show_prices=True, stock_mode="available", min_stock=1, images=None, omitted=0, snapshot_at=None):
    """Build an A5 PDF. Tables split between rows and repeat their product heading."""
    output = io.BytesIO()
    images = images or {}
    ink, muted, accent = [colors.HexColor(value) for value in ("#182C2B", "#526461", "#236653")]
    pale, line = colors.HexColor("#EFF4F0"), colors.HexColor("#D9E2DC")
    width, height = A5
    usable = width - 28 * mm
    styles = {
        "eyebrow": ParagraphStyle("eyebrow", fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=accent, spaceAfter=5),
        "code": ParagraphStyle("code", fontName="Helvetica-Bold", fontSize=26, leading=29, textColor=ink, spaceAfter=5),
        "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=ink),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14, textColor=ink, splitLongWords=True),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=11, textColor=muted),
        "white": ParagraphStyle("white", fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=colors.white),
    }

    def p(value, style="body"):
        return Paragraph(escape(str(value or "-")), styles[style])

    stamp = (snapshot_at or datetime.now(TZ_GMT7)).astimezone(TZ_GMT7).strftime("%d %b %Y, %H:%M WIB")

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(accent)
        canvas.rect(0, height - 3*mm, width, 3*mm, fill=1, stroke=0)
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(14*mm, height - 9*mm, "PRODUCT CATALOG")
        canvas.setStrokeColor(line)
        canvas.line(14*mm, 14*mm, width - 14*mm, 14*mm)
        canvas.drawString(14*mm, 10*mm, "Snapshot: " + stamp)
        canvas.drawRightString(width - 14*mm, 10*mm, str(doc.page))
        canvas.restoreState()

    def page_end(canvas, doc):
        # Draw after flowables so a new family's marker has already been processed.
        canvas.saveState()
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 7)
        label = getattr(canvas, "_catalog_family", "")
        if canvas.getPageNumber() > getattr(canvas, "_catalog_family_start_page", 1):
            label += " (continued)"
        canvas.drawRightString(width - 14*mm, height - 9*mm, label[:85])
        canvas.restoreState()

    story = []
    for index, card in enumerate(cards):
        if index:
            story.append(PageBreak())
        story.extend([_FamilyMarker(card["family_code"], f"family-{index}"), p(card["family_code"], "code"),
                      p(card["jenis_bahan"], "small"), Spacer(1, 4*mm)])
        photo = _normalized_photo(images.get(card["primary_image_url"]))
        if photo:
            image = Image(photo)
            image._restrictSize(usable, 54*mm)
            frame = Table([[image]], colWidths=[usable])
            frame.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), pale), ("BOX",(0,0),(-1,-1),0.5,line),
                                       ("ALIGN",(0,0),(-1,-1),"CENTER"), ("TOPPADDING",(0,0),(-1,-1),5),
                                       ("BOTTOMPADDING",(0,0),(-1,-1),5)]))
            story.append(frame)
        else:
            story.append(p("Product image unavailable", "small"))
        story.append(Spacer(1, 4*mm))
        for product in card["products"]:
            widths = [usable*.40, usable*.34, usable*.26] if show_prices else [usable*.70, usable*.30]
            headings = [p("SIZE / VARIANT", "white")]
            if show_prices:
                headings.append(p("PRICE", "white"))
            headings.append(p("STOCK" if show_stock else "STATUS", "white"))
            rows = [[p(product["name"], "title")] + [""] * (len(widths) - 1), headings]
            for row in product["rows"]:
                stock = row["stock"]
                if show_stock:
                    stock_label = f"{stock} pcs" if stock is not None else "Unknown"
                else:
                    stock_label = "Unknown" if stock is None else ("Available" if stock > 0 else "Out of stock")
                cells = [p(row["name"])]
                if show_prices:
                    price = "-" if row["price"] is None else "Rp" + format(Decimal(str(row["price"])), ",.0f").replace(",", ".")
                    cells.append(p(price))
                cells.append(p(stock_label))
                rows.append(cells)
            table = LongTable(rows, colWidths=widths, repeatRows=2, hAlign="LEFT")
            table.setStyle(TableStyle([("SPAN",(0,0),(-1,0)), ("BACKGROUND",(0,1),(-1,1),accent),
                ("ROWBACKGROUNDS",(0,2),(-1,-1),[colors.white,pale]), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),6),
                ("TOPPADDING",(0,0),(-1,-1),8), ("BOTTOMPADDING",(0,0),(-1,-1),8),
                ("LINEBELOW",(0,2),(-1,-1),.4,line)]))
            story.extend([table, Spacer(1, 3*mm)])
        stock_description = f"Minimum stock: {min_stock}" if stock_mode == "available" else "All stock levels included"
        story.append(p(stock_description + " | " + ("Quantities shown" if show_stock else "Quantities hidden"), "small"))
        disclaimer = "Prices and stock may change." if show_prices else "Stock may change."
        story.append(p(disclaimer + " Availability is a snapshot, not a reservation.", "small"))
        if omitted:
            story.append(p(f"{omitted} selected families omitted because no rows matched the stock filter.", "small"))
    doc = SimpleDocTemplate(output, pagesize=A5, leftMargin=14*mm, rightMargin=14*mm,
                            topMargin=14*mm, bottomMargin=19*mm, title="Product Catalog", author="", pageCompression=1)
    # SimpleDocTemplate creates page templates internally; a canvas subclass provides an end-of-page hook.
    from reportlab.pdfgen.canvas import Canvas

    class CatalogCanvas(Canvas):
        def showPage(self):
            page_end(self, doc)
            super().showPage()

    doc.build(story, onFirstPage=decorate, onLaterPages=decorate, canvasmaker=CatalogCanvas)
    return output.getvalue()
