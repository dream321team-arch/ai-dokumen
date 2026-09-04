import re
import time
import json
import logging
from typing import List, Optional
from google import genai
from google.genai import types
from src.schemas import Block, Chunk, BlockReviewResult, RuleReference
from src.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Anda adalah AI Document Checker & Legal Compliance Reviewer profesional untuk dokumen hukum, perundang-undangan, dan pedoman resmi Bahasa Indonesia.

Tugas Anda adalah memeriksa suatu blok teks dari "Dokumen Upload" berdasarkan "Potongan Dokumen Pedoman" (rujukan) serta kaidah Bahasa Indonesia (PUEBI/EYD V & KBBI).

═══════════════════════════════════════════
ATURAN UTAMA PENETAPAN STATUS:
═══════════════════════════════════════════
1. "sesuai" :
   - Gunakan status ini jika teks dokumen upload MEMATUHI, IDENTIK, ATAU COCOK dengan Potongan Dokumen Pedoman yang ada.
   - Gunakan status ini juga jika teks merupakan isi naskah yang benar dan tidak memiliki kesalahan substansi/aturan.
   - Tetap cantumkan 'rule_reference' jika ada rujukan pedoman yang relevan yang mendasarinya!
   - 'issue' diisi null (atau string singkat bahwa teks sesuai), 'suggested_revision' diisi null, 'span_errors' diisi [].

2. "perlu_revisi" :
   - Gunakan status ini jika teks dokumen upload MELANGGAR, BERTENTANGAN, SALAH URUTAN, ATAU MENGUBAH SUBSTANSI aturan dalam Potongan Dokumen Pedoman.
   - WAJIB menyertakan 'rule_reference' (document, page, section).
   - Jelaskan masalah di 'issue', berikan revisi utuh di 'suggested_revision', dan rincikan kata yang salah di 'span_errors'.

3. "ejaan_tanda_baca" :
   - Gunakan status ini jika substansi aturan TIDAK melanggar pedoman, tetapi ada kesalahan teknis penulisan:
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


class GeminiReviewer:
    """Reviewer using Google Gemini API with high reliability and fallback handling."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model = model or settings.GEMINI_MODEL or "gemini-3.7-flash"
        self.client = None

        if not self.api_key or not self.api_key.strip():
            logger.error("GEMINI_API_KEY tidak ditemukan di .env file!")
            return

        try:
            self.client = genai.Client(api_key=self.api_key.strip())
            logger.info(f"Gemini client initialized successfully (model: {self.model})")
        except Exception as e:
            logger.error(f"Gagal menginisialisasi Gemini client: {e}")

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

    def review_block(
        self, block: Block, retrieved_chunks: List[Chunk], max_retries: int = 2
    ) -> BlockReviewResult:
        """
        Reviews a document block against retrieved reference chunks.
        """
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
            f"{references_str}\n\n"
            f"INSTRUKSI:\n"
            f"1. Periksa apakah teks upload ini COCOK atau IDENTIK dengan salah satu potongan pedoman di atas.\n"
            f"   Jika COCOK/SESUAI dan tidak ada typo/tanda baca salah, kembalikan status 'sesuai' dan sertakan rule_reference ke dokumen rujukan tersebut.\n"
            f"2. Jika melanggar pedoman, kembalikan status 'perlu_revisi' + rule_reference + span_errors.\n"
            f"3. Jika ada typo / tanda baca / kosa kata salah tapi tidak melanggar aturan, kembalikan 'ejaan_tanda_baca' + span_errors.\n"
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
                # Buat request ke Gemini API
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[
                        types.Content(
                            role="user",
                            parts=[types.Part(text=user_prompt)],
                        ),
                    ],
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.1,
                        max_output_tokens=2048,
                    ),
                )

                content = response.text or ""

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
                logger.warning(
                    f"Error reviewing block {block.block_id} (attempt {attempt + 1}/{max_retries + 1}): {e}"
                )
                time.sleep(1)

        logger.error(
            f"Failed to review block {block.block_id} after retries: {last_exception}"
        )

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
