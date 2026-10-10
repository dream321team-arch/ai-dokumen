"""Penyimpanan file asli (.docx) yang diunggah untuk diperiksa.

Dipakai oleh ekspor "Naskah Asli + Komentar": dokumen asli harus tersedia
saat ekspor supaya margin, huruf, dan tata letak tetap sama persis. File
disimpan di Postgres (bytea) dengan kunci hash isi file, sehingga:
  - bertahan walau container dibuat ulang,
  - file yang sama diunggah berkali-kali hanya tersimpan sekali,
  - tidak butuh folder/volume tambahan.
"""
import hashlib
import logging
from typing import Optional, Tuple

from src.history_store import _connect

logger = logging.getLogger(__name__)

_DDL = """
create table if not exists check_source_files (
    sha256 text primary key,
    filename text not null,
    content bytea not null,
    size_bytes integer not null,
    created_at timestamptz not null default now()
)
"""


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_source_file(sha256: str, filename: str, data: bytes) -> bool:
    """Simpan file asli. True bila tersimpan (atau sudah ada), False bila gagal."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL)
                cur.execute(
                    """
                    insert into check_source_files (sha256, filename, content, size_bytes)
                    values (%s, %s, %s, %s)
                    on conflict (sha256) do nothing
                    """,
                    (sha256, filename, data, len(data)),
                )
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"Gagal menyimpan file asli ({filename}): {e}")
        return False


def get_source_file(sha256: str) -> Optional[Tuple[str, bytes]]:
    """Ambil (nama_file, isi) berdasarkan hash, atau None bila tidak ada."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL)
                cur.execute(
                    "select filename, content from check_source_files where sha256 = %s",
                    (sha256,),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            return None
        return row[0], bytes(row[1])
    except Exception as e:
        logger.error(f"Gagal mengambil file asli {sha256[:12]}: {e}")
        return None
