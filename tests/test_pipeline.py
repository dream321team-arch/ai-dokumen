import pytest
from datetime import datetime
from src.schemas import (
    RuleReference,
    BlockReviewResult,
    CheckReport,
    Chunk,
    Block,
)
from src.chunker import chunk_pedoman, split_into_blocks
from src.pdf_extractor import extract_pages


def test_rule_reference_schema():
    ref = RuleReference(document="Pedoman_A.pdf", page=12, section="BAB II Pasal 4")
    assert ref.document == "Pedoman_A.pdf"
    assert ref.page == 12
    assert ref.section == "BAB II Pasal 4"


def test_block_review_result_schema():
    ref = RuleReference(document="Pedoman_A.pdf", page=5, section="Pasal 1")
    result = BlockReviewResult(
        block_id="block_1",
        original_text="Format dokumen harus menggunakan Times New Roman 12pt.",
        status="perlu_revisi",
        issue="Font yang digunakan Arial 10pt",
        rule_reference=ref,
        suggested_revision="Ubah font ke Times New Roman 12pt.",
    )

    assert result.status == "perlu_revisi"
    assert result.rule_reference is not None
    assert result.rule_reference.page == 5


def test_check_report_schema():
    result = BlockReviewResult(
        block_id="block_1",
        original_text="Judul dokumen.",
        status="sesuai",
    )
    report = CheckReport(
        document_checked="draf.pdf",
        checked_at=datetime.now(),
        summary={"total_blocks": 1, "sesuai": 1, "perlu_revisi": 0, "tidak_ditemukan_rujukan": 0},
        blocks=[result],
    )

    assert report.document_checked == "draf.pdf"
    assert len(report.blocks) == 1
    assert report.summary["sesuai"] == 1


def test_chunk_pedoman():
    sample_pages = [
        {
            "page_number": 1,
            "text": "BAB I PENDAHULUAN\n\nPasal 1 Ketentuan Umum.\nIni adalah teks dokumen pedoman yang cukup panjang.",
        }
    ]
    chunks = chunk_pedoman(sample_pages, document_name="Pedoman_Testing.pdf")
    assert len(chunks) > 0
    assert chunks[0].document_name == "Pedoman_Testing.pdf"
    assert chunks[0].page_number == 1


def test_split_into_blocks():
    sample_pages = [
        {
            "page_number": 1,
            "text": (
                "Paragraf pertama yang berisi penjelasan mengenai tata cara penyusunan laporan tahunan perusahaan. "
                "Setiap laporan harus disusun sesuai standar operasional yang berlaku.\n\n"
                "Paragraf pendek."
            ),
        }
    ]
    blocks = split_into_blocks(sample_pages)
    assert len(blocks) == 1  # Short paragraph merged into previous
    assert blocks[0].block_id == "block_1"


def test_pdf_extractor_file_not_found():
    with pytest.raises(FileNotFoundError):
        extract_pages("non_existent_file.pdf")


def test_nvidia_reviewer_uninitialized():
    from src.nvidia_reviewer import NvidiaReviewer
    reviewer = NvidiaReviewer(api_key="")
    block = Block(block_id="block_1", text="Tes teks", page_number=1)
    res = reviewer.review_block(block, [])
    assert res.status == "tidak_ditemukan_rujukan"


def test_claude_reviewer_uninitialized():
    from src.claude_reviewer import ClaudeReviewer
    reviewer = ClaudeReviewer(api_key="")
    block = Block(block_id="block_1", text="Tes teks", page_number=1)
    res = reviewer.review_block(block, [])
    assert res.status == "tidak_ditemukan_rujukan"
    assert "ANTHROPIC_API_KEY" in res.issue


def test_settings_attributes():
    from src.config import settings
    assert hasattr(settings, "NVIDIA_API_KEY")
    assert hasattr(settings, "ANTHROPIC_API_KEY")
    assert hasattr(settings, "CLAUDE_MODEL")
    assert hasattr(settings, "NVIDIA_EMBED_URL")
    assert hasattr(settings, "NVIDIA_RERANK_URL")


def test_text_span_error_schema():
    from src.schemas import TextSpanError
    span = TextSpanError(
        original_snippet="mempersiapkan",
        suggested_snippet="menyiapkan",
        error_type="kosa_kata",
        explanation="Penggunaan kosa kata baku"
    )
    res = BlockReviewResult(
        block_id="block_1",
        original_text="Teks mempersiapkan laporan.",
        status="ejaan_tanda_baca",
        issue="Kesalahan kosa kata baku",
        span_errors=[span]
    )
    assert res.status == "ejaan_tanda_baca"
    assert len(res.span_errors) == 1
    assert res.span_errors[0].error_type == "kosa_kata"



