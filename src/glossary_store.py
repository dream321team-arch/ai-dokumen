import logging
from typing import List, Dict, Any, Optional
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from src.config import settings

logger = logging.getLogger(__name__)


def _connect():
    if not settings.SUPABASE_DB_URL:
        raise ValueError("SUPABASE_DB_URL belum diisi di .env - tidak bisa menyimpan glosarium.")
    # connect_timeout mencegah nge-hang tanpa batas kalau koneksi ke Supabase
    # lambat/putus - lebih baik gagal cepat & jelas daripada diam selamanya.
    return psycopg2.connect(settings.SUPABASE_DB_URL, connect_timeout=10)


def save_ketentuan_umum_definitions(definitions: List[Dict[str, Any]]) -> None:
    """
    Replaces the entire `ketentuan_umum` table content with the freshly extracted
    definitions. Called once per full re-indexing run (index_pedoman), so a clean
    replace (rather than per-document upsert) keeps stale/removed-document entries
    from lingering.
    """
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("truncate table ketentuan_umum")
                if definitions:
                    execute_values(
                        cur,
                        "insert into ketentuan_umum (document_name, term, definition, page) values %s",
                        [
                            (
                                d.get("document_name", ""),
                                d.get("term", ""),
                                d.get("definition", ""),
                                d.get("page"),
                            )
                            for d in definitions
                        ],
                    )
            conn.commit()
        logger.info(f"Saved {len(definitions)} Ketentuan Umum definition(s) to Supabase.")
    except Exception as e:
        logger.warning(f"Failed to save Ketentuan Umum definitions to Supabase: {e}")


def load_ketentuan_umum_definitions(
    selected_references: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Loads Ketentuan Umum definitions from Supabase, optionally filtered to a
    subset of reference document names."""
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if selected_references:
                    cur.execute(
                        "select document_name, term, definition, page from ketentuan_umum "
                        "where document_name = any(%s)",
                        (selected_references,),
                    )
                else:
                    cur.execute(
                        "select document_name, term, definition, page from ketentuan_umum"
                    )
                rows = cur.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.warning(f"Failed to load Ketentuan Umum definitions from Supabase: {e}")
        return []
