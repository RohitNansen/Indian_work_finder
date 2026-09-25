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
        paragraphs=[]
        for p in document.element.xpath('.//w:p'):
            nodes=[n for n in p.xpath('.//w:t') if next(n.iterancestors('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p')) is p]
            value=''.join(n.text or '' for n in nodes).strip()
            if value:paragraphs.append(value)
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
    """Edit text nodes in place; retain fonts, headings, tables, spacing and links."""
    from difflib import SequenceMatcher
    paragraphs = document.element.xpath('.//w:p')
    for section in document.sections:
        paragraphs += section.header._element.xpath('.//w:p') + section.footer._element.xpath('.//w:p')
    for change in changes:
        original, replacement = change['original'], change['replacement']
        for paragraph in paragraphs:
            nodes = [n for n in paragraph.xpath('.//w:t') if next(n.iterancestors('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p')) is paragraph]
            text = ''.join(n.text or '' for n in nodes)
            if original not in text:
                continue
            start = text.index(original)
            # Work backwards, preserving all unchanged run formatting.
            for op, i, j, a, b in reversed(SequenceMatcher(None, original, replacement).get_opcodes()):
                if op == 'equal': continue
                left, right = start+i, start+j
                offsets=[]; pos=0
                for n in nodes:
                    value=n.text or ''; offsets.append((n,pos,pos+len(value)));pos+=len(value)
                touched=[(n,lo,hi) for n,lo,hi in offsets if hi>left and lo<right]
                if left == right:
                    touched=[next(((n,lo,hi) for n,lo,hi in offsets if lo<=left<=hi), offsets[-1])]
                if not touched: return False
                for index,(n,lo,hi) in enumerate(touched):
                    value=n.text or ''
                    n.text=value[:max(0,left-lo)] + (replacement[a:b] if index==0 else '') + value[max(0,right-lo):]
                    n.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
            break
        else:
            return False
    return True


def _pdf_with_original_layout(source: Path, target: Path, changes: list[dict]):
    import pymupdf as fitz
    if not changes:
        shutil.copyfile(source,target)
        return
    doc=fitz.open(source)
    def normalized(value):
        return re.sub(r'\s+',' ',value).strip()
    plans={}
    for change in changes:
        old=normalized(change['original']); new=normalized(change['replacement'])
        matches=[]
        for page in doc:
            for block in page.get_text('dict')['blocks']:
                if 'lines' not in block: continue
                text=normalized(' '.join(''.join(s['text'] for s in line['spans']) for line in block['lines']))
                if old in text: matches.append((page.number,block,text))
        if len(matches)!=1:
            raise ValueError('This edit crosses layout areas or repeats in the PDF. Shorten the edit or upload the original Word file to preserve its design.')
        number,block,text=matches[0];key=(number,tuple(block['bbox']))
        plans.setdefault(key,[block,text])[1]=plans.get(key,[block,text])[1].replace(old,new,1)
    for (number,_),(block,text) in plans.items():
        page=doc[number]
        content_lines=[]; spans=[]
        for line in block['lines']:
            content=[s for s in line['spans'] if s['text'].strip() and s['text'].strip() not in {'•','●','▪'}]
            if content:
                content_lines.append(content);spans.extend(content)
        styles={(s['font'],round(s['size'],2),s['color']) for s in spans}
        if len(styles)!=1 or any(line.get('dir',(1,0))!=(1,0) for line in block['lines']):
            raise ValueError('This PDF edit crosses different text styles. Please use a smaller edit or the original Word file.')
        style=spans[0]; fontname=style['font']; buffer=None
        for xref,ext,kind,name,*_ in page.get_fonts():
            if name.split('+')[-1]==fontname:
                buffer=doc.extract_font(xref)[3]
                if buffer: break
        if not buffer:
            raise ValueError('The PDF font cannot be reused. Upload the original Word file to keep its font.')
        font=fitz.Font(fontbuffer=buffer)
        # Do not silently substitute a glyph or shrink the text.
        body=text.lstrip('•●▪ ').strip()
        if any(not font.has_glyph(ord(c)) for c in body if not c.isspace()):
            raise ValueError('The PDF font does not contain a character in this edit. Please use the original Word file.')
        words=body.split(); output=[];right=max(s['bbox'][2] for s in spans)
        for line in content_lines:
            x,y=line[0]['origin']; limit=right-x+0.5; current=[]
            while words and font.text_length(' '.join(current+[words[0]]),fontsize=style['size'])<=limit:
                current.append(words.pop(0))
            output.append(((x,y),' '.join(current)))
        if words:
            raise ValueError('This edit is too long for the original PDF layout. Shorten the proposed wording or upload the original Word file. No files were changed.')
        for span in spans:
            rect=fitz.Rect(span['bbox']); rect.y0+=0.2;rect.y1-=0.2
            page.add_redact_annot(rect,fill=False,cross_out=False)
        page.apply_redactions(images=0,graphics=0)
        alias=f'resumeFont{number}_{len(page.get_fonts())}'
        page.insert_font(fontname=alias,fontbuffer=buffer)
        color=tuple(((style['color']>>shift)&255)/255 for shift in (16,8,0))
        for point,line in output:
            if line:page.insert_text(point,line,fontname=alias,fontsize=style['size'],color=color)
    doc.save(target,garbage=4,deflate=True)
    doc.close()


def _convert_word_pdf(source: Path, folder: Path) -> Path:
    import os
    import tempfile
    binary=os.getenv('LIBREOFFICE_BIN') or shutil.which('libreoffice') or shutil.which('soffice')
    if not binary:
        raise ValueError('Word-to-PDF conversion is unavailable. The owner needs to enable the document converter.')
    with tempfile.TemporaryDirectory(prefix='job-resume-lo-') as profile:
        try:
            subprocess.run([binary, f'-env:UserInstallation={Path(profile).as_uri()}', '--headless',
                            '--convert-to','pdf','--outdir',str(folder),str(source)],
                           timeout=90,check=True,capture_output=True)
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
            raise ValueError('The Word file could not be rendered safely. Please try again or upload the original PDF.') from exc
    result=folder/(source.stem+'.pdf')
    if not result.exists():raise ValueError('The PDF converter did not produce a file')
    return result


def export_variant(resume_path: Path, original_text: str, changes: list[dict],
                   name: str, company: str, variant_id: int) -> tuple[Path, Path]:
    import tempfile
    from .pdf_word_layout import pdf_to_word
    base=safe_filename(f'{name} - {company}')
    folder=settings.data_dir/'exports'/str(variant_id)
    folder.mkdir(parents=True,exist_ok=True)
    apply_changes(original_text,changes)
    # Publish both files only after both have been successfully prepared.
    with tempfile.TemporaryDirectory(prefix='build-',dir=folder) as staging:
        stage=Path(staging);docx=stage/f'{base}.docx';pdf=stage/f'{base}.pdf'
        if resume_path.suffix.lower()=='.docx':
            document=Document(resume_path)
            if not _replace_in_docx(document,changes):
                raise ValueError('An edit crosses paragraphs. Please shorten it to preserve the original formatting.')
            document.save(docx)
            _convert_word_pdf(docx,stage)
        else:
            _pdf_with_original_layout(resume_path,pdf,changes)
            pdf_to_word(pdf,docx)
        final_docx=folder/docx.name;final_pdf=folder/pdf.name
        shutil.move(docx,final_docx);shutil.move(pdf,final_pdf)
    return final_docx,final_pdf
