import time
import logging
from typing import List, Dict, Any, Literal
import httpx
from src.config import settings

logger = logging.getLogger(__name__)


class NvidiaClient:
    """Client for calling NVIDIA API endpoints (embeddings & reranking) with retries."""

    def __init__(self):
        self.api_key = settings.NVIDIA_API_KEY
        self.embed_url = settings.NVIDIA_EMBED_URL
        self.embed_model = settings.NVIDIA_EMBED_MODEL
        self.rerank_url = settings.NVIDIA_RERANK_URL
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post_with_retry(
        self, url: str, payload: Dict[str, Any], max_retries: int = 2
    ) -> Dict[str, Any]:
        """Executes HTTP POST request with simple retry logic."""
        if not self.api_key:
            raise ValueError(
                "NVIDIA_API_KEY is not set. Please set it in your .env file."
            )

        last_exception = None

        with httpx.Client(timeout=60.0) as client:
            for attempt in range(max_retries + 1):
                try:
                    response = client.post(url, headers=self.headers, json=payload)
                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code >= 500 or response.status_code == 429:
                        logger.warning(
                            f"NVIDIA API status {response.status_code} (attempt {attempt + 1}/{max_retries + 1}): {response.text}"
                        )
                        time.sleep(2 ** attempt)
                    else:
                        response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code < 500 and exc.response.status_code != 429:
                        raise RuntimeError(
                            f"NVIDIA API client error ({exc.response.status_code}): {exc.response.text}"
                        ) from exc
                    last_exception = exc
                    time.sleep(2 ** attempt)
                except httpx.RequestError as exc:
                    last_exception = exc
                    logger.warning(
                        f"HTTP connection error during NVIDIA call (attempt {attempt + 1}/{max_retries + 1}): {exc}"
                    )
                    time.sleep(2 ** attempt)

        raise RuntimeError(f"NVIDIA API call failed after retries: {last_exception}")

    def embed_texts(
        self, texts: List[str], input_type: Literal["query", "passage"] = "passage"
    ) -> List[List[float]]:
        """
        Embeds a list of texts using NVIDIA embeddings endpoint.

        Args:
            texts: List of text strings to embed.
            input_type: 'query' or 'passage'

        Returns:
            List of embedding vectors (list of floats).
        """
        if not texts:
            return []

        payload = {
            "input": texts,
            "model": self.embed_model,
            "input_type": input_type,
        }

        data = self._post_with_retry(self.embed_url, payload)

        # Standard OpenAI/NVIDIA embeddings response parsing
        if "data" in data:
            # Sort by index if present to maintain order
            sorted_items = sorted(
                data["data"], key=lambda item: item.get("index", 0)
            )
            return [item["embedding"] for item in sorted_items]

        raise ValueError(f"Unexpected embeddings response format from NVIDIA: {data}")

    def rerank(
        self, query: str, passages: List[str], top_n: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Reranks a list of passages for a query using NVIDIA reranking endpoint.

        Args:
            query: Query string
            passages: List of passage strings to rank
            top_n: Number of top results to return

        Returns:
            List of dicts: [{"index": int, "text": str, "score": float}] sorted by relevance score desc.
        """
        if not passages:
            return []

        # NVIDIA rerank payload format
        payload = {
            "model": "nvidia/llama-nemotron-rerank-vl-1b-v2",
            "query": {"text": query},
            "passages": [{"text": p} for p in passages],
        }

        try:
            res = self._post_with_retry(self.rerank_url, payload)
        except Exception as e:
            logger.warning(
                f"Reranking API call failed, falling back to original ordering: {e}"
            )
            # Fallback if rerank fails: return top_n as is
            return [
                {"index": i, "text": p, "score": 1.0 - (i * 0.01)}
                for i, p in enumerate(passages[:top_n])
            ]

        rankings = []
        if "rankings" in res:
            rankings = res["rankings"]
        elif "results" in res:
            rankings = res["results"]
        elif "data" in res:
            rankings = res["data"]
        else:
            logger.warning(
                f"Unrecognized rerank response keys ({list(res.keys())}), falling back."
            )
            return [
                {"index": i, "text": p, "score": 1.0 - (i * 0.01)}
                for i, p in enumerate(passages[:top_n])
            ]

        results = []
        for r in rankings:
            idx = r.get("index", 0)
            score = r.get("logit", r.get("relevance_score", r.get("score", 0.0)))
            if idx < len(passages):
                results.append(
                    {"index": idx, "text": passages[idx], "score": float(score)}
                )

        # Sort descending by score and slice top_n
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_n]
