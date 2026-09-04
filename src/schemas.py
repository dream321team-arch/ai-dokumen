from datetime import datetime
from typing import Literal, Optional, Dict, Any, List
from pydantic import BaseModel, Field


class RuleReference(BaseModel):
    document: str = Field(..., description="Nama dokumen pedoman yang menjadi rujukan")
    page: int = Field(..., description="Nomor halaman dokumen pedoman")
    section: Optional[str] = Field(None, description="Judul bab, pasal, atau bagian jika ada")


class TextSpanError(BaseModel):
    original_snippet: str = Field(..., description="Kutipan PERSIS kata/kalimat asli yang salah dari teks blok (case-sensitive)")
    suggested_snippet: str = Field(..., description="Saran perbaikan kata atau kalimat yang benar")
    error_type: Literal["pedoman", "ejaan", "tanda_baca", "kosa_kata"] = Field(
        ..., description="Kategori kesalahan: pedoman, ejaan, tanda_baca, kosa_kata"
    )
    explanation: str = Field(..., description="Penjelasan mengapa salah dan aturan apa yang dilanggar")
    severity: Literal["high", "medium", "low"] = Field(
        default="medium",
        description="Tingkat keparahan: high, medium, low"
    )


class BlockReviewResult(BaseModel):
    block_id: str = Field(..., description="ID unik dari blok yang diperiksa")
    page_number: Optional[int] = Field(default=1, description="Nomor halaman dokumen upload")
    original_text: str = Field(..., description="Teks asli dari blok dokumen upload")
    status: Literal["sesuai", "perlu_revisi", "ejaan_tanda_baca", "tidak_ditemukan_rujukan"] = Field(
        ..., description="Status hasil pemeriksaan kepatuhan dan kebahasaan"
    )
    issue: Optional[str] = Field(
        None, description="Detail ketidaksesuaian atau masalah utama yang ditemukan"
    )
    rule_reference: Optional[RuleReference] = Field(
        None, description="Rujukan ke dokumen pedoman. WAJIB diisi jika status perlu_revisi"
    )
    suggested_revision: Optional[str] = Field(
        None, description="Saran perbaikan/revisi konkret dari keseluruhan blok"
    )
    span_errors: List[TextSpanError] = Field(
        default_factory=list, description="Daftar kesalahan per spasi/kata/kalimat spesifik"
    )


class ComplianceScoreInfo(BaseModel):
    overall_score: float = Field(..., description="Skor kepatuhan keseluruhan 0-100%")
    grade: str = Field(..., description="Grade predikat: A, B, C, D, E")
    predicate: str = Field(..., description="Penjelasan predikat")
    sub_scores: Dict[str, float] = Field(default_factory=dict, description="Rincian skor per komponen")


class LegalStructureMetric(BaseModel):
    long_sentences_count: int = 0
    hierarchy_warnings: int = 0
    pasal_detected: int = 0
    findings: List[Dict[str, Any]] = Field(default_factory=list)


class CheckReport(BaseModel):
    document_checked: str = Field(..., description="Nama atau path file yang diperiksa")
    checked_at: datetime = Field(default_factory=datetime.now, description="Waktu pemeriksaan")
    summary: Dict[str, int] = Field(..., description="Ringkasan statistik hasil pemeriksaan")
    compliance_score: Optional[ComplianceScoreInfo] = Field(None, description="Informasi skor kepatuhan naskah")
    legal_metrics: Optional[LegalStructureMetric] = Field(None, description="Hasil analisis struktur hukum dan kalimat")
    blocks: List[BlockReviewResult] = Field(..., description="Daftar hasil review per blok")


class Chunk(BaseModel):
    chunk_id: str
    text: str
    document_name: str
    page_number: int
    section_title: Optional[str] = None


class Block(BaseModel):
    block_id: str
    text: str
    page_number: int
