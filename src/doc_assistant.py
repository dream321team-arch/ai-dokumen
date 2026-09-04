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
    ) -> str:
        """
        Processes user consultation with contextual awareness of the active document and reference guidelines.
        """
        if not self.client:
            return "Koneksi AI Assistant belum siap. Harap pastikan GROQ_API_KEY atau NVIDIA_API_KEY telah diisi di file .env."

        # RAG query on guidelines if relevant
        vector_store = VectorStore()
        guideline_chunks = vector_store.query(text=user_message, top_k=3)
        guidelines_snippet = "\n".join([f"- ({c.document_name} Hal {c.page_number}): {c.text[:300]}" for c in guideline_chunks]) if guideline_chunks else "Tidak ada cuplikan pedoman spesifik."

        system_instruction = ASSISTANT_SYSTEM_PROMPT
        if document_context:
            system_instruction += f"\n\n=== DOKUMEN AKTIF ===\n{document_context[:3000]}"
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
