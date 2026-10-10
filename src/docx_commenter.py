"""Ekspor "Naskah Asli + Komentar".

Membuka file .docx ASLI yang diunggah pengguna, lalu menempelkan komentar Word
asli pada bagian yang salah. Isi, margin, huruf, tabel, dan tata letak tidak
diubah: yang bertambah hanya penanda komentar. Satu-satunya perubahan pada
isi dokumen adalah memecah sebuah run teks di batas kata yang dikomentari,
dengan format run yang sama persis (salinan rPr), sehingga tampilannya tidak
berubah.

Pencocokan posisi: teks kesalahan (original_snippet) dari AI dicari kembali di
paragraf dokumen. Paragraf milik blok yang bersangkutan didahulukan, lalu
dicari ke seluruh dokumen sebagai cadangan. Pencocokan mengabaikan perbedaan
spasi (spasi ganda, NBSP, tab).
"""
import copy
import io
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from docx import Document
from docx.oxml.ns import qn
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from src.schemas import BlockReviewResult, CheckReport

logger = logging.getLogger(__name__)

AUTHOR = "AI Document Checker"
INITIALS = "AI"
MAX_COMMENT_CHARS = 1500

ERROR_TYPE_LABELS = {
    "pedoman": "Pedoman/UU",
    "ejaan": "Ejaan",
    "tanda_baca": "Tanda Baca",
    "kosa_kata": "Kosa Kata",
}
SEVERITY_LABELS = {"high": "Tinggi", "medium": "Sedang", "low": "Rendah"}


@dataclass
class AnnotateStats:
    placed: int = 0
    unplaced: int = 0
    unplaced_items: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Normalisasi teks dengan peta indeks (spasi beruntun -> satu spasi)
# --------------------------------------------------------------------------
def _normalize_with_map(text: str) -> Tuple[str, List[Tuple[int, int]]]:
    """Kembalikan (teks_ternormalisasi, peta) dengan peta[i] = (awal, akhir) di teks asli."""
    out: List[str] = []
    spans: List[Tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            if out and out[-1] != " ":
                out.append(" ")
                spans.append((i, j))
            elif not out:
                pass  # buang spasi di awal
            i = j
        else:
            out.append(ch)
            spans.append((i, i + 1))
            i += 1
    while out and out[-1] == " ":  # buang spasi di akhir
        out.pop()
        spans.pop()
    return "".join(out), spans


def _norm(text: str) -> str:
    return _normalize_with_map(text)[0]


# --------------------------------------------------------------------------
# Akses run dalam paragraf
# --------------------------------------------------------------------------
def _paragraph_runs(paragraph: Paragraph) -> List[Run]:
    """Semua run berteks dalam urutan dokumen (termasuk di dalam hyperlink)."""
    runs: List[Run] = []
    for item in paragraph.iter_inner_content():
        if isinstance(item, Hyperlink):
            runs.extend(item.runs)
        else:
            runs.append(item)
    return runs


def _plain_text_run(run: Run) -> bool:
    """True bila isi run hanya teks biasa (w:t), sehingga aman dipecah."""
    for child in run._r:
        if child.tag == qn("w:rPr"):
            continue
        if child.tag != qn("w:t"):
            return False
    return True


def _set_text(run_el, text: str) -> None:
    t_elements = run_el.findall(qn("w:t"))
    t_elements[0].text = text
    t_elements[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for extra in t_elements[1:]:
        run_el.remove(extra)


def _split_run(run: Run, offset: int) -> Run:
    """Pecah run di `offset` menjadi dua run berformat sama. Kembalikan run bagian kanan."""
    text = run.text
    right_el = copy.deepcopy(run._r)
    _set_text(run._r, text[:offset])
    _set_text(right_el, text[offset:])
    run._r.addnext(right_el)
    return Run(right_el, run._parent)


def _isolate_range(paragraph: Paragraph, start: int, end: int) -> List[Run]:
    """Pastikan teks [start, end) pada paragraf berada tepat di batas run; kembalikan run-nya.

    Run yang bukan teks biasa (tab, line break, gambar, dsb.) tidak dipecah:
    rentang diperluas ke batas run tersebut.
    """
    # Pecah batas akhir dahulu, lalu batas awal, supaya offset tidak bergeser.
    for boundary in (end, start):
        pos = 0
        for run in _paragraph_runs(paragraph):
            length = len(run.text)
            if pos < boundary < pos + length and _plain_text_run(run):
                _split_run(run, boundary - pos)
                break
            pos += length

    selected: List[Run] = []
    pos = 0
    for run in _paragraph_runs(paragraph):
        length = len(run.text)
        run_start, run_end = pos, pos + length
        pos = run_end
        if length == 0:
            continue
        if run_end > start and run_start < end:
            selected.append(run)
    return selected


# --------------------------------------------------------------------------
# Daftar paragraf dokumen
# --------------------------------------------------------------------------
@dataclass
class _Para:
    index: int
    paragraph: Paragraph
    text: str
    norm: str
    norm_map: List[Tuple[int, int]]


def _collect_paragraphs(doc) -> List[_Para]:
    """Semua paragraf isi dokumen (tubuh dan sel tabel), sesuai urutan dokumen."""
    paras: List[_Para] = []
    for p_el in doc.element.body.iter(qn("w:p")):
        paragraph = Paragraph(p_el, doc._body)
        text = paragraph.text
        if not text.strip():
            continue
        norm, norm_map = _normalize_with_map(text)
        paras.append(_Para(len(paras), paragraph, text, norm, norm_map))
    return paras


# --------------------------------------------------------------------------
# Isi komentar
# --------------------------------------------------------------------------
def _span_comment_text(err, block: BlockReviewResult) -> str:
    tipe = ERROR_TYPE_LABELS.get(err.error_type, str(err.error_type))
    tingkat = SEVERITY_LABELS.get(err.severity, str(err.severity))
    lines = [f"[{tipe} | {tingkat}]"]
    saran = (err.suggested_snippet or "").strip()
    asli = (err.original_snippet or "").strip()
    if saran:
        lines.append(f"\"{asli}\" -> \"{saran}\"")
    if err.explanation:
        lines.append(err.explanation.strip())
    ref = block.rule_reference
    if err.error_type == "pedoman" and ref and ref.document:
        lines.append(f"Rujukan: {ref.document}" + (f", hal. {ref.page}" if ref.page else ""))
    return "\n".join(lines)[:MAX_COMMENT_CHARS]


def _block_comment_text(block: BlockReviewResult) -> Optional[str]:
    if block.status == "gagal_dianalisis":
        return "[Belum diperiksa] Blok ini gagal dianalisis AI. Mohon tinjau secara manual."
    if block.status == "perlu_revisi":
        lines = ["[Pedoman/UU]"]
        if block.issue:
            lines.append(block.issue.strip())
        if block.suggested_revision:
            lines.append(f"Usulan revisi: {block.suggested_revision.strip()}")
        ref = block.rule_reference
        if ref and ref.document:
            lines.append(f"Rujukan: {ref.document}" + (f", hal. {ref.page}" if ref.page else ""))
        return "\n".join(lines)[:MAX_COMMENT_CHARS]
    return None


# --------------------------------------------------------------------------
# Penempatan komentar
# --------------------------------------------------------------------------
def _add_comment(doc, runs: List[Run], text: str) -> None:
    doc.add_comment(runs=runs, text=text, author=AUTHOR, initials=INITIALS)


def _paragraphs_of_block(paras: List[_Para], block_norm: str) -> List[_Para]:
    return [p for p in paras if p.norm and p.norm in block_norm]


def _find_in_paragraph(para: _Para, snippet_norm: str, used: set) -> Optional[Tuple[int, int]]:
    """Cari kemunculan snippet yang belum dipakai; kembalikan (awal, akhir) di teks asli paragraf."""
    start = 0
    while True:
        pos = para.norm.find(snippet_norm, start)
        if pos < 0:
            return None
        key = (para.index, pos)
        if key not in used:
            used.add(key)
            o_start = para.norm_map[pos][0]
            o_end = para.norm_map[pos + len(snippet_norm) - 1][1]
            return o_start, o_end
        start = pos + 1


def annotate_docx(source_bytes: bytes, report: CheckReport) -> Tuple[io.BytesIO, AnnotateStats]:
    """Tempelkan komentar hasil pemeriksaan ke dokumen .docx asli."""
    doc = Document(io.BytesIO(source_bytes))
    stats = AnnotateStats()
    paras = _collect_paragraphs(doc)
    used: set = set()

    # Susun semua tugas terlebih dahulu (pada teks asli, sebelum ada run yang dipecah),
    # karena memecah run mengubah struktur run tetapi tidak teks paragraf.
    for block in report.blocks:
        block_norm = _norm(block.original_text)
        own_paras = _paragraphs_of_block(paras, block_norm)

        for err in block.span_errors or []:
            snippet_norm = _norm(err.original_snippet or "")
            label = f"{block.block_id}: \"{(err.original_snippet or '').strip()[:60]}\""
            if not snippet_norm:
                stats.unplaced += 1
                stats.unplaced_items.append(label)
                continue

            found: Optional[Tuple[_Para, int, int]] = None
            for candidates in (own_paras, paras):
                for para in candidates:
                    hit = _find_in_paragraph(para, snippet_norm, used)
                    if hit:
                        found = (para, hit[0], hit[1])
                        break
                if found:
                    break

            if not found:
                stats.unplaced += 1
                stats.unplaced_items.append(label)
                continue

            para, o_start, o_end = found
            runs = _isolate_range(para.paragraph, o_start, o_end)
            if not runs:
                stats.unplaced += 1
                stats.unplaced_items.append(label)
                continue
            _add_comment(doc, runs, _span_comment_text(err, block))
            stats.placed += 1

        block_text = _block_comment_text(block)
        if block_text:
            if own_paras:
                runs = _paragraph_runs(own_paras[0].paragraph)
                runs = [r for r in runs if r.text]
                if runs:
                    _add_comment(doc, [runs[0], runs[-1]], block_text)
                    stats.placed += 1
                    continue
            stats.unplaced += 1
            stats.unplaced_items.append(f"{block.block_id}: komentar blok")

    out = io.BytesIO()
    doc.save(out)
    out.seek(0)
    return out, stats
