import logging
from typing import List, Optional
import psycopg2
from psycopg2.extras import execute_values
from pgvector.psycopg2 import register_vector
from chromadb.utils import embedding_functions
from src.schemas import Chunk
from src.config import settings
from src.nvidia_client import NvidiaClient

logger = logging.getLogger(__name__)

# Model embedding lokal (sama persis yang dulu dipakai ChromaDB secara otomatis:
# all-MiniLM-L6-v2, gratis & offline, 384 dimensi) - dipakai ulang di sini murni
# buat menghasilkan vector, tanpa perlu chromadb sebagai database vector lagi.
_embedding_fn = embedding_functions.DefaultEmbeddingFunction()


class VectorStore:
    """
    Wrapper penyimpanan & pencarian pedoman berbasis Supabase (Postgres + pgvector),
    menggantikan ChromaDB lokal. Embedding tetap dihitung lokal & gratis (model
    bawaan ChromaDB), cuma penyimpanan/pencariannya yang pindah ke Postgres supaya
    persisten walau aplikasi di-deploy ke platform tanpa disk permanen.
    """

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or settings.SUPABASE_DB_URL
        if not self.db_url:
            raise ValueError(
                "SUPABASE_DB_URL belum diisi di .env - tidak bisa terhubung ke database pedoman."
            )
        # connect_timeout membatasi waktu tunggu koneksi awal; keepalives + statement_timeout
        # mencegah koneksi yang "diam-diam mati" (mis. jaringan idle lama saat menunggu LLM
        # antar-blok) membuat query berikutnya nge-hang selamanya tanpa pernah error - ini
        # instance dipakai berulang sepanjang satu proses check_document() yang bisa berjalan
        # puluhan menit, jadi rentan kena kondisi ini kalau tidak dijaga.
        self.conn = psycopg2.connect(
            self.db_url,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
            options="-c statement_timeout=30000",
        )
        self.conn.autocommit = True
        register_vector(self.conn)
        self.nvidia_client = NvidiaClient()

    def _embed(self, texts: List[str]) -> List[List[float]]:
        return _embedding_fn(texts)

    def index_chunks(self, chunks: List[Chunk], batch_size: int = 100) -> None:
        """
        Menyimpan chunks ke tabel `pedoman_chunks` di Supabase, dengan embedding
        dihitung lokal (gratis, tanpa API eksternal).

        Args:
            chunks: List of Chunk objects
            batch_size: Number of chunks per batch
        """
        if not chunks:
            logger.warning("No chunks provided to index.")
            return

        logger.info(f"Indexing {len(chunks)} chunks into Supabase (local embeddings)...")

        with self.conn.cursor() as cur:
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i: i + batch_size]
                texts = [c.text for c in batch]
                embeddings = self._embed(texts)

                rows = [
                    (
                        c.chunk_id,
                        c.document_name,
                        c.page_number,
                        c.section_title,
                        c.text,
                        embeddings[idx],
                    )
                    for idx, c in enumerate(batch)
                ]

                execute_values(
                    cur,
                    """
                    insert into pedoman_chunks (id, document_name, page_number, section_title, content, embedding)
                    values %s
                    on conflict (id) do update set
                        document_name = excluded.document_name,
                        page_number = excluded.page_number,
                        section_title = excluded.section_title,
                        content = excluded.content,
                        embedding = excluded.embedding
                    """,
                    rows,
                )

                logger.info(
                    f"Indexed batch {i // batch_size + 1}/{(len(chunks) - 1) // batch_size + 1} "
                    f"({len(batch)} chunks) [local embedding]"
                )

        logger.info("Indexing completed successfully (Supabase mode).")

    def query(
        self,
        text: Optional[str] = None,
        top_k: Optional[int] = None,
        document_names: Optional[List[str]] = None,
        precomputed_embedding: Optional[List[float]] = None,
    ) -> List[Chunk]:
        """
        Mencari chunks terkait lewat pencarian kemiripan vector (pgvector) di Supabase.

        Args:
            text: Query text string. Boleh diisi None kalau `precomputed_embedding`
                sudah disediakan (menghindari hitung ulang embedding untuk teks yang
                sama di banyak panggilan, mis. pencarian cascading per-dokumen).
            top_k: Number of nearest neighbors to retrieve
            document_names: Optional list of document_name values to restrict the
                search to (e.g. only search selected reference guideline files).
            precomputed_embedding: Vector embedding yang sudah dihitung sebelumnya
                untuk `text` yang sama - hemat komputasi kalau query yang sama mau
                dipakai ulang untuk beberapa pencarian (mis. per-dokumen berbeda).

        Returns:
            List of matching Chunk objects.
        """
        k = top_k or settings.RETRIEVAL_TOP_K
        matched_chunks: List[Chunk] = []

        try:
            query_embedding = precomputed_embedding if precomputed_embedding is not None else self._embed([text])[0]
            with self.conn.cursor() as cur:
                cur.execute(
                    "select id, document_name, page_number, section_title, content "
                    "from match_pedoman_chunks(%s, %s, %s)",
                    (query_embedding, k, document_names),
                )
                rows = cur.fetchall()
                for chunk_id, doc_name, page_number, section_title, content in rows:
                    matched_chunks.append(
                        Chunk(
                            chunk_id=chunk_id,
                            text=content,
                            document_name=doc_name,
                            page_number=page_number,
                            section_title=section_title,
                        )
                    )
        except Exception as e:
            logger.warning(f"Supabase pencarian pedoman gagal: {e}")

        return matched_chunks

    def count(self) -> int:
        """Returns the total number of indexed pedoman chunks."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("select count(*) from pedoman_chunks;")
                return cur.fetchone()[0]
        except Exception as e:
            logger.warning(f"Failed to count pedoman chunks: {e}")
            return 0

    def list_document_names(self) -> List[str]:
        """Returns the distinct document_name values present in the indexed pedoman."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("select distinct document_name from pedoman_chunks order by document_name;")
                return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.warning(f"Failed to list document names: {e}")
            return []

    def clear(self) -> None:
        """Clears all indexed pedoman chunks."""
        with self.conn.cursor() as cur:
            cur.execute("delete from pedoman_chunks;")
        logger.info("Cleared all rows in Supabase 'pedoman_chunks' table.")
