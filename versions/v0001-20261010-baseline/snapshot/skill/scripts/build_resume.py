#!/usr/bin/env python3
"""Render a source-backed Chinese resume JSON as a selectable PDF and standalone HTML.

Usage:
    python build_resume.py resume.json --output-dir output/v001

Only supplied content is rendered. Fact IDs and other provenance fields are kept in
the input by the caller; this renderer does not turn them into claims or footnotes.
Use --max-pages 0 to deliberately allow more than the normal two-page limit.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer


FONT_NAME = "ResumeCJK"
DEFAULT_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)
DRAFT_LABEL = "内容草稿 · 待补充核实"
NAVY = "#183248"
TEAL = "#237E83"
INK = "#293642"
MUTED = "#65717B"
LINE = "#D6E3E7"
PAGE_WIDTH, PAGE_HEIGHT = A4
SIDE_MARGIN = 17 * mm
TOP_MARGIN = 15 * mm
BOTTOM_MARGIN = 17 * mm


class ResumeError(ValueError):
    """An actionable input, typography, or page-budget error."""


def string(value: Any, location: str, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ResumeError(f"{location} must be a string")
    value = value.strip()
    if required and not value:
        raise ResumeError(f"{location} must not be empty")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise ResumeError(f"{location} contains unsupported control characters")
    return value


def validate(data: Any) -> dict[str, Any]:
    """Normalize the renderable schema without inferring missing information."""
    if not isinstance(data, dict):
        raise ResumeError("input must be a JSON object")
    result: dict[str, Any] = {
        "name": string(data.get("name"), "name", required=True),
        "headline": string(data.get("headline"), "headline"),
        "version_label": string(data.get("version_label"), "version_label"),
        "status": data.get("status"),
        "contact": [],
        "sections": [],
    }
    if result["status"] not in ("draft", "ready"):
        raise ResumeError("status must be explicitly set to 'draft' or 'ready'")

    contacts = data.get("contact", [])
    if not isinstance(contacts, list):
        raise ResumeError("contact must be an array of strings")
    result["contact"] = [
        string(value, f"contact[{index}]", required=True)
        for index, value in enumerate(contacts)
    ]

    summary = data.get("summary")
    if summary is not None:
        if not isinstance(summary, dict):
            raise ResumeError("summary must be an object with a text field")
        result["summary"] = {
            "text": string(summary.get("text"), "summary.text", required=True)
        }

    sections = data.get("sections", [])
    if not isinstance(sections, list):
        raise ResumeError("sections must be an array")
    for section_index, section in enumerate(sections):
        key = f"sections[{section_index}]"
        if not isinstance(section, dict):
            raise ResumeError(f"{key} must be an object")
        title = string(section.get("title"), f"{key}.title", required=True)
        items = section.get("items")
        if not isinstance(items, list) or not items:
            raise ResumeError(f"{key}.items must be a nonempty array")
        normalized_items = []
        for item_index, item in enumerate(items):
            item_key = f"{key}.items[{item_index}]"
            if not isinstance(item, dict):
                raise ResumeError(f"{item_key} must be an object")
            normalized_item = {
                field: string(
                    item.get(field), f"{item_key}.{field}", required=field == "heading"
                )
                for field in ("heading", "subheading", "period")
            }
            bullets = item.get("bullets", [])
            if not isinstance(bullets, list):
                raise ResumeError(f"{item_key}.bullets must be an array")
            normalized_item["bullets"] = []
            for bullet_index, bullet in enumerate(bullets):
                bullet_key = f"{item_key}.bullets[{bullet_index}]"
                if not isinstance(bullet, dict):
                    raise ResumeError(f"{bullet_key} must be an object with a text field")
                normalized_item["bullets"].append(
                    {"text": string(bullet.get("text"), f"{bullet_key}.text", required=True)}
                )
            normalized_items.append(normalized_item)
        result["sections"].append({"title": title, "items": normalized_items})
    if not result.get("summary") and not result["sections"]:
        raise ResumeError("provide a nonempty summary or at least one section")
    return result


def all_text(data: dict[str, Any]) -> str:
    values = [data["name"], data["headline"], data["version_label"], *data["contact"]]
    if data["status"] == "draft":
        values.append(DRAFT_LABEL)
    if data.get("summary"):
        values.append(data["summary"]["text"])
    for section in data["sections"]:
        values.append(section["title"])
        for item in section["items"]:
            values.extend(item[field] for field in ("heading", "subheading", "period"))
            values.extend(bullet["text"] for bullet in item["bullets"])
    # These fixed strings are generated by layout, not biographical claims.
    values.extend(("·", "•", " / 0123456789"))
    return "\n".join(values)


def register_font(font_argument: str | None, text: str) -> Path:
    candidates = [font_argument] if font_argument else DEFAULT_FONTS
    failures = []
    for candidate in candidates:
        path = Path(candidate).expanduser().resolve()
        if not path.is_file():
            failures.append(f"{path}: not found")
            continue
        try:
            font = TTFont(FONT_NAME, str(path))
            missing = sorted({char for char in text if not char.isspace() and ord(char) not in font.face.charToGlyph})
            if missing:
                failures.append(f"{path}: missing glyphs {''.join(missing[:30])!r}")
                continue
            pdfmetrics.registerFont(font)
            return path
        except Exception as error:
            failures.append(f"{path}: {error}")
    raise ResumeError("No usable embedded Chinese TrueType font. Pass --font PATH. " + "; ".join(failures))


def escaped(text: str) -> str:
    return html.escape(text, quote=True).replace("\n", "<br/>")


def make_styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName=FONT_NAME, textColor=colors.HexColor(INK), alignment=TA_LEFT,
                wordWrap="CJK", splitLongWords=True, allowWidows=0, allowOrphans=0)
    specs = {
        "name": dict(fontSize=24, leading=30, textColor=colors.HexColor(NAVY), spaceAfter=4, keepWithNext=True),
        "headline": dict(fontSize=12, leading=17, textColor=colors.HexColor(TEAL), spaceAfter=5, keepWithNext=True),
        "contact": dict(fontSize=10, leading=14, textColor=colors.HexColor(MUTED), spaceAfter=3, keepWithNext=True),
        "draft": dict(fontSize=9, leading=13, textColor=colors.HexColor(MUTED), spaceAfter=4, keepWithNext=True),
        "section": dict(fontSize=12.5, leading=18, textColor=colors.HexColor(TEAL), spaceBefore=12, spaceAfter=6, keepWithNext=True),
        "heading": dict(fontSize=11.4, leading=16.5, textColor=colors.HexColor(NAVY), spaceAfter=2, keepWithNext=True),
        "meta": dict(fontSize=10, leading=14.5, textColor=colors.HexColor(MUTED), spaceAfter=4, keepWithNext=True),
        "body": dict(fontSize=10.5, leading=16.2, spaceAfter=5),
        "bullet": dict(fontSize=10.5, leading=16.2, leftIndent=11, firstLineIndent=-9, spaceAfter=3.3),
    }
    styles = {}
    for name, overrides in specs.items():
        styles[name] = ParagraphStyle(name, **{**base, **overrides})
    return styles


class HeaderRule(Flowable):
    def __init__(self) -> None:
        super().__init__()
        self.height = 9
        self.keepWithNext = True

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        self.width = available_width
        return self.width, self.height

    def draw(self) -> None:
        self.canv.setStrokeColor(colors.HexColor(LINE))
        self.canv.setLineWidth(0.7)
        self.canv.line(0, 3, self.width, 3)
        self.canv.setStrokeColor(colors.HexColor(TEAL))
        self.canv.setLineWidth(2.2)
        self.canv.line(0, 3, 29 * mm, 3)


def write_pdf(data: dict[str, Any], path: Path) -> int:
    styles = make_styles()
    content = []

    def paragraph(text: str, style: str) -> Paragraph:
        return Paragraph(escaped(text), styles[style])

    content.append(paragraph(data["name"], "name"))
    if data["headline"]:
        content.append(paragraph(data["headline"], "headline"))
    if data["contact"]:
        content.append(paragraph("  ·  ".join(data["contact"]), "contact"))
    if data["status"] == "draft":
        content.append(paragraph(DRAFT_LABEL, "draft"))
    content.append(HeaderRule())
    if data.get("summary"):
        content.extend((Spacer(1, 6), paragraph(data["summary"]["text"], "body")))
    for section in data["sections"]:
        content.append(paragraph(section["title"], "section"))
        for item_index, item in enumerate(section["items"]):
            if item_index:
                content.append(Spacer(1, 6))
            heading_style = styles["heading"]
            if not (item["subheading"] or item["period"] or item["bullets"]):
                heading_style = ParagraphStyle("last-heading", parent=heading_style, keepWithNext=False)
            content.append(Paragraph(escaped(item["heading"]), heading_style))
            metadata = "  ·  ".join(value for value in (item["subheading"], item["period"]) if value)
            if metadata:
                metadata_style = styles["meta"]
                if not item["bullets"]:
                    metadata_style = ParagraphStyle("last-meta", parent=metadata_style, keepWithNext=False)
                content.append(Paragraph(escaped(metadata), metadata_style))
            content.extend(paragraph("•  " + bullet["text"], "bullet") for bullet in item["bullets"])

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor(LINE))
        canvas.setLineWidth(0.5)
        canvas.line(SIDE_MARGIN, 12.5 * mm, PAGE_WIDTH - SIDE_MARGIN, 12.5 * mm)
        canvas.setFont(FONT_NAME, 8)
        canvas.setFillColor(colors.HexColor(MUTED))
        if data["version_label"]:
            canvas.drawString(SIDE_MARGIN, 8.3 * mm, data["version_label"])
        canvas.drawRightString(PAGE_WIDTH - SIDE_MARGIN, 8.3 * mm, str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(path), pagesize=A4, rightMargin=SIDE_MARGIN, leftMargin=SIDE_MARGIN,
        topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN, pageCompression=1,
        title=f"{data['name']} · {data['headline']}".rstrip(" ·"),
        author=data["name"], subject="简历", creator="haixiang-resume",
        # A fixed PDF creation date avoids meaningless metadata changes between renders.
        invariant=1,
    )
    doc.build(content, onFirstPage=footer, onLaterPages=footer)
    reader = PdfReader(str(path))
    if not reader.pages or not any((page.extract_text() or "").strip() for page in reader.pages):
        raise ResumeError("Generated PDF has no selectable text")
    return len(reader.pages)


def write_html(data: dict[str, Any], path: Path) -> None:
    parts = ["<!doctype html>", '<html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f'<title>{escaped(data["name"])} · 简历</title>',
             """<style>
:root { --navy:#183248; --teal:#237E83; --ink:#293642; --muted:#65717B; --line:#D6E3E7; }
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); background:#eaf0f2; font-family:"Arial Unicode MS","PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif; font-size:10.5pt; line-height:1.54; }
.page { max-width:210mm; min-height:297mm; margin:12mm auto; padding:15mm 17mm 17mm; background:#fff; box-shadow:0 4px 28px #18324818; }
header { padding-bottom:7pt; border-bottom:0.7pt solid var(--line); position:relative; }
header:after { content:""; position:absolute; bottom:-1pt; left:0; width:29mm; border-bottom:2.2pt solid var(--teal); }
h1 { font-size:24pt; line-height:1.25; font-weight:600; color:var(--navy); margin:0 0 4pt; }
.headline { font-size:12pt; line-height:1.42; color:var(--teal); margin:0 0 5pt; }
.contact { display:flex; flex-wrap:wrap; align-items:baseline; gap:2pt 8pt; font-size:10pt; color:var(--muted); margin:0 0 3pt; }
.contact span + span:before { content:"·"; padding-right:8pt; }
.draft { display:inline-block; font-size:9pt; color:var(--muted); margin-top:2pt; }
.summary { margin:12pt 0 5pt; }
section { margin-top:12pt; }
h2 { margin:0 0 6pt; color:var(--teal); font-weight:600; font-size:12.5pt; line-height:1.44; break-after:avoid; }
article { margin:0 0 9pt; }
h3 { margin:0 0 2pt; color:var(--navy); font-weight:600; font-size:11.4pt; line-height:1.45; break-after:avoid; }
.meta { color:var(--muted); font-size:10pt; line-height:1.45; margin:0 0 4pt; break-after:avoid; }
ul { margin:0; padding-left:12pt; }
li { padding-left:1pt; margin:0 0 3.3pt; orphans:2; widows:2; }
p, li { overflow-wrap:anywhere; }
p { orphans:2; widows:2; }
footer { border-top:0.5pt solid var(--line); color:var(--muted); font-size:8pt; margin-top:16pt; padding-top:5pt; }
@page { size:A4; margin:15mm 17mm 17mm; }
@media print {
 body { background:#fff; print-color-adjust:exact; -webkit-print-color-adjust:exact; }
 .page { width:auto; max-width:none; min-height:0; margin:0; padding:0; box-shadow:none; }
 header { break-inside:avoid; break-after:avoid; }
 footer { break-inside:avoid; }
}
@media screen and (max-width:700px) { .page { margin:0; padding:24px; min-height:100vh; } }
</style></head><body><main class="page"><header>""",
             f'<h1>{escaped(data["name"])}</h1>']
    if data["headline"]:
        parts.append(f'<p class="headline">{escaped(data["headline"])}</p>')
    if data["contact"]:
        parts.append('<div class="contact">' + "".join(f"<span>{escaped(value)}</span>" for value in data["contact"]) + "</div>")
    if data["status"] == "draft":
        parts.append(f'<div class="draft">{DRAFT_LABEL}</div>')
    parts.append("</header>")
    if data.get("summary"):
        parts.append(f'<p class="summary">{escaped(data["summary"]["text"])}</p>')
    for section in data["sections"]:
        parts.append(f'<section><h2>{escaped(section["title"])}</h2>')
        for item in section["items"]:
            parts.append(f'<article><h3>{escaped(item["heading"])}</h3>')
            metadata = "  ·  ".join(value for value in (item["subheading"], item["period"]) if value)
            if metadata:
                parts.append(f'<p class="meta">{escaped(metadata)}</p>')
            if item["bullets"]:
                parts.append("<ul>" + "".join(f'<li>{escaped(bullet["text"])}</li>' for bullet in item["bullets"]) + "</ul>")
            parts.append("</article>")
        parts.append("</section>")
    if data["version_label"]:
        parts.append(f'<footer>{escaped(data["version_label"])}</footer>')
    parts.append("</main></body></html>\n")
    path.write_text("\n".join(parts), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 resume content JSON")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--font", help="Local Chinese TrueType font file; never downloaded")
    parser.add_argument("--max-pages", type=int, default=2, help="Page budget; default 2; 0 disables the limit")
    args = parser.parse_args(argv)
    try:
        if args.max_pages < 0:
            raise ResumeError("--max-pages must be nonnegative")
        raw = args.input.read_bytes()
        data = validate(json.loads(raw.decode("utf-8-sig")))
        font_path = register_font(args.font, all_text(data))
        output_dir = args.output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        pdf_path, html_path = output_dir / "resume.pdf", output_dir / "resume.html"
        pages = write_pdf(data, pdf_path)
        write_html(data, html_path)
        result = {
            "ok": not args.max_pages or pages <= args.max_pages,
            "status": data["status"], "pages": pages, "max_pages": args.max_pages,
            "pdf": str(pdf_path), "html": str(html_path), "font": str(font_path),
            "font_sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
            "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            "html_sha256": hashlib.sha256(html_path.read_bytes()).hexdigest(),
        }
        if not result["ok"]:
            result["error"] = f"Resume exceeds the {args.max_pages}-page budget ({pages} pages). Edit the content or deliberately set --max-pages; font size has not been reduced."
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    except (ResumeError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
