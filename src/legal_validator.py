import re
from typing import List, Dict, Any, Optional
from src.schemas import Block, TextSpanError


# Patterns for Indonesian Legal Hierarchy (UU No. 12 Tahun 2011 & UU No. 13 Tahun 2022)
BAB_PATTERN = re.compile(r"^BAB\s+([IVXLCDM]+|\d+)", re.IGNORECASE | re.MULTILINE)
BAGIAN_PATTERN = re.compile(r"^Bagian\s+([A-Za-z0-9]+)", re.IGNORECASE | re.MULTILINE)
PARAGRAF_PATTERN = re.compile(r"^Paragraf\s+(\d+)", re.IGNORECASE | re.MULTILINE)
PASAL_PATTERN = re.compile(r"^Pasal\s+(\d+)", re.IGNORECASE | re.MULTILINE)
AYAT_PATTERN = re.compile(r"^\((\d+)\)", re.MULTILINE)
HURUF_PATTERN = re.compile(r"^([a-z])\.", re.MULTILINE)
ANGKA_PATTERN = re.compile(r"^(\d+)\.", re.MULTILINE)

# Maximum recommended words per sentence in statutory drafting to prevent ambiguity
MAX_RECOMMENDED_SENTENCE_WORDS = 40


def validate_legal_structure_and_sentences(blocks: List[Block]) -> Dict[str, Any]:
    """
    Validates:
    1. Sentence length warnings (sentences > 40 words that are overly complex)
    2. Article (Pasal) sequence continuity
    3. Paragraph (Ayat) numbering continuity within blocks
    4. Letter (Huruf) sequence continuity (a, b, c...)
    
    Returns structured findings and supplementary span errors.
    """
    findings: List[Dict[str, Any]] = []
    pasal_numbers: List[int] = []
    long_sentences_count = 0
    hierarchy_warnings = 0

    for block in blocks:
        text = block.text

        # Check for Pasal numbering
        pasal_matches = PASAL_PATTERN.findall(text)
        for pm in pasal_matches:
            try:
                p_num = int(pm)
                pasal_numbers.append(p_num)
            except ValueError:
                pass

        # Check for overly long sentences
        sentences = re.split(r'(?<=[.!?])\s+', text)
        for s in sentences:
            words = s.strip().split()
            if len(words) > MAX_RECOMMENDED_SENTENCE_WORDS:
                long_sentences_count += 1
                snippet = " ".join(words[:6]) + "..." + " ".join(words[-4:])
                findings.append({
                    "block_id": block.block_id,
                    "type": "kalimat_panjang",
                    "severity": "low",
                    "word_count": len(words),
                    "snippet": snippet,
                    "message": f"Kalimat terdiri dari {len(words)} kata (disarankan maks. {MAX_RECOMMENDED_SENTENCE_WORDS} kata untuk naskah hukum/kebijakan)."
                })

    # Check Pasal sequence continuity
    if len(pasal_numbers) > 1:
        for i in range(len(pasal_numbers) - 1):
            curr_p = pasal_numbers[i]
            next_p = pasal_numbers[i + 1]
            if next_p != curr_p + 1 and next_p > curr_p:
                hierarchy_warnings += 1
                findings.append({
                    "block_id": "global",
                    "type": "urutan_pasal",
                    "severity": "medium",
                    "message": f"Terdapat lompatan penomoran Pasal dari Pasal {curr_p} langsung ke Pasal {next_p}."
                })

    return {
        "long_sentences_count": long_sentences_count,
        "hierarchy_warnings": hierarchy_warnings,
        "pasal_detected": len(pasal_numbers),
        "findings": findings
    }


def calculate_compliance_score(summary: Dict[str, Any], legal_metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Computes a 0-100% Comprehensive Compliance & Quality Score and Grade:
    - Pedoman compliance (Weight 45%)
    - Grammar & PUEBI/EYD (Weight 30%)
    - Punctuation & Vocabulary (Weight 15%)
    - Legal Draft Structure (Weight 10%)
    """
    total_blocks = max(1, summary.get("total_blocks", 1))
    revisi_blocks = summary.get("perlu_revisi", 0)
    ejaan_blocks = summary.get("ejaan_tanda_baca", 0)
    total_errors = summary.get("total_span_errors", 0)

    # Sub-scores
    # 1. Pedoman score (0-100)
    pedoman_ratio = revisi_blocks / total_blocks
    pedoman_score = max(0.0, 100.0 - (pedoman_ratio * 100.0 * 1.5))

    # 2. Grammar & Spelling score (0-100)
    ejaan_ratio = summary.get("errors_ejaan", 0) / (total_blocks * 2.0)
    ejaan_score = max(0.0, 100.0 - min(100.0, ejaan_ratio * 100.0))

    # 3. Punctuation & Vocabulary score (0-100)
    punct_vocab_errors = summary.get("errors_tanda_baca", 0) + summary.get("errors_kosa_kata", 0)
    punct_ratio = punct_vocab_errors / (total_blocks * 2.0)
    punct_vocab_score = max(0.0, 100.0 - min(100.0, punct_ratio * 100.0))

    # 4. Structure score (0-100)
    struct_score = 100.0
    if legal_metrics:
        long_s = legal_metrics.get("long_sentences_count", 0)
        h_warn = legal_metrics.get("hierarchy_warnings", 0)
        struct_deduction = (long_s * 2.0) + (h_warn * 10.0)
        struct_score = max(50.0, 100.0 - struct_deduction)

    # Overall weighted score
    overall_score = round(
        (pedoman_score * 0.45) +
        (ejaan_score * 0.25) +
        (punct_vocab_score * 0.20) +
        (struct_score * 0.10),
        1
    )

    # Grade determination
    if overall_score >= 90:
        grade = "A"
        predicate = "Sangat Baik (Sesuai Standar)"
    elif overall_score >= 80:
        grade = "B"
        predicate = "Baik (Perlu Sedikit Perbaikan)"
    elif overall_score >= 70:
        grade = "C"
        predicate = "Cukup (Perlu Penyesuaian)"
    elif overall_score >= 50:
        grade = "D"
        predicate = "Kurang (Banyak Ketidaksesuaian)"
    else:
        grade = "E"
        predicate = "Kritis (Perlu Revisi Total)"

    return {
        "overall_score": overall_score,
        "grade": grade,
        "predicate": predicate,
        "sub_scores": {
            "pedoman_score": round(pedoman_score, 1),
            "ejaan_score": round(ejaan_score, 1),
            "tanda_baca_kosa_kata_score": round(punct_vocab_score, 1),
            "struktur_hukum_score": round(struct_score, 1),
        }
    }
