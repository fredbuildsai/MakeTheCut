"""Render a list of ApplicationRecords as a PNG table image."""

import io
from PIL import Image, ImageDraw, ImageFont
from models import ApplicationRecord

# ── Design tokens ─────────────────────────────────────────────────────────────
BG          = "#1e1e2e"
HEADER_BG   = "#313244"
ROW_ALT     = "#262637"
TEXT        = "#cdd6f4"
TEXT_DIM    = "#a6adc8"
BORDER      = "#45475a"
GREEN       = "#a6e3a1"
YELLOW      = "#f9e2af"
RED         = "#f38ba8"
ACCENT      = "#89b4fa"

PAD_X = 24
PAD_Y = 20
ROW_H = 38
HEADER_H = 46
FONT_SIZE = 15
TITLE_SIZE = 17

COLS = [
    {"key": "score",   "label": "Score", "width": 72,  "align": "center"},
    {"key": "date",    "label": "Date",  "width": 102, "align": "center"},
    {"key": "company", "label": "Company", "width": 200, "align": "left"},
    {"key": "role",    "label": "Role",  "width": 280, "align": "left"},
]


def _load_font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSansMono", "Menlo", "Courier New", "monospace"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _score_color(score: int, threshold: int = 70) -> str:
    if score >= 80:
        return GREEN
    if score >= threshold:
        return YELLOW
    return RED


def _truncate(text: str, font: ImageFont.ImageFont, max_w: int) -> str:
    while text and font.getlength(text) > max_w:
        text = text[:-1]
    return text.rstrip()


def render_table(records: list[ApplicationRecord], threshold: int = 70) -> io.BytesIO:
    font = _load_font(FONT_SIZE)
    title_font = _load_font(TITLE_SIZE)

    table_w = sum(c["width"] for c in COLS) + PAD_X * (len(COLS) + 1)
    title_h = PAD_Y + TITLE_SIZE + PAD_Y
    total_h = title_h + HEADER_H + ROW_H * len(records) + PAD_Y

    img = Image.new("RGB", (table_w, total_h), BG)
    d = ImageDraw.Draw(img)

    # Title
    title = f"{len(records)} job{'s' if len(records) != 1 else ''} analysed"
    d.text((PAD_X, PAD_Y), title, font=title_font, fill=ACCENT)

    y = title_h

    # Header background
    d.rectangle([0, y, table_w, y + HEADER_H], fill=HEADER_BG)

    # Header text
    x = PAD_X
    for col in COLS:
        label_x = x + col["width"] // 2 if col["align"] == "center" else x
        anchor = "mm" if col["align"] == "center" else "lm"
        d.text((label_x, y + HEADER_H // 2), col["label"], font=font, fill=TEXT, anchor=anchor)
        x += col["width"] + PAD_X

    # Header bottom border
    d.line([0, y + HEADER_H, table_w, y + HEADER_H], fill=BORDER, width=1)
    y += HEADER_H

    # Rows
    for i, rec in enumerate(records):
        row_bg = ROW_ALT if i % 2 == 0 else BG
        d.rectangle([0, y, table_w, y + ROW_H], fill=row_bg)

        values = {
            "score": str(rec.analysis.fit_score),
            "date":  rec.timestamp.strftime("%Y-%m-%d"),
            "company": rec.job_info.company,
            "role":  rec.job_info.role,
        }

        x = PAD_X
        for col in COLS:
            val = _truncate(values[col["key"]], font, col["width"])
            color = _score_color(rec.analysis.fit_score, threshold) if col["key"] == "score" else TEXT
            text_x = x + col["width"] // 2 if col["align"] == "center" else x
            anchor = "mm" if col["align"] == "center" else "lm"
            d.text((text_x, y + ROW_H // 2), val, font=font, fill=color, anchor=anchor)
            x += col["width"] + PAD_X

        # Row bottom border
        d.line([0, y + ROW_H, table_w, y + ROW_H], fill=BORDER, width=1)
        y += ROW_H

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
