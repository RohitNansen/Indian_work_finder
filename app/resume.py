from __future__ import annotations

import io
import re
import shutil
import subprocess
from pathlib import Path

from docx import Document
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from xml.sax.saxutils import escape

from .config import settings


MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def extract_text(data: bytes, filename: str) -> tuple[str, str]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("The resume must be smaller than 5 MB")
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        document = Document(io.BytesIO(data))
        paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                paragraphs.append(" | ".join(c.text.strip() for c in row.cells))
        text = "\n".join(paragraphs)
        style_sample = "\n".join(paragraphs[:20])
    elif suffix == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        style_sample = "\n".join(text.splitlines()[:30])
    else:
        raise ValueError("Upload a PDF or Word .docx file")
    if len(text.strip()) < 80:
        raise ValueError("I could not read enough text from this file. A scanned PDF may need OCR.")
    return text, style_sample


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._-]", "", value).strip()[:80] or "Resume"


def apply_changes(text: str, changes: list[dict]) -> str:
    for change in changes:
        original, replacement = change["original"], change["replacement"]
        if original not in text:
            raise ValueError("An edit no longer matches the uploaded resume")
        text = text.replace(original, replacement, 1)
    return text


def _replace_in_docx(document: Document, changes: list[dict]) -> bool:
    """Preserve paragraph styles and as much inline formatting as possible."""
    all_paragraphs = list(document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                all_paragraphs.extend(cell.paragraphs)
    for change in changes:
        original, replacement = change["original"], change["replacement"]
        found = False
        for paragraph in all_paragraphs:
            if original not in paragraph.text:
                continue
            new_text = paragraph.text.replace(original, replacement, 1)
            if paragraph.runs:
                paragraph.runs[0].text = new_text
                for run in paragraph.runs[1:]:
                    run.text = ""
            else:
                paragraph.add_run(new_text)
            found = True
            break
        if not found:
            return False
    return True


def export_variant(resume_path: Path, original_text: str, changes: list[dict],
                   name: str, company: str, variant_id: int) -> tuple[Path, Path]:
    base = safe_filename(f"{name} - {company}")
    folder = settings.data_dir / "exports" / str(variant_id)
    folder.mkdir(parents=True, exist_ok=True)
    docx_path = folder / f"{base}.docx"
    pdf_path = folder / f"{base}.pdf"
    edited_text = apply_changes(original_text, changes)

    if resume_path.suffix.lower() == ".docx":
        document = Document(resume_path)
        if not _replace_in_docx(document, changes):
            raise ValueError("An edit spans document formatting; review the proposal before export")
    else:
        document = Document()
        for line in edited_text.splitlines():
            if line.strip():
                document.add_paragraph(line.strip())
    document.save(docx_path)

    # LibreOffice preserves DOCX layout when installed; ReportLab is the light fallback.
    libreoffice = shutil.which("libreoffice") or shutil.which("soffice")
    if libreoffice:
        try:
            subprocess.run([libreoffice, "-env:UserInstallation=file:///tmp/work-engine-lo",
                            "--headless", "--convert-to", "pdf", "--outdir", str(folder),
                            str(docx_path)], timeout=40, check=True, capture_output=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    if not pdf_path.exists():
        _simple_pdf(edited_text, pdf_path)
    return docx_path, pdf_path


def _simple_pdf(text: str, output: Path) -> None:
    styles = {
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.5,
                               leading=13, textColor=colors.HexColor("#243041")),
        "heading": ParagraphStyle("heading", fontName="Helvetica-Bold", fontSize=12,
                                  leading=16, spaceBefore=9, textColor=colors.HexColor("#162b42")),
    }
    story = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            story.append(Spacer(1, 6))
            continue
        heading = len(line) < 65 and (line.isupper() or line.endswith(":"))
        story.append(Paragraph(escape(line), styles["heading" if heading else "body"]))
        story.append(Spacer(1, 3))
    SimpleDocTemplate(str(output), pagesize=A4, rightMargin=46, leftMargin=46,
                      topMargin=42, bottomMargin=42).build(story)
