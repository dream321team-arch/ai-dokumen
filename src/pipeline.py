import os
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Callable, Any

from src.config import settings
from src.document_extractor import extract_document_pages, SUPPORTED_EXTENSIONS
from src.chunker import (
    chunk_pedoman,
    split_into_blocks,
    extract_ketentuan_umum_definitions,
    extract_year_from_filename,
)
from src.vector_store import VectorStore
from src.glossary_store import save_ketentuan_umum_definitions, load_ketentuan_umum_definitions
from src.openrouter_reviewer import OpenRouterReviewer
from src.legal_validator import validate_legal_structure_and_sentences, calculate_compliance_score
from src.schemas import (
    CheckReport,
    BlockReviewResult,
    Chunk,
    TextSpanError,
    RuleReference,
    ComplianceScoreInfo,
    LegalStructureMetric,
)

logger = logging.getLogger(__name__)

# Minimum local rerank (Jaccard) score for a pedoman document to be considered
# to genuinely cover a block's topic during cascading recency-based retrieval.
# Below this, the block's topic is treated as "not addressed" in that document
# and the search falls through to the next older pedoman.
MIN_RELEVANCE_SCORE = 0.12


def _order_documents_by_recency(document_names: List[str]) -> List[str]:
    """
    Orders reference document names newest-first based on a year parsed from
    each filename (e.g. "UU 12 Tahun 2011.pdf" -> 2011). Documents whose year
    can't be determined are placed last (lowest priority), since a newer
    regulation on the same topic should take precedence over an older one.
    """
    def sort_key(name: str):
        year = extract_year_from_filename(name)
        if year is None:
            return (1, 0)
        return (0, -year)

    return sorted(document_names, key=sort_key)


def _load_ketentuan_umum_definitions(
    selected_references: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Loads previously extracted Ketentuan Umum definitions from Supabase,
    optionally filtered to a subset of reference document names."""
    return load_ketentuan_umum_definitions(selected_references)


def index_pedoman(rules_dir: str) -> int:
    """
    Indexes all guideline documents (PDF, DOCX, TXT, MD, RTF, etc.) found in rules_dir into ChromaDB.
    """
    rules_path = Path(rules_dir)
    if not rules_path.exists() or not rules_path.is_dir():
        raise ValueError(f"Rules directory not found: {rules_dir}")

    doc_files = [
        f for f in rules_path.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not doc_files:
        logger.warning(f"No guideline documents found in '{rules_dir}'.")
        return 0

    logger.info(f"Found {len(doc_files)} guideline file(s) in '{rules_dir}'.")

    vector_store = VectorStore()
    total_chunks = 0
    all_definitions: List[Dict[str, Any]] = []

    for doc_path in doc_files:
        file_name = doc_path.name
        logger.info(f"Processing pedoman: {file_name}")

        try:
            pages = extract_document_pages(str(doc_path))
            chunks = chunk_pedoman(pages, document_name=file_name)

            if chunks:
                vector_store.index_chunks(chunks)
                total_chunks += len(chunks)

            all_definitions.extend(
                extract_ketentuan_umum_definitions(pages, document_name=file_name)
            )
        except Exception as e:
            logger.error(f"Failed processing guideline '{file_name}': {e}")

    save_ketentuan_umum_definitions(all_definitions)

    logger.info(
        f"Indexing complete! Total {total_chunks} chunk(s) indexed into ChromaDB."
    )
    return total_chunks


def _deduplicate_span_errors(span_errors: List[TextSpanError]) -> List[TextSpanError]:
    seen = set()
    unique = []
    for err in span_errors:
        key = (err.original_snippet, err.error_type)
        if key not in seen:
            seen.add(key)
            unique.append(err)
    return unique


def _post_process_review(review_res: BlockReviewResult, retrieved_chunks: List[Chunk]) -> BlockReviewResult:
    if review_res.span_errors:
        review_res.span_errors = _deduplicate_span_errors(review_res.span_errors)

    severity_order = {"high": 0, "medium": 1, "low": 2}
    if review_res.span_errors:
        original_text = review_res.original_text

        def sort_key(err: TextSpanError):
            sev = severity_order.get(err.severity, 1)
            pos = original_text.find(err.original_snippet)
            if pos == -1:
                pos = 999999
            return (sev, pos)

        review_res.span_errors = sorted(review_res.span_errors, key=sort_key)

    has_errors = len(review_res.span_errors) > 0
    has_pedoman_errors = any(
        e.error_type == "pedoman" for e in review_res.span_errors
    )
    has_language_errors = any(
        e.error_type in ("ejaan", "tanda_baca", "kosa_kata")
        for e in review_res.span_errors
    )

    if retrieved_chunks and review_res.status == "tidak_ditemukan_rujukan":
        if has_language_errors:
            review_res.status = "ejaan_tanda_baca"
        elif has_pedoman_errors:
            review_res.status = "perlu_revisi"
        else:
            review_res.status = "sesuai"

    if not review_res.rule_reference and retrieved_chunks:
        top_c = retrieved_chunks[0]
        review_res.rule_reference = RuleReference(
            document=top_c.document_name,
            page=top_c.page_number,
            section=top_c.section_title,
        )

    if review_res.status == "sesuai" and has_errors:
        if has_pedoman_errors:
            review_res.status = "perlu_revisi"
        else:
            review_res.status = "ejaan_tanda_baca"

    return review_res


def check_document(
    input_doc: str,
    model_override: Optional[str] = None,
    selected_references: Optional[List[str]] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> CheckReport:
    """
    Checks an uploaded document (PDF, DOCX, DOC, TXT, MD, RTF, etc.) for compliance,
    computes legal structure metrics and compliance scores.

    Args:
        input_doc: Path to the document to check.
        model_override: Optional LLM model override.
        selected_references: Optional list of reference guideline document names to
            restrict the check to (default: all indexed guidelines).
        progress_callback: Optional callback invoked with a dict after each block
            finishes processing, e.g. {"completed": int, "total": int, "block_id": str,
            "status": str, "error": Optional[str]}. Used to drive live progress UI.
    """
    input_path = Path(input_doc)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input document file not found: {input_doc}")

    logger.info(f"Starting compliance check for document: '{input_path.name}'...")

    pages = extract_document_pages(str(input_path))
    blocks = split_into_blocks(pages)

    # Step 1: Legal structure & Sentence complexity validation
    raw_legal_metrics = validate_legal_structure_and_sentences(blocks)

    definitions = _load_ketentuan_umum_definitions(selected_references)

    vector_store = VectorStore()
    nvidia_client = vector_store.nvidia_client
    reviewer = OpenRouterReviewer(model=model_override)

    # Urutkan pedoman kandidat dari yang PALING BARU ke paling lama (berdasarkan
    # tahun di nama file), supaya pemeriksaan memprioritaskan aturan terbaru
    # yang membahas topik suatu blok sebelum jatuh ke aturan yang lebih lama.
    all_indexed_docs = vector_store.list_document_names()
    if selected_references:
        candidate_pool = [d for d in selected_references if d in all_indexed_docs] or list(selected_references)
    else:
        candidate_pool = all_indexed_docs
    ordered_reference_docs = _order_documents_by_recency(candidate_pool)

    block_results: List[BlockReviewResult] = []
    summary_counts: Dict[str, int] = {
        "total_blocks": len(blocks),
        "sesuai": 0,
        "perlu_revisi": 0,
        "ejaan_tanda_baca": 0,
        "tidak_ditemukan_rujukan": 0,
    }

    error_type_totals: Dict[str, int] = {
        "pedoman": 0,
        "ejaan": 0,
        "tanda_baca": 0,
        "kosa_kata": 0,
    }

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def process_single_block(block):
        logger.info(f"Processing {block.block_id} (page {block.page_number})...")

        # Cocokkan istilah pada blok ini dengan definisi resmi "Ketentuan Umum"
        # pedoman (substring match, dibatasi agar prompt tetap ringkas & relevan).
        block_text_lower = block.text.lower()
        matched_terms = [
            d for d in definitions if d["term"].lower() in block_text_lower
        ]
        matched_terms.sort(key=lambda d: len(d["term"]), reverse=True)
        glossary_terms = matched_terms[:8]

        # Pencarian bertingkat (cascading) per-dokumen, dari pedoman PALING BARU
        # ke paling lama: begitu satu dokumen dianggap cukup relevan (skor
        # rerank Jaccard >= MIN_RELEVANCE_SCORE), berhenti di situ dan JANGAN
        # campur dengan pedoman yang lebih lama. Kalau tidak ada satupun yang
        # cukup relevan, tetap pakai kandidat dengan skor terbaik yang ada.
        reranked_chunks: List[Chunk] = []
        best_fallback_chunks: List[Chunk] = []
        best_fallback_score = -1.0

        for doc_name in ordered_reference_docs:
            doc_chunks = vector_store.query(
                text=block.text,
                top_k=settings.RETRIEVAL_TOP_K,
                document_names=[doc_name],
            )
            if not doc_chunks:
                continue

            try:
                passages = [c.text for c in doc_chunks]
                rerank_res = nvidia_client.rerank_local(
                    query=block.text, passages=passages, top_n=settings.RERANK_TOP_N
                )
            except Exception as e:
                logger.warning(f"Reranking error for '{doc_name}', using top retrieved chunks: {e}")
                rerank_res = [
                    {"index": i, "score": 0.0}
                    for i in range(min(len(doc_chunks), settings.RERANK_TOP_N))
                ]

            doc_reranked = [
                doc_chunks[item["index"]]
                for item in rerank_res
                if 0 <= item.get("index", -1) < len(doc_chunks)
            ]
            doc_top_score = rerank_res[0].get("score", 0.0) if rerank_res else 0.0

            if doc_top_score > best_fallback_score:
                best_fallback_score = doc_top_score
                best_fallback_chunks = doc_reranked

            if doc_top_score >= MIN_RELEVANCE_SCORE:
                reranked_chunks = doc_reranked
                break

        if not reranked_chunks:
            reranked_chunks = best_fallback_chunks

        review_res = reviewer.review_block(
            block=block, retrieved_chunks=reranked_chunks, glossary_terms=glossary_terms
        )
        review_res.page_number = block.page_number

        review_res = _post_process_review(review_res, reranked_chunks)
        return review_res

    # Gunakan multithreading untuk mempercepat pemrosesan. Dibatasi ke 3 sekaligus
    # (bukan 5) supaya tidak gampang nabrak rate limit provider (mis. batas
    # 20 request/menit pada akun OpenRouter baru).
    max_workers = min(3, len(blocks)) if blocks else 1
    block_results_map = {}
    completed_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_block = {
            executor.submit(process_single_block, block): block for block in blocks
        }
        for future in as_completed(future_to_block):
            block = future_to_block[future]
            error_msg = None
            try:
                res = future.result()
                block_results_map[block.block_id] = res
                if res.status == "tidak_ditemukan_rujukan" and res.issue:
                    error_msg = None  # normal outcome, not a processing error
            except Exception as exc:
                logger.error(f"Block {block.block_id} generated an exception: {exc}")
                error_msg = str(exc)

            completed_count += 1
            if progress_callback:
                try:
                    progress_callback(
                        {
                            "completed": completed_count,
                            "total": len(blocks),
                            "block_id": block.block_id,
                            "status": block_results_map.get(block.block_id).status
                            if block.block_id in block_results_map
                            else "gagal",
                            "error": error_msg,
                        }
                    )
                except Exception as cb_exc:
                    logger.warning(f"progress_callback raised an exception: {cb_exc}")

    # Susun kembali sesuai urutan blok asli
    for block in blocks:
        if block.block_id in block_results_map:
            review_res = block_results_map[block.block_id]
        else:
            review_res = BlockReviewResult(
                block_id=block.block_id,
                page_number=block.page_number,
                original_text=block.text,
                status="tidak_ditemukan_rujukan",
                issue="Gagal memproses blok.",
            )

        block_results.append(review_res)

        status = review_res.status
        if status in summary_counts:
            summary_counts[status] += 1
        else:
            summary_counts["tidak_ditemukan_rujukan"] += 1

        for span_err in review_res.span_errors:
            if span_err.error_type in error_type_totals:
                error_type_totals[span_err.error_type] += 1

    summary_counts["total_span_errors"] = sum(error_type_totals.values())
    summary_counts["errors_pedoman"] = error_type_totals["pedoman"]
    summary_counts["errors_ejaan"] = error_type_totals["ejaan"]
    summary_counts["errors_tanda_baca"] = error_type_totals["tanda_baca"]
    summary_counts["errors_kosa_kata"] = error_type_totals["kosa_kata"]

    # Calculate compliance score & grade
    score_data = calculate_compliance_score(summary_counts, raw_legal_metrics)
    compliance_info = ComplianceScoreInfo(**score_data)
    legal_metrics_info = LegalStructureMetric(**raw_legal_metrics)

    report = CheckReport(
        document_checked=input_path.name,
        checked_at=datetime.now(),
        summary=summary_counts,
        compliance_score=compliance_info,
        legal_metrics=legal_metrics_info,
        blocks=block_results,
    )

    logger.info(
        f"Check complete for '{input_path.name}'. "
        f"Score: {compliance_info.overall_score} (Grade {compliance_info.grade}) | Summary: {summary_counts}"
    )
    return report
