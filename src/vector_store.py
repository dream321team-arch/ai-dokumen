import logging
from typing import List, Optional, Dict, Any
import chromadb
from src.schemas import Chunk
from src.config import settings
from src.nvidia_client import NvidiaClient

logger = logging.getLogger(__name__)


class VectorStore:
    """Wrapper for local persistent ChromaDB collection storing guideline chunks."""

    def __init__(self, persist_dir: Optional[str] = None):
        self.persist_dir = persist_dir or settings.CHROMA_PERSIST_DIR
        self.client = chromadb.PersistentClient(path=self.persist_dir)
        self.collection = self.client.get_or_create_collection(
            name="pedoman",
            metadata={"hnsw:space": "cosine"},
        )
        self.nvidia_client = NvidiaClient()

    def index_chunks(self, chunks: List[Chunk], batch_size: int = 50) -> None:
        """
        Embeds and stores chunks in ChromaDB in batches.

        Args:
            chunks: List of Chunk objects
            batch_size: Number of chunks per API request batch
        """
        if not chunks:
            logger.warning("No chunks provided to index.")
            return

        logger.info(f"Indexing {len(chunks)} chunks into ChromaDB collection 'pedoman'...")

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            texts = [c.text for c in batch]
            ids = [c.chunk_id for c in batch]
            metadatas = [
                {
                    "document_name": c.document_name,
                    "page_number": c.page_number,
                    "section_title": c.section_title or "",
                }
                for c in batch
            ]

            embeddings = None
            try:
                # Generate embeddings via NVIDIA API if key available
                if settings.NVIDIA_API_KEY:
                    embeddings = self.nvidia_client.embed_texts(texts, input_type="passage")
            except Exception as e:
                logger.warning(f"Gagal menghasilkan embedding via API ({e}), menggunakan fallback indexing.")

            # Store in ChromaDB (ChromaDB can store documents directly even without custom embeddings)
            if embeddings:
                self.collection.upsert(
                    ids=ids,
                    embeddings=embeddings,
                    documents=texts,
                    metadatas=metadatas,
                )
            else:
                self.collection.upsert(
                    ids=ids,
                    documents=texts,
                    metadatas=metadatas,
                )

            logger.info(
                f"Indexed batch {i // batch_size + 1}/{(len(chunks) - 1) // batch_size + 1} ({len(batch)} chunks)"
            )

        logger.info("Indexing completed successfully.")

    def query(self, text: str, top_k: Optional[int] = None) -> List[Chunk]:
        """
        Queries ChromaDB using NVIDIA embedding vector or exact/keyword text fallback.

        Args:
            text: Query text string
            top_k: Number of nearest neighbors to retrieve

        Returns:
            List of matching Chunk objects with metadata.
        """
        k = top_k or settings.RETRIEVAL_TOP_K

        count = self.collection.count()
        if count == 0:
            logger.warning("ChromaDB collection 'pedoman' is empty.")
            return []

        n_results = min(k, count)
        matched_chunks: List[Chunk] = []

        # 1. First attempt: Vector search via NVIDIA embeddings
        if settings.NVIDIA_API_KEY:
            try:
                query_embeddings = self.nvidia_client.embed_texts([text], input_type="query")
                res = self.collection.query(
                    query_embeddings=query_embeddings,
                    n_results=n_results,
                    include=["documents", "metadatas"],
                )
                if res and res.get("documents") and res["documents"][0]:
                    docs = res["documents"][0]
                    ids = res["ids"][0]
                    metas = res["metadatas"][0]

                    for chunk_id, doc_text, meta in zip(ids, docs, metas):
                        sec = meta.get("section_title")
                        matched_chunks.append(
                            Chunk(
                                chunk_id=chunk_id,
                                text=doc_text,
                                document_name=str(meta.get("document_name", "")),
                                page_number=int(meta.get("page_number", 1)),
                                section_title=sec if sec else None,
                            )
                        )
                    return matched_chunks
            except Exception as e:
                logger.warning(f"Vector search embedding failed ({e}), falling back to text matching.")

        # 2. Second attempt / Fallback: Chroma document query (fuzzy/exact keyword match)
        try:
            # Query top results directly by document text matching
            res = self.collection.query(
                query_texts=[text[:1000]], # Chroma internal query
                n_results=n_results,
                include=["documents", "metadatas"],
            )
            if res and res.get("documents") and res["documents"][0]:
                docs = res["documents"][0]
                ids = res["ids"][0]
                metas = res["metadatas"][0]

                for chunk_id, doc_text, meta in zip(ids, docs, metas):
                    sec = meta.get("section_title")
                    matched_chunks.append(
                        Chunk(
                            chunk_id=chunk_id,
                            text=doc_text,
                            document_name=str(meta.get("document_name", "")),
                            page_number=int(meta.get("page_number", 1)),
                            section_title=sec if sec else None,
                        )
                    )
                return matched_chunks
        except Exception as e:
            logger.warning(f"Text query search failed ({e}). Getting top sample chunks.")

        # 3. Last fallback: Retrieve nearest available chunks from collection
        try:
            sample = self.collection.get(limit=n_results, include=["documents", "metadatas"])
            if sample and sample.get("documents"):
                for cid, cdoc, cmeta in zip(sample["ids"], sample["documents"], sample["metadatas"]):
                    matched_chunks.append(
                        Chunk(
                            chunk_id=cid,
                            text=cdoc,
                            document_name=str(cmeta.get("document_name", "")),
                            page_number=int(cmeta.get("page_number", 1)),
                            section_title=cmeta.get("section_title") or None,
                        )
                    )
        except Exception as e:
            logger.error(f"Failed all collection retrieval attempts: {e}")

        return matched_chunks

    def clear(self) -> None:
        """Clears all documents in the collection."""
        self.client.delete_collection("pedoman")
        self.collection = self.client.get_or_create_collection(
            name="pedoman", metadata={"hnsw:space": "cosine"}
        )
        logger.info("Cleared ChromaDB 'pedoman' collection.")
