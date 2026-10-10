from src.legal_validator import MIN_COVERAGE_FOR_GRADE, calculate_compliance_score


def _summary(total=100, gagal=0, **kw):
    base = {
        "total_blocks": total,
        "gagal_dianalisis": gagal,
        "perlu_revisi": 0,
        "ejaan_tanda_baca": 0,
        "total_span_errors": 0,
        "errors_ejaan": 0,
        "errors_tanda_baca": 0,
        "errors_kosa_kata": 0,
    }
    base.update(kw)
    return base


def test_tanpa_kegagalan_hasil_tidak_berubah():
    # Nilai acuan dihitung dari rumus lama: 10 blok perlu revisi, 20 kesalahan ejaan, 10 tanda baca.
    s = _summary(total=100, perlu_revisi=10, errors_ejaan=20, errors_tanda_baca=10)
    r = calculate_compliance_score(s, {"long_sentences_count": 5, "hierarchy_warnings": 0})
    # pedoman 85, ejaan 90, tanda baca 95, struktur 90 -> 38.25 + 22.5 + 19 + 9 = 88.75
    assert r["overall_score"] == 88.8 or r["overall_score"] == 88.7
    assert r["grade"] == "B"
    assert r["predicate"] == "Baik (Perlu Sedikit Perbaikan)"
    assert r["sub_scores"]["cakupan_pemeriksaan"] == 100.0


def test_semua_blok_gagal_tidak_boleh_mendapat_grade():
    r = calculate_compliance_score(_summary(total=36, gagal=36), {"long_sentences_count": 20})
    assert r["grade"] == "-"
    assert r["overall_score"] == 0.0
    assert "seluruh blok gagal" in r["predicate"]
    assert all(v == 0.0 for v in r["sub_scores"].values())


def test_cakupan_di_bawah_ambang_tidak_diberi_grade():
    r = calculate_compliance_score(_summary(total=100, gagal=30))
    assert r["grade"] == "-"
    assert "30 dari 100 blok gagal diperiksa" in r["predicate"]
    assert r["sub_scores"]["cakupan_pemeriksaan"] == 70.0


def test_gagal_sedikit_tetap_diberi_grade_dengan_catatan():
    r = calculate_compliance_score(_summary(total=100, gagal=5))
    assert r["grade"] == "A"
    assert "5 dari 100 blok gagal diperiksa" in r["predicate"]
    assert r["sub_scores"]["cakupan_pemeriksaan"] == 95.0


def test_tepat_di_ambang_masih_diberi_grade():
    gagal = int(round((1 - MIN_COVERAGE_FOR_GRADE) * 100))  # 20 dari 100 -> cakupan 80%
    assert calculate_compliance_score(_summary(total=100, gagal=gagal))["grade"] != "-"


def test_rasio_kesalahan_dihitung_terhadap_blok_yang_diperiksa():
    # 100 blok, 10 gagal, 90 diperiksa: 9 blok perlu revisi -> rasio 10% (bukan 9%).
    s = _summary(total=100, gagal=10, perlu_revisi=9)
    r = calculate_compliance_score(s)
    assert r["sub_scores"]["pedoman_score"] == 85.0  # 100 - 0.10*100*1.5


def test_data_tanpa_kunci_gagal_tetap_berfungsi():
    # Laporan lama tidak punya kunci gagal_dianalisis di ringkasan.
    s = _summary(total=10)
    del s["gagal_dianalisis"]
    r = calculate_compliance_score(s)
    assert r["grade"] == "A"
