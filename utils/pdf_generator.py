"""Convert a markdown string to PDF bytes using fpdf2."""

import io
import re
from pathlib import Path
import markdown as md_lib
from fpdf import FPDF

# Font family candidates: (regular, bold, italic, bold-italic)
_FONT_CANDIDATES = [
    (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf",
    ),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    ),
]

def _find_font_family() -> tuple[str, str, str, str] | None:
    for regular, bold, italic, bold_italic in _FONT_CANDIDATES:
        if Path(regular).exists():
            return regular, bold, italic, bold_italic
    return None

_FONT_FAMILY = _find_font_family()


def _strip_images(html: str) -> str:
    """Remove all <img> tags — local paths won't resolve inside the PDF renderer."""
    return re.sub(r"<img[^>]*>", "", html)


def markdown_to_pdf_bytes(markdown_text: str, photo_bytes: bytes | None = None) -> bytes:
    """Render markdown to a clean PDF and return the raw bytes."""
    html = md_lib.markdown(markdown_text, extensions=["extra", "sane_lists"])
    html = _strip_images(html)

    pdf = FPDF()
    pdf.set_margins(22, 22, 22)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    if _FONT_FAMILY:
        regular, bold, italic, bold_italic = _FONT_FAMILY
        pdf.add_font("Body", style="",   fname=regular)
        pdf.add_font("Body", style="B",  fname=bold)
        pdf.add_font("Body", style="I",  fname=italic)
        pdf.add_font("Body", style="BI", fname=bold_italic)
        pdf.set_font("Body", size=11)
    else:
        pdf.set_font("Helvetica", size=11)

    if photo_bytes:
        photo_buf = io.BytesIO(photo_bytes)
        page_w = pdf.w - pdf.r_margin - pdf.l_margin
        photo_w = 35
        pdf.image(photo_buf, x=pdf.l_margin + page_w - photo_w, y=pdf.t_margin, w=photo_w)
        pdf.set_y(pdf.t_margin + photo_w + 4)

    pdf.write_html(html)
    return bytes(pdf.output())


def has_image_refs(markdown_text: str) -> bool:
    """Return True if the markdown contains any image references."""
    return bool(re.search(r"!\[.*?\]\(.*?\)", markdown_text))


def pdf_filename(date_str: str, role: str, company: str, suffix: str) -> str:
    """Build a clean filename: YYYY-MM-DD_ShortRole_Company_suffix.pdf"""
    def _slug(text: str, max_len: int) -> str:
        text = re.sub(r"[^\w\s-]", "", text.strip())
        text = re.sub(r"[\s-]+", "_", text)
        return text[:max_len]

    return f"{date_str}_{_slug(role, 20)}_{_slug(company, 15)}_{suffix}.pdf"
