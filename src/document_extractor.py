import os
import logging
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Supported file extensions
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".odt"}


def _extract_pdf(path: Path) -> List[Dict[str, Any]]:
    """Extract text from PDF page-by-page."""
    import pymupdf as fitz

    doc = fitz.open(str(path))
    pages: List[Dict[str, Any]] = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        blocks = page.get_text("blocks")
        text_blocks = [
            b[4].strip()
            for b in sorted(blocks, key=lambda b: (b[1], b[0]))
            if b[6] == 0 and b[4].strip()
        ]
        full_text = "\n\n".join(text_blocks)
        pages.append({"page_number": page_idx + 1, "text": full_text})

    return pages


def _extract_docx(path: Path) -> List[Dict[str, Any]]:
    """Extract text from DOCX document."""
    from docx import Document

    doc = Document(str(path))
    paragraphs: List[str] = []

    for p in doc.paragraphs:
        t = p.text.strip()
        if t:
            paragraphs.append(t)

    # Also extract tables if any
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
            if row_text:
                paragraphs.append(row_text)

    # Approximate pagination (roughly ~350 words per page)
    pages: List[Dict[str, Any]] = []
    current_page_paras: List[str] = []
    current_word_count = 0
    page_num = 1

    for p in paragraphs:
        w_count = len(p.split())
        if current_word_count + w_count > 350 and current_page_paras:
            pages.append({
                "page_number": page_num,
                "text": "\n\n".join(current_page_paras),
            })
            page_num += 1
            current_page_paras = [p]
            current_word_count = w_count
        else:
            current_page_paras.append(p)
            current_word_count += w_count

    if current_page_paras:
        pages.append({
            "page_number": page_num,
            "text": "\n\n".join(current_page_paras),
        })

    if not pages:
        pages.append({"page_number": 1, "text": ""})

    return pages


def _extract_rtf(path: Path) -> List[Dict[str, Any]]:
    """Extract plain text from RTF file."""
    from striprtf.striprtf import rtf_to_text

    raw = path.read_text(encoding="utf-8", errors="ignore")
    plain = rtf_to_text(raw)
    return _split_text_to_pages(plain)


def _extract_plain_text(path: Path) -> List[Dict[str, Any]]:
    """Extract text from TXT or Markdown file."""
    for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
        try:
            content = path.read_text(encoding=enc)
            return _split_text_to_pages(content)
        except UnicodeDecodeError:
            continue
    content = path.read_text(encoding="utf-8", errors="ignore")
    return _split_text_to_pages(content)


def _split_text_to_pages(text: str, words_per_page: int = 350) -> List[Dict[str, Any]]:
    """Helper to partition raw continuous text into numbered pages."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

    pages: List[Dict[str, Any]] = []
    current_paras: List[str] = []
    current_words = 0
    page_num = 1

    for p in paragraphs:
        w_count = len(p.split())
        if current_words + w_count > words_per_page and current_paras:
            pages.append({
                "page_number": page_num,
                "text": "\n\n".join(current_paras),
            })
            page_num += 1
            current_paras = [p]
            current_words = w_count
        else:
            current_paras.append(p)
            current_words += w_count

    if current_paras:
        pages.append({
            "page_number": page_num,
            "text": "\n\n".join(current_paras),
        })

    if not pages:
        pages.append({"page_number": 1, "text": ""})

    return pages


def extract_document_pages(file_path: str) -> List[Dict[str, Any]]:
    """
    Universal document reader that extracts text page-by-page from:
    PDF, DOCX, DOC, TXT, MD, RTF, and ODT.

    Args:
        file_path: Path to the document.

    Returns:
        List of dicts: [{"page_number": int, "text": str}]
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File dokumen tidak ditemukan di: {file_path}")

    ext = path.suffix.lower()
    logger.info(f"Extracting text from '{path.name}' (format: {ext})...")

    if ext == ".pdf":
        return _extract_pdf(path)
    elif ext in [".docx", ".doc"]:
        try:
            return _extract_docx(path)
        except Exception as e:
            logger.warning(f"Failed extracting DOCX via python-docx ({e}), trying text fallback.")
            return _extract_plain_text(path)
    elif ext == ".rtf":
        return _extract_rtf(path)
    elif ext in [".txt", ".md", ".odt"]:
        return _extract_plain_text(path)
    else:
        # Fallback to plain text reader for any text-like file
        return _extract_plain_text(path)
