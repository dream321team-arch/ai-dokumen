"""Pembaca format yang tidak ditangani python-docx/PyMuPDF: .doc (Word 97-2003) dan .odt (OpenDocument)."""
import logging
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

from src.doc_errors import DocumentReadError

logger = logging.getLogger(__name__)

_MAX_ODT_XML_BYTES = 100 * 1024 * 1024  # batas isi content.xml setelah dikompresi (cegah ZIP bomb)
_ODF_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def extract_doc_paragraphs(path: Path) -> List[str]:
    """Baca file .doc biner lama lewat `antiword`; kembalikan daftar paragraf."""
    exe = shutil.which("antiword")
    if not exe:
        raise DocumentReadError(
            "Pembaca file .doc tidak tersedia di server ini. Simpan dokumen sebagai .docx lalu unggah ulang."
        )
    try:
        proc = subprocess.run(
            [exe, "-m", "UTF-8.txt", "-w", "0", str(path)],
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise DocumentReadError("Membaca file .doc terlalu lama. Simpan ulang sebagai .docx lalu unggah ulang.")
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "ignore").strip()[:150]
        logger.warning(f"antiword gagal untuk '{path.name}': {detail}")
        raise DocumentReadError(
            "File .doc tidak dapat dibaca (mungkin rusak, dikunci kata sandi, atau bukan dokumen Word). "
            "Simpan ulang sebagai .docx lalu unggah ulang."
        )
    text = proc.stdout.decode("utf-8", "ignore")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _odf_text(element: ET.Element) -> str:
    """Teks satu paragraf ODT, termasuk spasi (text:s), tab, dan baris baru di dalamnya."""
    parts: List[str] = [element.text or ""]
    for child in element:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "s":
            parts.append(" " * int(child.attrib.get(f"{{{_ODF_TEXT}}}c", "1")))
        elif tag == "tab":
            parts.append("\t")
        elif tag == "line-break":
            parts.append("\n")
        else:
            parts.append(_odf_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def extract_odt_paragraphs(path: Path) -> List[str]:
    """Baca file .odt (ZIP berisi content.xml) tanpa pustaka tambahan; kembalikan daftar paragraf."""
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("content.xml")
            if info.file_size > _MAX_ODT_XML_BYTES:
                raise DocumentReadError("Isi file .odt terlalu besar untuk dibaca.")
            xml_bytes = z.read("content.xml")
    except (zipfile.BadZipFile, KeyError):
        raise DocumentReadError("File .odt tidak valid atau rusak. Simpan ulang dari aplikasi pengolah kata lalu unggah ulang.")

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise DocumentReadError("Isi file .odt tidak dapat dibaca. Simpan ulang dari aplikasi pengolah kata lalu unggah ulang.")

    paragraphs: List[str] = []
    for el in root.iter():
        if el.tag in (f"{{{_ODF_TEXT}}}p", f"{{{_ODF_TEXT}}}h"):
            text = _odf_text(el).strip()
            if text:
                paragraphs.append(text)
    return paragraphs
