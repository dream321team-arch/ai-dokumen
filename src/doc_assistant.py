import json
import logging
from typing import List, Dict, Any, Optional
from openai import OpenAI
from src.config import settings
from src.vector_store import VectorStore

logger = logging.getLogger(__name__)

ASSISTANT_SYSTEM_PROMPT = """Anda adalah **Asisten Hukum & Ahli Perancang Peraturan Perundang-undangan (Senior Legal Drafter & Compliance AI)** terkemuka di Indonesia.
Anda bertugas mendampingi pengguna dalam menyusun, meninjau, memperbaiki, menganalisis, dan mengonsultasikan naskah dokumen hukum (UU, PP, Perpres, Permen, Perda, SOP, Kontrak Bisnis, dsb) serta menguji kesesuaian kaidah Bahasa Indonesia (PUEBI/EYD V & KBBI).

═══════════════════════════════════════════════════════════════
PEDOMAN FORMAT & GAYA KOMUNIKASI (SANGAT PENTING):
═══════════════════════════════════════════════════════════════
1. **STRUKTUR & TATA LETAK MENARIK (VISUALLY APPEALING)**:
   - Sajikan jawaban dengan format Markdown yang rapi, terstruktur, dan mudah dipindai (scannable).
   - Awali dengan **Ringkasan Eksekutif (Executive Summary)** atau tinjauan ringkas.
   - Gunakan **Heading hierarkis** (`## 1. ...`, `### A. ...`), **Poin Tebal Berbutir**, dan **Tabel Rapi** jika membandingkan aspek/data.
   - Gunakan **Kotak Kutipan (Callout Quote `>`)** untuk catatan krusial, asas hukum, atau peringatan kepatuhan.

2. **FORMULASI SEBELUM & SESUDAH (BEFORE & AFTER)**:
   - Jika merekomendasikan perbaikan kalimat atau norma pasal, WAJIB sertakan perbandingan jelas:
     * **Naskah Semula / Masalah**: (tunjukkan kelemahan atau klausul ambigu/typo)
     * **Rekomendasi Rumusan Baku**: (tuliskan rumusan norma utuh yang presisi dan formal)
     * **Dasar Kaidah / Rujukan**: (jelaskan asas hukum atau aturan EYD/KBBI yang mendasarinya)

3. **LANDASAN HUKUM & KEBAHASAAN KUAT**:
   - Rujuk aturan baku: UU No. 12 Tahun 2011 jo. UU No. 13 Tahun 2022 (Pembentukan Peraturan Perundang-undangan), asas-asas hukum terkait, serta kamus resmi KBBI & PUEBI/EYD Edisi V.
   - Hindari bahasa yang bertele-tele; gunakan bahasa Indonesia formal, presisi, lugas, dan santun.

4. **INTEGRITAS OUTPUT**:
   - Selesaikan seluruh analisis sampai tuntas tanpa terpotong di tengah kalimat.
   - Akhiri dengan **Langkah Tindak Lanjut / Rekomendasi Aksi** yang konkret.
"""


# Batas konteks dokumen yang dikirim ke model (karakter). Temuan didahulukan karena
# paling sering ditanyakan; sisa jatah dipakai untuk isi naskah per blok.
MAX_REPORT_CONTEXT_CHARS = 60000
MAX_FINDINGS_CHARS = 30000


def _format_span_error(se: Dict[str, Any]) -> str:
    tipe = se.get("error_type") or "-"
    tingkat = se.get("severity") or "-"
    asli = (se.get("original_snippet") or "").strip()
    saran = (se.get("suggested_snippet") or "").strip()
    alasan = (se.get("explanation") or "").strip()
    line = f"    - [{tipe}/{tingkat}] \"{asli}\""
    if saran:
        line += f" -> \"{saran}\""
    if alasan:
        line += f" ({alasan})"
    return line


def build_report_context(
    report: Optional[Dict[str, Any]], max_chars: int = MAX_REPORT_CONTEXT_CHARS
) -> str:
    """Susun konteks dokumen aktif dari laporan hasil pemeriksaan (CheckReport sebagai dict).

    Isinya: identitas dan skor, daftar temuan per blok, lalu isi naskah per blok.
    Sebelumnya asisten hanya menerima nama file sehingga tidak bisa menjawab
    pertanyaan seperti "kesalahan apa saja di dokumen tadi".
    """
    if not isinstance(report, dict):
        return ""
    blocks = report.get("blocks") or []
    if not blocks:
        return ""

    skor = report.get("compliance_score") or {}
    ringkasan = report.get("summary") or {}
    metrik = report.get("legal_metrics") or {}

    header = [f"Nama file: {report.get('document_checked', '-')}"]
    if report.get("checked_at"):
        header.append(f"Waktu pemeriksaan: {report['checked_at']}")
    if skor:
        header.append(
            f"Skor kepatuhan: {skor.get('overall_score')} "
            f"(grade {skor.get('grade')}, {skor.get('predicate')})"
        )
        sub = skor.get("sub_scores") or {}
        if sub:
            header.append("Sub-skor: " + ", ".join(f"{k}={v}" for k, v in sub.items()))
    if ringkasan:
        header.append("Ringkasan: " + ", ".join(f"{k}={v}" for k, v in ringkasan.items()))
    if metrik:
        header.append(
            f"Struktur hukum: pasal terdeteksi={metrik.get('pasal_detected')}, "
            f"peringatan hierarki={metrik.get('hierarchy_warnings')}, "
            f"kalimat panjang={metrik.get('long_sentences_count')}"
        )
        for f in (metrik.get("findings") or [])[:20]:
            header.append("  - " + (f if isinstance(f, str) else json.dumps(f, ensure_ascii=False))[:300])
    header_text = "\n".join(header)

    temuan = []
    for b in blocks:
        status = b.get("status")
        spans = b.get("span_errors") or []
        if status == "sesuai" and not spans:
            continue
        entry = [f"- {b.get('block_id')} (hal. {b.get('page_number', 1)}), status: {status}"]
        if b.get("issue"):
            entry.append(f"  Masalah: {b['issue']}")
        ref = b.get("rule_reference") or {}
        if ref.get("document"):
            entry.append(f"  Rujukan: {ref.get('document')} hal. {ref.get('page')}")
        if b.get("suggested_revision"):
            entry.append(f"  Usulan revisi: {b['suggested_revision']}")
        entry.extend(_format_span_error(se) for se in spans)
        temuan.append("\n".join(entry))

    temuan_text, dipotong_temuan = "", 0
    for i, t in enumerate(temuan):
        if len(temuan_text) + len(t) > MAX_FINDINGS_CHARS:
            dipotong_temuan = len(temuan) - i
            break
        temuan_text += t + "\n"
    if dipotong_temuan:
        temuan_text += f"(... {dipotong_temuan} blok bertemuan lainnya tidak ditampilkan karena terlalu panjang)\n"
    if not temuan_text:
        temuan_text = "(Tidak ada temuan: seluruh blok dinyatakan sesuai.)\n"

    jatah = max_chars - len(header_text) - len(temuan_text) - 300
    naskah, terpakai, terpotong = [], 0, False
    for b in blocks:
        teks = (b.get("original_text") or "").strip()
        if not teks:
            continue
        baris = f"[{b.get('block_id')} hal.{b.get('page_number', 1)}] {teks}"
        if terpakai + len(baris) > jatah:
            terpotong = True
            break
        naskah.append(baris)
        terpakai += len(baris) + 1
    naskah_text = "\n".join(naskah)
    if terpotong:
        naskah_text += "\n(... naskah dipotong karena terlalu panjang; sebagian akhir dokumen tidak terlihat)"

    return (
        f"{header_text}\n\n"
        f"--- TEMUAN PEMERIKSAAN PER BLOK ---\n{temuan_text}\n"
        f"--- ISI NASKAH (per blok) ---\n{naskah_text}"
    )


class DocumentAssistant:
    """Conversational assistant powered by Groq (Llama 3.3 70B) with NVIDIA (GPT-OSS-120B) fallback."""

    def __init__(self, model: Optional[str] = None):
        if settings.OPENROUTER_API_KEY and settings.OPENROUTER_API_KEY.strip():
            self.api_key = settings.OPENROUTER_API_KEY.strip()
            self.base_url = settings.OPENROUTER_BASE_URL
            self.model = model or settings.OPENROUTER_MODEL or "myio"
            self.provider = "9router/OpenRouter"
        elif settings.GROQ_API_KEY and settings.GROQ_API_KEY.strip():
            self.api_key = settings.GROQ_API_KEY.strip()
            self.base_url = settings.GROQ_BASE_URL
            self.model = model or settings.GROQ_MODEL or "llama-3.3-70b-versatile"
            self.provider = "Groq"
        else:
            self.api_key = (settings.NVIDIA_CHAT_API_KEY or settings.NVIDIA_API_KEY).strip()
            self.base_url = settings.NVIDIA_BASE_URL
            self.model = model or settings.NVIDIA_CHAT_MODEL or "openai/gpt-oss-120b"
            self.provider = "NVIDIA"

        self.client = None

        if self.api_key:
            try:
                self.client = OpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    timeout=120.0,
                )
                logger.info(f"{self.provider} Chat Assistant client initialized (model: {self.model})")
            except Exception as e:
                logger.warning(f"Failed to init Assistant client: {e}")

    def chat(
        self,
        user_message: str,
        chat_history: Optional[List[Dict[str, str]]] = None,
        document_context: Optional[str] = None,
        block_context: Optional[str] = None,
        report: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Processes user consultation with contextual awareness of the active document and reference guidelines.

        `report` adalah laporan pemeriksaan lengkap (hasil /api/check) dan dipakai untuk
        menyusun konteks dokumen aktif. `document_context` hanya cadangan untuk klien lama
        yang mengirim teks mentah.
        """
        if not self.client:
            return "Koneksi AI Assistant belum siap. Harap pastikan GROQ_API_KEY atau NVIDIA_API_KEY telah diisi di file .env."

        # RAG query on guidelines if relevant
        vector_store = VectorStore()
        guideline_chunks = vector_store.query(text=user_message, top_k=3)
        guidelines_snippet = "\n".join([f"- ({c.document_name} Hal {c.page_number}): {c.text[:300]}" for c in guideline_chunks]) if guideline_chunks else "Tidak ada cuplikan pedoman spesifik."

        system_instruction = ASSISTANT_SYSTEM_PROMPT
        report_context = build_report_context(report)
        if report_context:
            system_instruction += (
                f"\n\n=== DOKUMEN AKTIF (HASIL PEMERIKSAAN & ISI NASKAH) ===\n{report_context}\n\n"
                "Petunjuk: bagian TEMUAN adalah hasil pemeriksaan otomatis atas dokumen ini; pakai untuk "
                "menjawab pertanyaan seperti \"kesalahan apa saja\". Bagian ISI NASKAH adalah teks asli per blok. "
                "Jangan mengatakan dokumen tidak tersedia. Sebut ID blok dan halaman saat merujuk, dan "
                "sampaikan keterbatasannya bila ada bagian yang dinyatakan dipotong."
            )
        elif document_context:
            system_instruction += f"\n\n=== DOKUMEN AKTIF ===\n{document_context[:3000]}"
        else:
            system_instruction += (
                "\n\n=== DOKUMEN AKTIF ===\nBelum ada dokumen yang diperiksa pada sesi ini. Jika pengguna "
                "menanyakan \"dokumen tadi\", minta mereka mengunggah dan memeriksa dokumen terlebih dahulu "
                "di tab Pemeriksaan."
            )
        if block_context:
            system_instruction += f"\n\n=== BLOK TEKS YANG DITANYAKAN ===\n{block_context}"
        if guidelines_snippet:
            system_instruction += f"\n\n=== RUJUKAN PEDOMAN TERKAIT ===\n{guidelines_snippet}"

        messages = [{"role": "system", "content": system_instruction}]

        if chat_history:
            for ch in chat_history[-6:]:
                messages.append({"role": ch.get("role", "user"), "content": ch.get("content", "")})

        messages.append({"role": "user", "content": user_message})

        try:
            res = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                max_tokens=4096,
            )
            return res.choices[0].message.content or "Tidak ada respons dari AI."
        except Exception as e:
            logger.error(f"Chat assistant error with primary provider ({self.provider}): {e}")
            
            # Fallback to NVIDIA if primary was Groq and failed
            if self.provider == "Groq" and (settings.NVIDIA_CHAT_API_KEY or settings.NVIDIA_API_KEY):
                try:
                    logger.info("Attempting fallback to NVIDIA client...")
                    fallback_key = (settings.NVIDIA_CHAT_API_KEY or settings.NVIDIA_API_KEY).strip()
                    fallback_client = OpenAI(base_url=settings.NVIDIA_BASE_URL, api_key=fallback_key)
                    fallback_res = fallback_client.chat.completions.create(
                        model=settings.NVIDIA_CHAT_MODEL or "openai/gpt-oss-120b",
                        messages=messages,
                        temperature=0.3,
                        max_tokens=4096,
                    )
                    return fallback_res.choices[0].message.content or "Tidak ada respons dari AI."
                except Exception as fb_err:
                    logger.error(f"Fallback error: {fb_err}")

            return f"Terjadi kesalahan saat memproses pertanyaan: {str(e)}"
