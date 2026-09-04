import io
from datetime import datetime
from typing import List, Dict, Any, Optional
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

from src.schemas import CheckReport, BlockReviewResult


def _set_cell_background(cell, hex_color: str):
    """Set background color of a docx table cell."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    tcPr.append(shd)


def _set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
    """Set internal cell margins."""
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'<w:top w:w="{top}" w:type="dxa"/>'
        f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
        f'<w:left w:w="{left}" w:type="dxa"/>'
        f'<w:right w:w="{right}" w:type="dxa"/>'
        f'</w:tcMar>'
    )
    tcPr.append(tcMar)


def export_clean_docx(report: CheckReport, accepted_block_ids: Optional[List[str]] = None) -> io.BytesIO:
    """
    Generates a clean, revised Word document.
    If a block has suggested_revision and is accepted (or all if None), applies the revision.
    Otherwise retains the original text.
    """
    doc = Document()

    # Set document margins (1 inch)
    for section in doc.sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    # Document Header Title
    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_p.add_run(report.document_checked.replace(".pdf", "").replace(".docx", "").upper())
    title_run.font.name = "Times New Roman"
    title_run.font.size = Pt(14)
    title_run.bold = True

    subtitle_p = doc.add_paragraph()
    subtitle_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = subtitle_p.add_run(f"(Naskah Hasil Penyelarasan & Perbaikan AI - {datetime.now().strftime('%d %B %Y')})")
    sub_run.font.name = "Times New Roman"
    sub_run.font.size = Pt(10)
    sub_run.italic = True
    sub_run.font.color.rgb = RGBColor(100, 116, 139)

    doc.add_paragraph()  # blank spacer

    for block in report.blocks:
        # Determine text to use
        is_accepted = (accepted_block_ids is None) or (block.block_id in accepted_block_ids)
        use_revision = is_accepted and block.suggested_revision and block.suggested_revision.strip()
        final_text = block.suggested_revision.strip() if use_revision else block.original_text.strip()

        paragraphs = final_text.split("\n\n")
        for para_text in paragraphs:
            if not para_text.strip():
                continue
            p = doc.add_paragraph()
            p.paragraph_format.line_spacing = 1.5
            p.paragraph_format.space_after = Pt(6)
            
            # Check if looks like a heading
            is_heading = any(para_text.strip().startswith(h) for h in ["BAB ", "Pasal ", "Bagian ", "MEMUTUSKAN:", "Menimbang:", "Mengingat:"])
            if is_heading and len(para_text.strip().split()) < 10:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER if para_text.strip().startswith(("BAB ", "Bagian ", "MEMUTUSKAN:")) else WD_ALIGN_PARAGRAPH.LEFT
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

            run = p.add_run(para_text.strip())
            run.font.name = "Times New Roman"
            run.font.size = Pt(12)
            if is_heading:
                run.bold = True

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def export_track_changes_docx(report: CheckReport) -> io.BytesIO:
    """
    Generates a Word document highlighting revisions visually in Turnitin/Track Changes style:
    - Strike-through red for original text that was changed
    - Green bold text for accepted revisions/corrected words
    - Margin notes/comments explaining why changes were made
    """
    doc = Document()

    for section in doc.sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    # Title
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_title = p_title.add_run("NASKAH REVIEW DENGAN PERUBAHAN (TRACK CHANGES)")
    r_title.font.name = "Arial"
    r_title.font.size = Pt(14)
    r_title.bold = True
    r_title.font.color.rgb = RGBColor(30, 64, 175)

    p_meta = doc.add_paragraph()
    p_meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_meta = p_meta.add_run(f"Dokumen: {report.document_checked} | Waktu Audit: {report.checked_at.strftime('%d/%m/%Y %H:%M')}")
    r_meta.font.name = "Arial"
    r_meta.font.size = Pt(9.5)
    r_meta.font.color.rgb = RGBColor(100, 116, 139)

    doc.add_paragraph()

    for block in report.blocks:
        p = doc.add_paragraph()
        p.paragraph_format.line_spacing = 1.3
        p.paragraph_format.space_after = Pt(8)

        # Block ID tag
        r_id = p.add_run(f"[{block.block_id}] ")
        r_id.font.name = "Arial"
        r_id.font.size = Pt(8.5)
        r_id.bold = True
        r_id.font.color.rgb = RGBColor(148, 163, 184)

        if block.status == "sesuai" or (not block.span_errors and not block.suggested_revision):
            # No changes - pure text
            r = p.add_run(block.original_text)
            r.font.name = "Times New Roman"
            r.font.size = Pt(11)
        elif block.span_errors:
            # Inline span changes
            text = block.original_text
            # Build segments with inline replacements (non-overlapping)
            raw_matches = []
            for err in block.span_errors:
                snip = err.original_snippet
                if snip and snip in text:
                    pos = text.find(snip)
                    raw_matches.append({
                        "start": pos,
                        "end": pos + len(snip),
                        "err": err
                    })
            raw_matches.sort(key=lambda x: (x["start"], -(x["end"] - x["start"])))

            matches = []
            last_end = -1
            for m in raw_matches:
                if m["start"] >= last_end:
                    matches.append(m)
                    last_end = m["end"]

            cursor = 0
            for m in matches:
                if m["start"] > cursor:
                    r_norm = p.add_run(text[cursor:m["start"]])
                    r_norm.font.name = "Times New Roman"
                    r_norm.font.size = Pt(11)

                # Deleted text (Red Strikethrough)
                r_del = p.add_run(text[m["start"]:m["end"]])
                r_del.font.name = "Times New Roman"
                r_del.font.size = Pt(11)
                r_del.font.strike = True
                r_del.font.color.rgb = RGBColor(220, 38, 38)

                # Added text (Green Bold Underline)
                r_add = p.add_run(f" [{m['err'].suggested_snippet}] ")
                r_add.font.name = "Times New Roman"
                r_add.font.size = Pt(11)
                r_add.bold = True
                r_add.font.color.rgb = RGBColor(22, 163, 74)

                cursor = m["end"]

            if cursor < len(text):
                r_rem = p.add_run(text[cursor:])
                r_rem.font.name = "Times New Roman"
                r_rem.font.size = Pt(11)

            # Add explanation note below paragraph
            p_note = doc.add_paragraph()
            p_note.paragraph_format.left_indent = Inches(0.4)
            p_note.paragraph_format.space_after = Pt(6)
            for err in block.span_errors:
                r_note = p_note.add_run(f"• ({err.error_type.upper()}) \"{err.original_snippet}\" ➔ \"{err.suggested_snippet}\": {err.explanation}\n")
                r_note.font.name = "Arial"
                r_note.font.size = Pt(8.5)
                r_note.font.color.rgb = RGBColor(100, 116, 139)
        else:
            # Full block revision
            r_orig = p.add_run(block.original_text + "\n")
            r_orig.font.name = "Times New Roman"
            r_orig.font.size = Pt(10)
            r_orig.font.strike = True
            r_orig.font.color.rgb = RGBColor(220, 38, 38)

            r_rev = p.add_run("➔ Usulan Revisi: " + str(block.suggested_revision))
            r_rev.font.name = "Times New Roman"
            r_rev.font.size = Pt(11)
            r_rev.bold = True
            r_rev.font.color.rgb = RGBColor(22, 163, 74)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def export_audit_report_docx(report: CheckReport, compliance_score: float, grade: str) -> io.BytesIO:
    """
    Generates a formal Executive Audit Report in DOCX format with summary tables,
    compliance score, and breakdown of findings.
    """
    doc = Document()

    # Cover / Header Banner
    title_p = doc.add_paragraph()
    r_main = title_p.add_run("LAPORAN HASIL AUDIT KEPATUHAN & TATA BAHASA NASKAH\n")
    r_main.font.name = "Arial"
    r_main.font.size = Pt(15)
    r_main.bold = True
    r_main.font.color.rgb = RGBColor(30, 64, 175)

    r_sub = title_p.add_run(f"Dokumen Diperiksa: {report.document_checked}\nTanggal Pemeriksaan: {report.checked_at.strftime('%d %B %Y, %H:%M WIB')}")
    r_sub.font.name = "Arial"
    r_sub.font.size = Pt(10)
    r_sub.font.color.rgb = RGBColor(100, 116, 139)

    doc.add_paragraph()

    # Score Card Table
    table_score = doc.add_table(rows=2, cols=4)
    table_score.alignment = WD_TABLE_ALIGNMENT.CENTER
    
    headers = ["SKOR KEPATUHAN", "GRADE KUALITAS", "TOTAL BLOK", "TOTAL KESALAHAN"]
    values = [f"{int(compliance_score)} / 100", f"Predikat {grade}", str(report.summary.get("total_blocks", 0)), str(report.summary.get("total_span_errors", 0))]

    for i, h in enumerate(headers):
        cell = table_score.cell(0, i)
        cell.text = h
        _set_cell_background(cell, "1E40AF")
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for r in p.runs:
            r.font.name = "Arial"
            r.font.size = Pt(8.5)
            r.bold = True
            r.font.color.rgb = RGBColor(255, 255, 255)

    for i, v in enumerate(values):
        cell = table_score.cell(1, i)
        cell.text = v
        _set_cell_background(cell, "EFF6FF")
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for r in p.runs:
            r.font.name = "Arial"
            r.font.size = Pt(12)
            r.bold = True
            r.font.color.rgb = RGBColor(30, 64, 175)

    doc.add_paragraph()

    # Breakdown Table
    doc.add_heading("1. Ringkasan Temuan Kesalahan", level=2)
    t_breakdown = doc.add_table(rows=5, cols=3)
    t_breakdown.alignment = WD_TABLE_ALIGNMENT.CENTER

    b_rows = [
        ("Kategori Masalah", "Jumlah Temuan", "Persentase"),
        ("Pelanggaran Pedoman / UU", str(report.summary.get("errors_pedoman", 0)), "-"),
        ("Kesalahan Tanda Baca (PUEBI/EYD)", str(report.summary.get("errors_tanda_baca", 0)), "-"),
        ("Kosa Kata Tidak Baku (KBBI)", str(report.summary.get("errors_kosa_kata", 0)), "-"),
        ("Kesalahan Ejaan / Typo", str(report.summary.get("errors_ejaan", 0)), "-"),
    ]

    for row_idx, data in enumerate(b_rows):
        for col_idx, text in enumerate(data):
            cell = t_breakdown.cell(row_idx, col_idx)
            cell.text = text
            p = cell.paragraphs[0]
            if row_idx == 0:
                _set_cell_background(cell, "F1F5F9")
                p.runs[0].bold = True
                p.runs[0].font.size = Pt(9)
            else:
                p.runs[0].font.size = Pt(9)

    doc.add_paragraph()

    # Detailed Findings List
    doc.add_heading("2. Rincian Blok yang Perlu Perbaikan", level=2)

    issue_blocks = [b for b in report.blocks if b.status in ["perlu_revisi", "ejaan_tanda_baca"]]
    if not issue_blocks:
        p_ok = doc.add_paragraph("Selamat! Tidak ditemukan kesalahan pedoman, ejaan, tanda baca, maupun kosa kata pada naskah ini.")
        p_ok.runs[0].font.color.rgb = RGBColor(22, 163, 74)
    else:
        for b in issue_blocks:
            p_blk = doc.add_paragraph()
            p_blk.paragraph_format.space_after = Pt(4)
            page_str = getattr(b, 'page_number', None) or 1
            r_b = p_blk.add_run(f"Blok {b.block_id} (Halaman {page_str}) - Status: {b.status.upper()}")
            r_b.bold = True
            r_b.font.size = Pt(10)
            if b.status == "perlu_revisi":
                r_b.font.color.rgb = RGBColor(220, 38, 38)
            else:
                r_b.font.color.rgb = RGBColor(217, 119, 6)

            if b.issue:
                p_iss = doc.add_paragraph()
                p_iss.paragraph_format.left_indent = Inches(0.2)
                p_iss.paragraph_format.space_after = Pt(2)
                r_iss = p_iss.add_run(f"Masalah: {b.issue}")
                r_iss.font.size = Pt(9)

            if b.rule_reference:
                p_ref = doc.add_paragraph()
                p_ref.paragraph_format.left_indent = Inches(0.2)
                p_ref.paragraph_format.space_after = Pt(2)
                r_ref = p_ref.add_run(f"Rujukan: {b.rule_reference.document} (Hal {b.rule_reference.page})")
                r_ref.font.size = Pt(8.5)
                r_ref.font.color.rgb = RGBColor(30, 64, 175)

            if b.suggested_revision:
                p_sug = doc.add_paragraph()
                p_sug.paragraph_format.left_indent = Inches(0.2)
                p_sug.paragraph_format.space_after = Pt(6)
                r_sug = p_sug.add_run(f"Saran Perbaikan: {b.suggested_revision}")
                r_sug.font.size = Pt(9)
                r_sug.font.color.rgb = RGBColor(22, 163, 74)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer
