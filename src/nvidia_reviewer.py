import re
import time
import json
import logging
from typing import List, Optional
from openai import OpenAI
from src.schemas import Block, Chunk, BlockReviewResult, RuleReference, TextSpanError
from src.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Anda adalah AI Document Checker & Grammar Reviewer profesional untuk dokumen hukum/perundang-undangan Bahasa Indonesia.
Tugas Anda adalah memeriksa suatu blok teks (dari Dokumen Upload) berdasarkan 2 aspek utama secara TELITI dan MENDETAIL:

========================================
ASPEK 1: KEPATUHAN TERHADAP DOKUMEN PEDOMAN/UU
========================================
Bandingkan isi blok teks dengan Potongan Dokumen Pedoman yang diberikan.
- Periksa apakah substansi, istilah, format penomoran, dan struktur kalimat sesuai pedoman.
- Jika teks MELANGGAR atau BERTENTANGAN dengan pedoman, set status "perlu_revisi".
- WAJIB sertakan 'rule_reference' (document, page, section) saat status "perlu_revisi".

========================================
ASPEK 2: TANDA BACA, EJAAN, DAN KOSA KATA (PUEBI/EYD V & KBBI)
========================================
Periksa setiap kata dan tanda baca dalam blok teks secara SATU PER SATU berdasarkan aturan berikut:

[A] TANDA BACA (error_type: "tanda_baca"):
  - Tanda Titik (.)  : Wajib di akhir kalimat berita, di belakang angka/huruf dalam suatu bagan/daftar, pada singkatan (a.n., d/a., dll.).
  - Tanda Koma (,)   : Sebelum kata penghubung (tetapi, melainkan, sedangkan), setelah kata penghubung antarkalimat (oleh karena itu, jadi, dengan demikian), di antara unsur-unsur dalam suatu pemerincian, memisahkan anak kalimat yang mendahului induk kalimat.
  - Tanda Titik Dua (:) : Pada akhir suatu pernyataan lengkap yang diikuti pemerincian.
  - Tanda Titik Koma (;) : Sebagai pengganti kata penghubung untuk memisahkan kalimat setara, memisahkan item-item dalam pemerincian vertikal.
  - Tanda Hubung (-)  : Menyambung suku-suku kata dasar yang terpisah baris, menyambung unsur kata ulang (sayur-mayur), menyambung awalan "ke-" dan "se-" dengan angka (ke-2).
  - Tanda Pisah (--) : Membatasi penyisipan kata/kalimat penjelasan di antara tanda pisah.
  - Tanda Kurung (()) : Mengapit tambahan keterangan/penjelasan, mengapit huruf/angka pemerincian.
  - Tanda Petik ("") : Mengapit istilah khusus, judul, petikan langsung.
  - Spasi            : Tidak boleh ada spasi ganda, spasi sebelum tanda baca titik/koma, atau spasi hilang setelah tanda baca.

[B] EJAAN (error_type: "ejaan"):
  - Penulisan huruf kapital: Awal kalimat, nama diri, gelar, jabatan yang diikuti nama, nama lembaga/badan, akronim nama diri.
  - Penulisan huruf miring: Kata/istilah asing yang belum diserap ke Bahasa Indonesia.
  - Penulisan kata depan: "di" sebagai kata depan dipisah (di atas, di bawah, di antara), "di" sebagai imbuhan digabung (dibuat, dilakukan, ditetapkan).
  - Penulisan partikel: "-lah", "-kah", "-tah" ditulis serangkai; "pun" ditulis terpisah kecuali dalam konjungsi (adapun, walaupun, meskipun, biarpun, kendatipun, maupun, sekalipun, sungguhpun, andaipun).
  - Penulisan angka dan bilangan: Bilangan dalam teks hukum yang dapat ditulis dengan satu/dua kata ditulis dengan huruf; angka digunakan untuk bilangan besar/statistik.
  - Penulisan singkatan dan akronim: sesuai PUEBI (misalnya: UU, PP, Keppres, dll.).
  - Penulisan kata berimbuhan: me- + p/t/k/s menjadi mem/men/meng/meny (memproses bukan memperoses, menyetujui bukan mensetujui).
  - Typo/salah ketik: Huruf tertukar, huruf hilang, huruf ganda yang tidak perlu.

[C] KOSA KATA BAKU (error_type: "kosa_kata"):
  - Gunakan kata baku sesuai KBBI. Contoh kesalahan umum:
    "merubah" seharusnya "mengubah"
    "mentaati" seharusnya "menaati"
    "nasehat" seharusnya "nasihat"
    "aktifitas" seharusnya "aktivitas"
    "sistim" seharusnya "sistem"
    "resiko" seharusnya "risiko"
    "praktek" seharusnya "praktik"
    "ijin" seharusnya "izin"
    "propinsi" seharusnya "provinsi"
    "tehnologi" seharusnya "teknologi"
    "analisa" seharusnya "analisis"
    "metoda" seharusnya "metode"
    "karir" seharusnya "karier"
    "hakekat" seharusnya "hakikat"
    "azas" seharusnya "asas"
    "sekedar" seharusnya "sekadar"
    "mensyaratkan" seharusnya "menyaratkan"
    "diatas" (kata depan) seharusnya "di atas"
    "dimana" seharusnya "yang mana" / konstruksi ulang kalimat
    "daripada" (makna 'dari') seharusnya "dari"
  - Pilihan kata yang tepat sesuai konteks hukum/perundang-undangan.
  - Penggunaan kata serapan asing yang sudah ada padanan bakunya di KBBI.

========================================
ATURAN PENENTUAN STATUS:
========================================
1. "perlu_revisi"             : Melanggar/bertentangan dengan isi pedoman. WAJIB isi 'rule_reference'.
2. "ejaan_tanda_baca"         : Tidak melanggar pedoman, TAPI ada kesalahan tanda baca / ejaan / kosa kata tidak baku.
3. "sesuai"                   : Mematuhi pedoman DAN ejaan, tanda baca, kosa kata semuanya sudah benar.
4. "tidak_ditemukan_rujukan"  : Tidak ada potongan pedoman relevan DAN ejaan/tanda baca/kosa kata sudah benar.

CATATAN PENTING:
- Jika ada GABUNGAN kesalahan pedoman + ejaan/tanda baca, prioritaskan status "perlu_revisi" dan masukkan SEMUA kesalahan ke span_errors.
- Jika hanya ada kesalahan ejaan/tanda baca/kosa kata tanpa pelanggaran pedoman, gunakan status "ejaan_tanda_baca".
- Setiap kesalahan yang ditemukan WAJIB dimasukkan ke array 'span_errors' sebagai item terpisah.
- 'original_snippet' HARUS berupa kutipan PERSIS dari teks asli (case-sensitive, termasuk tanda baca).
- 'suggested_snippet' berisi perbaikan yang benar.
- 'explanation' berisi penjelasan singkat mengapa salah dan aturan apa yang dilanggar.
- Periksa teks secara MENYELURUH, jangan hanya sebagian. Setiap kata dan tanda baca harus dicek.
- Jika tidak ada kesalahan sama sekali, kembalikan span_errors sebagai array kosong [].

========================================
FORMAT RESPONS (JSON VALID):
========================================
{
  "status": "sesuai" | "perlu_revisi" | "ejaan_tanda_baca" | "tidak_ditemukan_rujukan",
  "issue": "ringkasan masalah utama yang ditemukan (null jika sesuai)",
  "rule_reference": {
    "document": "nama file pedoman",
    "page": 1,
    "section": "judul pasal/bab"
  },
  "suggested_revision": "teks blok lengkap yang sudah diperbaiki (null jika sesuai)",
  "span_errors": [
    {
      "original_snippet": "kutipan PERSIS teks yang salah",
      "suggested_snippet": "perbaikan yang benar",
      "error_type": "pedoman" | "ejaan" | "tanda_baca" | "kosa_kata",
      "explanation": "penjelasan mengapa salah dan aturan yang dilanggar",
      "severity": "high" | "medium" | "low"
    }
  ]
}
"""


class NvidiaReviewer:
    """Wrapper for NVIDIA OpenAI-compatible API to review document blocks."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or settings.NVIDIA_API_KEY
        self.base_url = base_url or settings.NVIDIA_BASE_URL
        self.model = model or settings.NVIDIA_LLM_MODEL
        self.client = None

        if self.api_key and self.api_key.strip():
            try:
                self.client = OpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key.strip(),
                )
            except Exception as e:
                logger.warning(f"Gagal menginisialisasi OpenAI/NVIDIA client: {e}")
        else:
            logger.warning("NVIDIA_API_KEY belum diisi di .env file.")

    def _validate_and_fix_span_errors(
        self, span_errors_raw: list, original_text: str
    ) -> List[dict]:
        """
        Validates span_errors from AI response, ensuring original_snippet exists
        in the original text and normalizing error_type values.
        """
        valid_error_types = {"pedoman", "ejaan", "tanda_baca", "kosa_kata"}
        valid_severities = {"high", "medium", "low"}
        validated = []

        for err in span_errors_raw:
            if not isinstance(err, dict):
                continue

            original_snippet = err.get("original_snippet", "").strip()
            suggested_snippet = err.get("suggested_snippet", "").strip()
            error_type = err.get("error_type", "ejaan").strip().lower()
            explanation = err.get("explanation", "").strip()
            severity = err.get("severity", "medium").strip().lower()

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

            # Normalize severity
            if severity not in valid_severities:
                severity = "medium"

            # Verify original_snippet exists in the text (case-sensitive first, then insensitive)
            if original_snippet not in original_text:
                # Try case-insensitive match
                lower_text = original_text.lower()
                lower_snippet = original_snippet.lower()
                if lower_snippet in lower_text:
                    # Find the actual text with correct casing
                    idx = lower_text.index(lower_snippet)
                    original_snippet = original_text[idx : idx + len(original_snippet)]
                else:
                    # Skip if snippet doesn't exist in text at all
                    logger.debug(
                        f"Skipping span_error: '{original_snippet}' not found in block text."
                    )
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
        Reviews a document block against retrieved reference chunks using NVIDIA LLM.

        Args:
            block: The Block object to review
            retrieved_chunks: List of reference Chunk objects
            max_retries: Retry attempts on API/parsing failure

        Returns:
            BlockReviewResult object
        """
        if self.client is None:
            return BlockReviewResult(
                block_id=block.block_id,
                original_text=block.text,
                status="tidak_ditemukan_rujukan",
                issue="NVIDIA_API_KEY belum dikonfigurasi di file .env.",
            )

        # Format reference chunks into prompt
        formatted_chunks = []
        for idx, chunk in enumerate(retrieved_chunks, start=1):
            sec_info = f" | Section: {chunk.section_title}" if chunk.section_title else ""
            formatted_chunks.append(
                f"--- Rujukan #{idx} ---\n"
                f"Dokumen: {chunk.document_name} | Halaman: {chunk.page_number}{sec_info}\n"
                f"Teks: {chunk.text}\n"
            )

        references_str = (
            "\n".join(formatted_chunks)
            if formatted_chunks
            else "Tidak ada potongan pedoman yang relevan ditemukan."
        )

        user_prompt = (
            f"ID Blok: {block.block_id}\n"
            f"Halaman Upload: {block.page_number}\n\n"
            f"=== TEKS BLOK DOKUMEN UPLOAD ===\n"
            f"{block.text}\n\n"
            f"=== POTONGAN DOKUMEN PEDOMAN ===\n"
            f"{references_str}\n\n"
            f"Analisis blok di atas secara MENYELURUH. Periksa SETIAP kata dan tanda baca.\n"
            f"Pastikan 'original_snippet' dikutip PERSIS dari teks asli.\n"
            f"Kembalikan JSON hasil pemeriksaan."
        )

        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.1,
                    top_p=0.9,
                    max_tokens=2048,
                    response_format={"type": "json_object"},
                )

                content = completion.choices[0].message.content or ""

                # Strip potential markdown codefence blocks
                cleaned_content = re.sub(r"^```json\s*", "", content.strip())
                cleaned_content = re.sub(r"\s*```$", "", cleaned_content)

                parsed_json = json.loads(cleaned_content)

                parsed_json["block_id"] = block.block_id
                parsed_json["original_text"] = block.text

                # Validate and fix span_errors before creating result
                raw_span_errors = parsed_json.get("span_errors", [])
                if isinstance(raw_span_errors, list):
                    parsed_json["span_errors"] = self._validate_and_fix_span_errors(
                        raw_span_errors, block.text
                    )
                else:
                    parsed_json["span_errors"] = []

                result = BlockReviewResult(**parsed_json)

                # Check rule_reference requirement for perlu_revisi
                if result.status == "perlu_revisi" and not result.rule_reference:
                    if retrieved_chunks:
                        top_c = retrieved_chunks[0]
                        result.rule_reference = RuleReference(
                            document=top_c.document_name,
                            page=top_c.page_number,
                            section=top_c.section_title,
                        )

                # Auto-correct status if span_errors exist but status says sesuai
                if result.status == "sesuai" and len(result.span_errors) > 0:
                    has_pedoman_error = any(
                        e.error_type == "pedoman" for e in result.span_errors
                    )
                    if has_pedoman_error:
                        result.status = "perlu_revisi"
                    else:
                        result.status = "ejaan_tanda_baca"

                return result

            except Exception as e:
                last_exception = e
                logger.warning(
                    f"Error reviewing block {block.block_id} via NVIDIA API (attempt {attempt + 1}/{max_retries + 1}): {e}"
                )
                time.sleep(1)

        logger.error(
            f"Failed to review block {block.block_id} after retries: {last_exception}"
        )
        return BlockReviewResult(
            block_id=block.block_id,
            original_text=block.text,
            status="tidak_ditemukan_rujukan",
            issue=f"Gagal memproses analisis AI NVIDIA: {str(last_exception)}",
        )
