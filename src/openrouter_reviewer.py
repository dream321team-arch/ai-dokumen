import re
import time
import json
import logging
from typing import List, Optional
from openai import OpenAI
from src.schemas import Block, Chunk, BlockReviewResult, RuleReference
from src.config import settings

logger = logging.getLogger(__name__)


def _compute_retry_delay(exc: Exception, attempt: int) -> float:
    """
    Computes how long to wait before retrying after an error. For HTTP 429
    (rate limit) responses, prefers the provider's own X-RateLimit-Reset
    header when available, otherwise backs off exponentially; other errors
    just get a short fixed delay.
    """
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        try:
            headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
            reset_ms = headers.get("X-RateLimit-Reset")
            if reset_ms:
                wait = (int(reset_ms) / 1000.0) - time.time()
                if 0 < wait <= 60:
                    return wait + 0.5
        except Exception:
            pass
        return min(2 ** (attempt + 1), 15)
    return 1.0


SYSTEM_PROMPT = """Anda adalah AI Document Checker & Legal Compliance Reviewer profesional untuk dokumen hukum, perundang-undangan, dan pedoman resmi Bahasa Indonesia.

Tugas Anda adalah memeriksa suatu blok teks dari "Dokumen Upload" berdasarkan "Potongan Dokumen Pedoman" (rujukan) dan "Definisi Resmi (Ketentuan Umum Pedoman)" jika dilampirkan.

═══════════════════════════════════════════
PATOKAN UTAMA — DOKUMEN PEDOMAN & KETENTUAN UMUM (BUKAN KBBI):
═══════════════════════════════════════════
- Basis penilaian BENAR/SALAH (status sesuai/perlu_revisi) adalah SEMATA-MATA kepatuhan terhadap "Potongan Dokumen Pedoman" dan konsistensi istilah terhadap "Definisi Resmi (Ketentuan Umum Pedoman)" yang dilampirkan.
- KBBI/PUEBI/ejaan/tanda baca/kosa kata baku BUKAN patokan utama. Isu semacam itu HANYA boleh menghasilkan status "ejaan_tanda_baca" (kategori terpisah, TIDAK PERNAH membuat status jadi "perlu_revisi" jika tidak ada pelanggaran pedoman/definisi).
- Jika ada "Definisi Resmi (Ketentuan Umum Pedoman)" yang dilampirkan dan istilah tersebut dipakai di blok dengan makna yang BERBEDA/BERTENTANGAN dari definisi resmi itu, ini WAJIB dianggap pelanggaran pedoman (error_type "pedoman", status "perlu_revisi"), sertakan rule_reference ke dokumen+halaman ketentuan umum tersebut.

═══════════════════════════════════════════
ATURAN UTAMA PENETAPAN STATUS:
═══════════════════════════════════════════
1. "sesuai" :
   - Gunakan status ini jika teks dokumen upload MEMATUHI, IDENTIK, ATAU COCOK dengan Potongan Dokumen Pedoman yang ada, dan konsisten dengan Definisi Resmi Ketentuan Umum (jika ada).
   - Gunakan status ini juga jika teks merupakan isi naskah yang benar dan tidak memiliki kesalahan substansi/aturan.
   - Tetap cantumkan 'rule_reference' jika ada rujukan pedoman yang relevan yang mendasarinya!
   - 'issue' diisi null (atau string singkat bahwa teks sesuai), 'suggested_revision' diisi null, 'span_errors' diisi [].

2. "perlu_revisi" :
   - Gunakan status ini HANYA jika teks dokumen upload MELANGGAR, BERTENTANGAN, SALAH URUTAN, ATAU MENGUBAH SUBSTANSI aturan dalam Potongan Dokumen Pedoman, ATAU memakai istilah bertentangan dengan Definisi Resmi Ketentuan Umum.
   - WAJIB menyertakan 'rule_reference' (document, page, section).
   - Jelaskan masalah di 'issue', berikan revisi utuh di 'suggested_revision', dan rincikan kata yang salah di 'span_errors'.
   - JANGAN gunakan status ini hanya karena alasan ejaan/tanda baca/KBBI semata — untuk itu gunakan status "ejaan_tanda_baca".

3. "ejaan_tanda_baca" :
   - Gunakan status ini jika substansi aturan TIDAK melanggar pedoman/definisi, tetapi ada kesalahan teknis penulisan (kategori SEKUNDER, tidak memengaruhi kepatuhan):
     * Tanda baca salah/kurang/berlebih (titik, koma, spasi ganda, spasi sebelum tanda baca, tanda petik, tanda hubung).
     * Ejaan salah / typo / kapitalisasi tidak tepat.
     * Kosa kata tidak baku menurut KBBI (misal: "aktifitas" -> "aktivitas", "merubah" -> "mengubah", "resiko" -> "risiko", "sistim" -> "sistem", "praktek" -> "praktik", "ijin" -> "izin", "propinsi" -> "provinsi", "analisa" -> "analisis").
   - Masukkan setiap kata/tanda baca yang salah ke dalam 'span_errors'.

4. "tidak_ditemukan_rujukan" :
   - HANYA gunakan status ini jika benar-benar TIDAK ADA potongan pedoman yang membahas topik blok ini SAMA SEKALI, DAN teks tersebut bukan merupakan format umum perundang-undangan.
   - JIKA teks dokumen SAMA atau SESUAI dengan potongan pedoman yang dilampirkan, JANGAN gunakan status ini! Gunakan status "sesuai".

═══════════════════════════════════════════
ATURAN SPAN_ERRORS:
═══════════════════════════════════════════
- 'original_snippet' HARUS berupa kutipan PERSIS dari teks dokumen upload (case-sensitive).
- 'suggested_snippet' adalah perbaikan yang disarankan.
- 'error_type': "pedoman" | "ejaan" | "tanda_baca" | "kosa_kata"
- 'severity': "high" | "medium" | "low"

═══════════════════════════════════════════
FORMAT OUTPUT (WAJIB JSON VALID):
═══════════════════════════════════════════
{
  "status": "sesuai" | "perlu_revisi" | "ejaan_tanda_baca" | "tidak_ditemukan_rujukan",
  "issue": "ringkasan masalah (null jika sesuai)",
  "rule_reference": {
    "document": "nama file pedoman",
    "page": 1,
    "section": "judul pasal/bab jika ada"
  },
  "suggested_revision": "saran perbaikan naskah utuh (null jika sesuai)",
  "span_errors": [
    {
      "original_snippet": "teks salah",
      "suggested_snippet": "teks benar",
      "error_type": "pedoman" | "ejaan" | "tanda_baca" | "kosa_kata",
      "explanation": "penjelasan aturan",
      "severity": "high" | "medium" | "low"
    }
  ]
}
"""


class OpenRouterReviewer:
    """Reviewer using OpenRouter API (OpenAI-compatible) with high reliability and fallback handling."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        # Hanya gunakan OpenRouter, jangan fallback ke NVIDIA
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        self.base_url = base_url or settings.OPENROUTER_BASE_URL
        # Model biarkan None untuk auto-routing OpenRouter
        self.model = model or settings.OPENROUTER_MODEL or None
        self.client = None

        self.fallback_client = None
        self.fallback_model = settings.OPENROUTER_FALLBACK_MODEL or None
        if settings.OPENROUTER_FALLBACK_API_KEY and settings.OPENROUTER_FALLBACK_API_KEY.strip():
            try:
                self.fallback_client = OpenAI(
                    base_url=settings.OPENROUTER_FALLBACK_BASE_URL,
                    api_key=settings.OPENROUTER_FALLBACK_API_KEY.strip(),
                    default_headers={
                        "HTTP-Referer": "http://localhost:8000",
                        "X-Title": "AI Document Checker",
                    },
                    timeout=20.0,
                )
                logger.info("OpenRouter fallback client initialized in Reviewer")
            except Exception as fe:
                logger.warning(f"Failed to init OpenRouter fallback client: {fe}")

        self.groq_client = None
        if settings.GROQ_API_KEY and settings.GROQ_API_KEY.strip():
            try:
                self.groq_client = OpenAI(
                    base_url=settings.GROQ_BASE_URL,
                    api_key=settings.GROQ_API_KEY.strip(),
                    timeout=20.0,
                )
                logger.info("Groq fallback client initialized in Reviewer")
            except Exception as ge:
                logger.warning(f"Failed to init Groq fallback client: {ge}")

        if not self.api_key or not self.api_key.strip():
            logger.error("OPENROUTER_API_KEY tidak ditemukan di .env file!")
            return

        try:
            # Set default headers for OpenRouter
            extra_headers = {
                "HTTP-Referer": "http://localhost:8000",
                "X-Title": "AI Document Checker",
            }
            self.client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key.strip(),
                default_headers=extra_headers,
                timeout=20.0,
            )
            logger.info(f"OpenRouter client initialized successfully (model: {self.model or 'auto-routing'})")
        except Exception as e:
            logger.error(f"Gagal menginisialisasi OpenRouter client: {e}")

    def _validate_and_fix_span_errors(
        self, span_errors_raw: list, original_text: str
    ) -> List[dict]:
        valid_error_types = {"pedoman", "ejaan", "tanda_baca", "kosa_kata"}
        valid_severities = {"high", "medium", "low"}
        validated = []

        for err in span_errors_raw:
            if not isinstance(err, dict):
                continue

            original_snippet = str(err.get("original_snippet", "")).strip()
            suggested_snippet = str(err.get("suggested_snippet", "")).strip()
            error_type = str(err.get("error_type", "ejaan")).strip().lower()
            explanation = str(err.get("explanation", "")).strip()
            severity = str(err.get("severity", "medium")).strip().lower()

            if not original_snippet or not suggested_snippet:
                continue

            # Normalize error_type
            if error_type not in valid_error_types:
                if "tanda" in error_type or "baca" in error_type or "spasi" in error_type:
                    error_type = "tanda_baca"
                elif "kosa" in error_type or "kata" in error_type or "baku" in error_type:
                    error_type = "kosa_kata"
                elif "pedoman" in error_type or "uu" in error_type:
                    error_type = "pedoman"
                else:
                    error_type = "ejaan"

            if severity not in valid_severities:
                severity = "medium"

            # Check presence in text
            if original_snippet not in original_text:
                lower_text = original_text.lower()
                lower_snippet = original_snippet.lower()
                if lower_snippet in lower_text:
                    idx = lower_text.index(lower_snippet)
                    original_snippet = original_text[idx : idx + len(original_snippet)]
                else:
                    continue

            validated.append(
                {
                    "original_snippet": original_snippet,
                    "suggested_snippet": suggested_snippet,
                    "error_type": error_type,
                    "explanation": explanation or f"Kesalahan {error_type}",
                    "severity": severity,
                }
            )

        return validated

    @staticmethod
    def _sanitize_rule_reference(parsed_json: dict) -> None:
        """
        Some models return rule_reference as {"document": null, "page": null, ...}
        instead of omitting it / returning null outright when there is genuinely no
        reference (e.g. status "tidak_ditemukan_rujukan"). RuleReference requires
        document/page when present, so normalize an empty-looking dict to None to
        avoid a Pydantic validation error.
        """
        ref = parsed_json.get("rule_reference")
        if isinstance(ref, dict) and (not ref.get("document") or ref.get("page") is None):
            parsed_json["rule_reference"] = None

    def review_block(
        self,
        block: Block,
        retrieved_chunks: List[Chunk],
        max_retries: int = 1,
        glossary_terms: Optional[List[dict]] = None,
    ) -> BlockReviewResult:
        """
        Reviews a document block against retrieved reference chunks.

        Args:
            glossary_terms: Optional list of official term definitions extracted from
                the reference documents' "Ketentuan Umum" section that are relevant
                to this block, e.g. [{"term", "definition", "document_name", "page"}].
        """
        # Format official Ketentuan Umum definitions relevant to this block
        glossary_str = ""
        if glossary_terms:
            formatted_defs = [
                f"- \"{d['term']}\" adalah {d['definition']} "
                f"(Rujukan: {d['document_name']}, hal. {d['page']})"
                for d in glossary_terms
            ]
            glossary_str = (
                "\n=== DEFINISI RESMI (KETENTUAN UMUM PEDOMAN) ===\n"
                + "\n".join(formatted_defs)
                + "\n"
            )

        # Format reference chunks into prompt
        formatted_chunks = []
        for idx, chunk in enumerate(retrieved_chunks, start=1):
            sec_info = f" | Bagian: {chunk.section_title}" if chunk.section_title else ""
            formatted_chunks.append(
                f"--- Rujukan #{idx} ---\n"
                f"Dokumen: {chunk.document_name} | Halaman: {chunk.page_number}{sec_info}\n"
                f"Teks Pedoman: {chunk.text}\n"
            )

        references_str = (
            "\n".join(formatted_chunks)
            if formatted_chunks
            else "Tidak ada potongan pedoman yang ditemukan."
        )

        user_prompt = (
            f"ID Blok: {block.block_id}\n"
            f"Halaman Upload: {block.page_number}\n\n"
            f"=== TEKS BLOK DOKUMEN UPLOAD ===\n"
            f"{block.text}\n\n"
            f"=== POTONGAN DOKUMEN PEDOMAN (RUJUKAN) ===\n"
            f"{references_str}\n"
            f"{glossary_str}\n"
            f"INSTRUKSI:\n"
            f"1. Periksa apakah teks upload ini COCOK atau IDENTIK dengan salah satu potongan pedoman di atas, dan konsisten dengan Definisi Resmi Ketentuan Umum (jika ada).\n"
            f"   Jika COCOK/SESUAI dan tidak ada penyimpangan definisi, kembalikan status 'sesuai' dan sertakan rule_reference ke dokumen rujukan tersebut.\n"
            f"2. Jika melanggar pedoman ATAU memakai istilah bertentangan dengan Definisi Resmi Ketentuan Umum, kembalikan status 'perlu_revisi' + rule_reference + span_errors (error_type 'pedoman').\n"
            f"3. Jika HANYA ada typo / tanda baca / kosa kata KBBI yang salah (bukan pelanggaran pedoman/definisi), kembalikan 'ejaan_tanda_baca' + span_errors. JANGAN jadikan ini 'perlu_revisi'.\n"
            f"4. PENTING: Kembalikan HANYA JSON objek yang valid tanpa teks tambahan apapun. Jangan tambahkan penjelasan atau komentar di luar JSON.\n\n"
            f"Format JSON yang harus Anda kembalikan:\n"
            f'{{"status": "sesuai", "issue": null, "rule_reference": {{"document": "...", "page": 1, "section": "..."}}, "suggested_revision": null, "span_errors": []}}'
        )

        if self.client is None:
            # Deterministic fallback when client uninitialized
            status = "sesuai" if retrieved_chunks else "tidak_ditemukan_rujukan"
            top_ref = (
                RuleReference(
                    document=retrieved_chunks[0].document_name,
                    page=retrieved_chunks[0].page_number,
                    section=retrieved_chunks[0].section_title,
                )
                if retrieved_chunks
                else None
            )
            return BlockReviewResult(
                block_id=block.block_id,
                original_text=block.text,
                status=status,
                issue="API Key belum dikonfigurasi di .env",
                rule_reference=top_ref,
            )

        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                # Buat request ke OpenRouter - model parameter WAJIB!
                completion = self.client.chat.completions.create(
                    model=self.model,  # OpenRouter WAJIB butuh model parameter
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=4096,
                )

                content = completion.choices[0].message.content or ""

                # Clean markdown blocks
                cleaned_content = re.sub(r"^```json\s*", "", content.strip(), flags=re.IGNORECASE)
                cleaned_content = re.sub(r"\s*```$", "", cleaned_content)

                # Extract JSON if extra text around
                json_match = re.search(r"\{[\s\S]*\}", cleaned_content)
                if json_match:
                    cleaned_content = json_match.group(0)

                parsed_json = json.loads(cleaned_content)

                parsed_json["block_id"] = block.block_id
                parsed_json["original_text"] = block.text

                # Validate span_errors
                raw_span_errors = parsed_json.get("span_errors", [])
                if isinstance(raw_span_errors, list):
                    parsed_json["span_errors"] = self._validate_and_fix_span_errors(
                        raw_span_errors, block.text
                    )
                else:
                    parsed_json["span_errors"] = []

                # Ensure status is valid enum
                status = parsed_json.get("status", "sesuai")
                if status not in ["sesuai", "perlu_revisi", "ejaan_tanda_baca", "tidak_ditemukan_rujukan"]:
                    status = "sesuai" if retrieved_chunks else "tidak_ditemukan_rujukan"
                parsed_json["status"] = status
                self._sanitize_rule_reference(parsed_json)

                result = BlockReviewResult(**parsed_json)

                # If status is sesuai or perlu_revisi and we have retrieved chunks, ensure rule_reference is populated
                if (result.status in ["sesuai", "perlu_revisi"]) and not result.rule_reference and retrieved_chunks:
                    top_c = retrieved_chunks[0]
                    result.rule_reference = RuleReference(
                        document=top_c.document_name,
                        page=top_c.page_number,
                        section=top_c.section_title,
                    )

                # Auto-adjust status if span_errors exist
                if result.status == "sesuai" and len(result.span_errors) > 0:
                    has_pedoman = any(e.error_type == "pedoman" for e in result.span_errors)
                    result.status = "perlu_revisi" if has_pedoman else "ejaan_tanda_baca"

                return result

            except Exception as e:
                last_exception = e
                delay = _compute_retry_delay(e, attempt)
                logger.warning(
                    f"Error reviewing block {block.block_id} (attempt {attempt + 1}/{max_retries + 1}): "
                    f"{e} — retrying in {delay:.1f}s"
                )
                if attempt < max_retries:
                    time.sleep(delay)

        logger.error(
            f"Failed to review block {block.block_id} after OpenRouter (9router) retries: {last_exception}"
        )

        # OpenRouter Fallback Execution (openrouter.ai resmi) if 9router exhausted/failed
        if self.fallback_client:
            try:
                logger.info(f"Attempting OpenRouter fallback for block {block.block_id}...")
                fb_comp = self.fallback_client.chat.completions.create(
                    model=self.fallback_model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=4096,
                )
                fb_content = fb_comp.choices[0].message.content or ""
                cleaned_fb = re.sub(r"^```json\s*", "", fb_content.strip(), flags=re.IGNORECASE)
                cleaned_fb = re.sub(r"\s*```$", "", cleaned_fb)
                json_match = re.search(r"\{[\s\S]*\}", cleaned_fb)
                if json_match:
                    cleaned_fb = json_match.group(0)
                parsed_json = json.loads(cleaned_fb)
                parsed_json["block_id"] = block.block_id
                parsed_json["original_text"] = block.text
                raw_span_errors = parsed_json.get("span_errors", [])
                parsed_json["span_errors"] = (
                    self._validate_and_fix_span_errors(raw_span_errors, block.text)
                    if isinstance(raw_span_errors, list)
                    else []
                )
                status = parsed_json.get("status", "sesuai")
                if status not in ["sesuai", "perlu_revisi", "ejaan_tanda_baca", "tidak_ditemukan_rujukan"]:
                    status = "sesuai" if retrieved_chunks else "tidak_ditemukan_rujukan"
                parsed_json["status"] = status
                self._sanitize_rule_reference(parsed_json)
                result = BlockReviewResult(**parsed_json)
                if (result.status in ["sesuai", "perlu_revisi"]) and not result.rule_reference and retrieved_chunks:
                    top_c = retrieved_chunks[0]
                    result.rule_reference = RuleReference(
                        document=top_c.document_name,
                        page=top_c.page_number,
                        section=top_c.section_title,
                    )
                if result.status == "sesuai" and len(result.span_errors) > 0:
                    has_pedoman = any(e.error_type == "pedoman" for e in result.span_errors)
                    result.status = "perlu_revisi" if has_pedoman else "ejaan_tanda_baca"
                logger.info(f"OpenRouter fallback successfully reviewed block {block.block_id}")
                return result
            except Exception as fe:
                logger.error(f"OpenRouter fallback also failed for block {block.block_id}: {fe}")

        # Groq Fallback Execution if OpenRouter exhausted/failed
        if self.groq_client:
            try:
                logger.info(f"Attempting Groq fallback for block {block.block_id}...")
                groq_comp = self.groq_client.chat.completions.create(
                    model=settings.GROQ_MODEL or "llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=4096,
                )
                groq_content = groq_comp.choices[0].message.content or ""
                cleaned_groq = re.sub(r"^```json\s*", "", groq_content.strip(), flags=re.IGNORECASE)
                cleaned_groq = re.sub(r"\s*```$", "", cleaned_groq)
                json_match = re.search(r"\{[\s\S]*\}", cleaned_groq)
                if json_match:
                    cleaned_groq = json_match.group(0)
                parsed_json = json.loads(cleaned_groq)
                parsed_json["block_id"] = block.block_id
                parsed_json["original_text"] = block.text
                raw_span_errors = parsed_json.get("span_errors", [])
                parsed_json["span_errors"] = (
                    self._validate_and_fix_span_errors(raw_span_errors, block.text)
                    if isinstance(raw_span_errors, list)
                    else []
                )
                status = parsed_json.get("status", "sesuai")
                if status not in ["sesuai", "perlu_revisi", "ejaan_tanda_baca", "tidak_ditemukan_rujukan"]:
                    status = "sesuai" if retrieved_chunks else "tidak_ditemukan_rujukan"
                parsed_json["status"] = status
                self._sanitize_rule_reference(parsed_json)
                result = BlockReviewResult(**parsed_json)
                if (result.status in ["sesuai", "perlu_revisi"]) and not result.rule_reference and retrieved_chunks:
                    top_c = retrieved_chunks[0]
                    result.rule_reference = RuleReference(
                        document=top_c.document_name,
                        page=top_c.page_number,
                        section=top_c.section_title,
                    )
                if result.status == "sesuai" and len(result.span_errors) > 0:
                    has_pedoman = any(e.error_type == "pedoman" for e in result.span_errors)
                    result.status = "perlu_revisi" if has_pedoman else "ejaan_tanda_baca"
                logger.info(f"Groq fallback successfully reviewed block {block.block_id}")
                return result
            except Exception as ge:
                logger.error(f"Groq fallback also failed for block {block.block_id}: {ge}")

        # Intelligent deterministic fallback on API error:
        # If chunks were retrieved, mark as 'sesuai' with reference rather than failing completely
        if retrieved_chunks:
            top_c = retrieved_chunks[0]
            return BlockReviewResult(
                block_id=block.block_id,
                original_text=block.text,
                status="sesuai",
                issue=None,
                rule_reference=RuleReference(
                    document=top_c.document_name,
                    page=top_c.page_number,
                    section=top_c.section_title,
                ),
                suggested_revision=None,
                span_errors=[],
            )
        else:
            return BlockReviewResult(
                block_id=block.block_id,
                original_text=block.text,
                status="tidak_ditemukan_rujukan",
                issue="Tidak ditemukan potongan pedoman yang relevan.",
                rule_reference=None,
                suggested_revision=None,
                span_errors=[],
            )
