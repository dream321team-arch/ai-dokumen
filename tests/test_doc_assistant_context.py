from src.doc_assistant import build_report_context


def _report():
    return {
        "document_checked": "bahan test ai.docx",
        "compliance_score": {
            "overall_score": 83.0,
            "grade": "B",
            "predicate": "Baik",
            "sub_scores": {"ejaan_score": 67.0},
        },
        "summary": {"total_blocks": 3, "sesuai": 1, "ejaan_tanda_baca": 1},
        "legal_metrics": {"pasal_detected": 2, "hierarchy_warnings": 0, "long_sentences_count": 1},
        "blocks": [
            {
                "block_id": "block_1",
                "page_number": 1,
                "status": "sesuai",
                "original_text": "Daerah adalah Provinsi Riau.",
                "span_errors": [],
            },
            {
                "block_id": "block_2",
                "page_number": 2,
                "status": "ejaan_tanda_baca",
                "issue": "Kata waijb salah ketik",
                "original_text": "Setiap orang waijb menjaga ketertiban umum.",
                "span_errors": [
                    {
                        "error_type": "ejaan",
                        "severity": "high",
                        "original_snippet": "waijb",
                        "suggested_snippet": "wajib",
                        "explanation": "Ejaan tidak baku",
                    }
                ],
            },
        ],
    }


def test_konteks_memuat_temuan_dan_isi_naskah():
    ctx = build_report_context(_report())
    assert "bahan test ai.docx" in ctx
    assert "grade B" in ctx
    assert "block_2" in ctx and "waijb" in ctx and "wajib" in ctx
    assert "Setiap orang waijb menjaga ketertiban umum." in ctx
    assert "Daerah adalah Provinsi Riau." in ctx


def test_blok_sesuai_tidak_masuk_daftar_temuan():
    ctx = build_report_context(_report())
    temuan = ctx.split("--- ISI NASKAH")[0]
    assert "block_1" not in temuan


def test_laporan_kosong_atau_tidak_valid_menghasilkan_string_kosong():
    assert build_report_context(None) == ""
    assert build_report_context({}) == ""
    assert build_report_context({"blocks": []}) == ""


def test_naskah_dipotong_saat_melebihi_batas():
    r = _report()
    r["blocks"] = [
        {"block_id": f"block_{i}", "page_number": 1, "status": "sesuai", "original_text": "kata " * 200}
        for i in range(50)
    ]
    ctx = build_report_context(r, max_chars=5000)
    assert "naskah dipotong" in ctx
    assert len(ctx) < 6000
