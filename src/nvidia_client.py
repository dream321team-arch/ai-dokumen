import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class NvidiaClient:
    """
    Placeholder client — embedding & reranking ditangani langsung oleh ChromaDB
    menggunakan model lokal bawaan (tidak memerlukan NVIDIA API eksternal).
    """

    def __init__(self):
        pass

    def rerank_local(
        self, query: str, passages: List[str], top_n: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Reranking berbasis skor kata kunci lokal (tanpa API eksternal).
        Menghitung kecocokan kata antara query dan setiap passage.

        Args:
            query: Query string
            passages: List of passage strings to rank
            top_n: Number of top results to return

        Returns:
            List of dicts: [{"index": int, "text": str, "score": float}] sorted by score desc.
        """
        if not passages:
            return []

        query_words = set(query.lower().split())

        results = []
        for i, passage in enumerate(passages):
            passage_words = set(passage.lower().split())
            # Jaccard similarity score
            intersection = len(query_words & passage_words)
            union = len(query_words | passage_words)
            score = intersection / union if union > 0 else 0.0
            results.append({"index": i, "text": passage, "score": score})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_n]
