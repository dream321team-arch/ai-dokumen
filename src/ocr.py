"""OCR untuk halaman PDF hasil scan memakai Tesseract (perintah `tesseract`).

Halaman PDF tanpa teks (isinya gambar) dirender menjadi gambar abu-abu, lalu dibaca Tesseract dengan
bahasa Indonesia + Inggris. Pembacaan dilakukan paralel dalam gumpalan kecil supaya pemakaian memori
tetap terbatas walau dokumennya panjang.
"""
import logging
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any, Dict, List

from src.config import settings
from src.doc_errors import DocumentReadError

logger = logging.getLogger(__name__)

# Halaman dengan teks lebih sedikit dari ini dianggap tidak punya teks dan dibaca dengan OCR.
MIN_PAGE_TEXT_CHARS = 30
# Jumlah halaman yang dirender sekaligus sebelum dibaca (membatasi memori).
_CHUNK_PAGES = 8


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


@lru_cache(maxsize=1)
def _languages() -> str:
    """Bahasa OCR yang benar-benar terpasang, dari setelan AI_OCR_LANGS (mis. "ind+eng")."""
    wanted = [x.strip() for x in settings.AI_OCR_LANGS.split("+") if x.strip()]
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20).stdout.split()
    except Exception:
        return "eng"
    present = [x for x in wanted if x in out]
    if present:
        return "+".join(present)
    return "eng" if "eng" in out else (wanted[0] if wanted else "eng")


def _ocr_png(png: bytes) -> str:
    # OMP_THREAD_LIMIT=1: beberapa Tesseract berjalan paralel, jangan biarkan tiap proses memakai semua core.
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    proc = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", _languages(), "--psm", "3"],
        input=png,
        capture_output=True,
        timeout=settings.AI_OCR_PAGE_TIMEOUT,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "ignore")[:200])
    return proc.stdout.decode("utf-8", "ignore")


def _reflow(text: str) -> str:
    """Gabungkan baris-baris hasil OCR dalam satu paragraf; paragraf dipisah baris kosong."""
    paragraphs = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    out = []
    for p in paragraphs:
        lines = [ln.strip() for ln in p.splitlines() if ln.strip()]
        if lines:
            out.append(" ".join(lines))
    return "\n\n".join(out)


def _safe_ocr(png: bytes, page_number: int) -> str:
    try:
        return _reflow(_ocr_png(png))
    except Exception as e:
        logger.warning(f"OCR halaman {page_number} gagal: {e}")
        return ""


def ocr_pdf_pages(doc: Any, pages: List[Dict[str, Any]], indices: List[int]) -> int:
    """Baca halaman `indices` (nol-berbasis) dengan OCR dan isi `pages[i]["text"]`, tandai `ocr=True`.

    Mengembalikan jumlah halaman yang diproses. Melempar DocumentReadError bila terlalu banyak
    halaman atau OCR tidak tersedia.
    """
    if not indices:
        return 0
    if not settings.AI_OCR_ENABLED or not tesseract_available():
        raise DocumentReadError(
            "PDF ini tampaknya hasil scan (halamannya berupa gambar), dan pembacaan OCR tidak tersedia di server ini."
        )
    if len(indices) > settings.AI_OCR_MAX_PAGES:
        raise DocumentReadError(
            f"PDF hasil scan ini terlalu panjang ({len(indices)} halaman perlu OCR; batasnya {settings.AI_OCR_MAX_PAGES}). "
            "Pisahkan dokumen menjadi beberapa bagian, atau gunakan PDF berteks."
        )

    import pymupdf

    workers = max(1, min(4, os.cpu_count() or 2))
    done = 0
    logger.info(f"OCR {len(indices)} halaman (bahasa: {_languages()}, DPI: {settings.AI_OCR_DPI}, {workers} paralel)...")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(indices), _CHUNK_PAGES):
            chunk = indices[start:start + _CHUNK_PAGES]
            # Merender memakai objek PyMuPDF harus di thread utama; hanya Tesseract-nya yang paralel.
            rendered = [
                (i, doc[i].get_pixmap(dpi=settings.AI_OCR_DPI, colorspace=pymupdf.csGRAY).tobytes("png"))
                for i in chunk
            ]
            texts = list(pool.map(lambda item: _safe_ocr(item[1], item[0] + 1), rendered))
            for (i, _), text in zip(rendered, texts):
                pages[i]["text"] = text
                pages[i]["ocr"] = True
                done += 1
    return done
