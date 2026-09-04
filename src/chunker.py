import re
import uuid
import logging
from typing import List, Dict, Any, Optional
from src.schemas import Chunk, Block
from src.config import settings

logger = logging.getLogger(__name__)

# Heuristic patterns for section headers in Indonesian reference documents
HEADING_REGEX = re.compile(
    r"^(BAB\s+[IVXLCDM\d]+|PASAL\s+\d+|BAGIAN\s+[A-Z0-9]+|LAMPIRAN\s+[A-Z0-9]+|[A-Z0-9]\.\s+[A-Z0-9])",
    re.IGNORECASE,
)

# Sentence-ending punctuation pattern for Indonesian text
SENTENCE_END_RE = re.compile(r'[.!?;]\s+')

# Maximum block size in words - blocks larger than this will be split at sentence boundaries
MAX_BLOCK_WORDS = 300

# Minimum block size in words - blocks smaller than this may be merged with neighbors
MIN_BLOCK_WORDS = 30


def _detect_section_title(text_snippet: str) -> Optional[str]:
    """Detect section heading in text snippet."""
    lines = [line.strip() for line in text_snippet.split("\n") if line.strip()]
    for line in lines[:3]:  # Check top lines
        if HEADING_REGEX.search(line):
            return line
    return None


def _find_sentence_boundary(text: str, target_pos: int, search_range: int = 200) -> int:
    """
    Find the nearest sentence boundary (after sentence-ending punctuation)
    near target_pos within search_range characters.
    Returns the best split position, preferring positions after sentence-ending punctuation.
    """
    # Search window around target position
    search_start = max(0, target_pos - search_range)
    search_end = min(len(text), target_pos + search_range)
    search_text = text[search_start:search_end]

    best_pos = target_pos  # fallback to original position
    best_distance = search_range + 1

    # Find all sentence boundaries in the search window
    for match in SENTENCE_END_RE.finditer(search_text):
        # Position in original text (end of match = start of next sentence)
        abs_pos = search_start + match.end()
        distance = abs(abs_pos - target_pos)
        if distance < best_distance:
            best_distance = distance
            best_pos = abs_pos

    # Also try splitting at paragraph boundaries (\n\n)
    for match in re.finditer(r'\n\s*\n', search_text):
        abs_pos = search_start + match.end()
        distance = abs(abs_pos - target_pos)
        if distance < best_distance:
            best_distance = distance
            best_pos = abs_pos

    return best_pos


def chunk_pedoman(pages: List[Dict[str, Any]], document_name: str) -> List[Chunk]:
    """
    Chunks reference guideline pages into chunks of approx CHUNK_SIZE_TOKENS
    with CHUNK_OVERLAP_TOKENS overlap. Splits at sentence boundaries to preserve
    punctuation context.

    Args:
        pages: List of page dicts [{"page_number": int, "text": str}]
        document_name: Name of the reference PDF document

    Returns:
        List of Chunk objects.
    """
    chunks: List[Chunk] = []

    # Rough character count estimates: 1 token ~= 4 chars
    chunk_char_size = settings.CHUNK_SIZE_TOKENS * 4
    overlap_char_size = settings.CHUNK_OVERLAP_TOKENS * 4

    current_section = None

    for page in pages:
        page_num = page["page_number"]
        page_text = page["text"]

        if not page_text.strip():
            continue

        # Update active section title if found on page
        heading = _detect_section_title(page_text)
        if heading:
            current_section = heading

        # Perform sentence-boundary-aware sliding window with overlap
        start_idx = 0
        text_length = len(page_text)

        while start_idx < text_length:
            raw_end = min(start_idx + chunk_char_size, text_length)

            # If not at the end of the page, try to split at a sentence boundary
            if raw_end < text_length:
                end_idx = _find_sentence_boundary(page_text, raw_end)
                # Ensure we make forward progress
                if end_idx <= start_idx:
                    end_idx = raw_end
            else:
                end_idx = raw_end

            chunk_str = page_text[start_idx:end_idx].strip()

            if chunk_str:
                chunk_id = f"{document_name}_p{page_num}_{uuid.uuid4().hex[:6]}"
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        text=chunk_str,
                        document_name=document_name,
                        page_number=page_num,
                        section_title=current_section,
                    )
                )

            if end_idx >= text_length:
                break

            step = max(1, chunk_char_size - overlap_char_size)
            # Adjust step to also respect sentence boundaries for overlap start
            next_start = start_idx + step
            if next_start < text_length:
                boundary = _find_sentence_boundary(page_text, next_start, search_range=100)
                if boundary > start_idx:
                    next_start = boundary
            start_idx = next_start

    logger.info(f"Created {len(chunks)} chunks for document '{document_name}'.")
    return chunks


def _split_long_block_at_sentences(text: str, max_words: int = MAX_BLOCK_WORDS) -> List[str]:
    """
    Splits a long text block at sentence boundaries so each resulting piece
    has at most max_words words. Preserves all punctuation.
    """
    words = text.split()
    if len(words) <= max_words:
        return [text]

    # Split text into sentences first
    # Use a pattern that keeps the delimiter attached to the preceding sentence
    sentences = re.split(r'(?<=[.!?;])\s+', text)

    result_blocks: List[str] = []
    current_sentences: List[str] = []
    current_word_count = 0

    for sentence in sentences:
        sentence_words = len(sentence.split())

        if current_word_count + sentence_words > max_words and current_sentences:
            # Flush current block
            result_blocks.append(" ".join(current_sentences))
            current_sentences = [sentence]
            current_word_count = sentence_words
        else:
            current_sentences.append(sentence)
            current_word_count += sentence_words

    # Flush remaining
    if current_sentences:
        result_blocks.append(" ".join(current_sentences))

    return result_blocks


def split_into_blocks(pages: List[Dict[str, Any]]) -> List[Block]:
    """
    Splits uploaded document pages into blocks (paragraphs/sections).
    - Combines short paragraphs (<MIN_BLOCK_WORDS words) with the previous block
      to ensure sufficient context for AI review.
    - Splits overly long blocks (>MAX_BLOCK_WORDS words) at sentence boundaries
      to preserve punctuation integrity.
    - Never cuts in the middle of a sentence, ensuring tanda baca context is preserved.

    Args:
        pages: List of page dicts [{"page_number": int, "text": str}]

    Returns:
        List of Block objects.
    """
    blocks: List[Block] = []

    raw_paragraphs: List[Dict[str, Any]] = []

    # Gather paragraphs per page
    for page in pages:
        page_num = page["page_number"]
        page_text = page["text"]

        # Split by double newline or blank lines
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", page_text) if p.strip()]

        for p in paragraphs:
            raw_paragraphs.append({"text": p, "page_number": page_num})

    # Phase 1: Combine short paragraphs with previous block for context
    combined_blocks: List[Dict[str, Any]] = []

    for item in raw_paragraphs:
        text = item["text"]
        word_count = len(text.split())

        if combined_blocks and word_count < MIN_BLOCK_WORDS:
            # Only merge if resulting block won't exceed max size
            prev_words = len(combined_blocks[-1]["text"].split())
            if prev_words + word_count <= MAX_BLOCK_WORDS:
                combined_blocks[-1]["text"] += "\n\n" + text
            else:
                combined_blocks.append({"text": text, "page_number": item["page_number"]})
        else:
            combined_blocks.append({"text": text, "page_number": item["page_number"]})

    # Phase 2: Split overly long blocks at sentence boundaries
    final_blocks: List[Dict[str, Any]] = []

    for item in combined_blocks:
        text = item["text"]
        word_count = len(text.split())

        if word_count > MAX_BLOCK_WORDS:
            sub_texts = _split_long_block_at_sentences(text, MAX_BLOCK_WORDS)
            for sub_text in sub_texts:
                if sub_text.strip():
                    final_blocks.append({
                        "text": sub_text.strip(),
                        "page_number": item["page_number"],
                    })
        else:
            final_blocks.append(item)

    # Phase 3: Normalize whitespace and convert to Block schemas
    for idx, item in enumerate(final_blocks, start=1):
        # Clean up excessive internal whitespace but preserve intentional formatting
        text = item["text"]
        # Remove trailing whitespace per line but preserve paragraph breaks
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        # Collapse triple+ newlines into double
        text = re.sub(r"\n{3,}", "\n\n", text)

        block_id = f"block_{idx}"
        blocks.append(
            Block(
                block_id=block_id,
                text=text.strip(),
                page_number=item["page_number"],
            )
        )

    logger.info(f"Split uploaded document into {len(blocks)} blocks.")
    return blocks
