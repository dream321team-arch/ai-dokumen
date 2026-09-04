import os
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

from src.config import settings
from src.document_extractor import extract_document_pages, SUPPORTED_EXTENSIONS
from src.chunker import chunk_pedoman, split_into_blocks
from src.vector_store import VectorStore
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

    for doc_path in doc_files:
        file_name = doc_path.name
        logger.info(f"Processing pedoman: {file_name}")

        try:
            pages = extract_document_pages(str(doc_path))
            chunks = chunk_pedoman(pages, document_name=file_name)

            if chunks:
                vector_store.index_chunks(chunks)
                total_chunks += len(chunks)
        except Exception as e:
            logger.error(f"Failed processing guideline '{file_name}': {e}")

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


def check_document(input_doc: str, model_override: Optional[str] = None) -> CheckReport:
    """
    Checks an uploaded document (PDF, DOCX, DOC, TXT, MD, RTF, etc.) for compliance,
    computes legal structure metrics and compliance scores.
    """
    input_path = Path(input_doc)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input document file not found: {input_doc}")

    logger.info(f"Starting compliance check for document: '{input_path.name}'...")

    pages = extract_document_pages(str(input_path))
    blocks = split_into_blocks(pages)

    # Step 1: Legal structure & Sentence complexity validation
    raw_legal_metrics = validate_legal_structure_and_sentences(blocks)

    vector_store = VectorStore()
    nvidia_client = vector_store.nvidia_client
    reviewer = OpenRouterReviewer(model=model_override)

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

        retrieved_chunks = vector_store.query(
            text=block.text, top_k=settings.RETRIEVAL_TOP_K
        )

        reranked_chunks: List[Chunk] = []
        if retrieved_chunks and settings.NVIDIA_API_KEY:
            try:
                passages = [c.text for c in retrieved_chunks]
                rerank_res = nvidia_client.rerank(
                    query=block.text, passages=passages, top_n=settings.RERANK_TOP_N
                )
                for item in rerank_res:
                    idx = item.get("index", 0)
                    if 0 <= idx < len(retrieved_chunks):
                        reranked_chunks.append(retrieved_chunks[idx])
            except Exception as e:
                logger.warning(f"Reranking error, using top retrieved chunks: {e}")
                reranked_chunks = retrieved_chunks[: settings.RERANK_TOP_N]
        else:
            reranked_chunks = retrieved_chunks[: settings.RERANK_TOP_N]

        review_res = reviewer.review_block(
            block=block, retrieved_chunks=reranked_chunks
        )
        review_res.page_number = block.page_number

        review_res = _post_process_review(review_res, reranked_chunks)
        return review_res

    # Gunakan multithreading untuk mempercepat pemrosesan hingga 5x lebih cepat
    max_workers = min(5, len(blocks)) if blocks else 1
    block_results_map = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_block = {
            executor.submit(process_single_block, block): block for block in blocks
        }
        for future in as_completed(future_to_block):
            block = future_to_block[future]
            try:
                res = future.result()
                block_results_map[block.block_id] = res
            except Exception as exc:
                logger.error(f"Block {block.block_id} generated an exception: {exc}")

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
