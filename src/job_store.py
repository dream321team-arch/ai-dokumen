import json
import logging
from typing import Dict, Any, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from src.config import settings

logger = logging.getLogger(__name__)


def _connect():
    if not settings.SUPABASE_DB_URL:
        raise ValueError("SUPABASE_DB_URL belum diisi di .env - tidak bisa menyimpan status job.")
    # connect_timeout mencegah nge-hang tanpa batas kalau koneksi ke Supabase
    # lambat/putus - lebih baik gagal cepat & jelas daripada diam selamanya.
    return psycopg2.connect(settings.SUPABASE_DB_URL, connect_timeout=10)


def create_job(job_id: str, total_blocks: int, filename: str, eta_seconds: float) -> None:
    """
    Registers a new background check job in Supabase instead of an in-memory dict,
    so its progress survives a server restart (e.g. Render free-tier instances
    restarting mid-job) - the frontend can keep polling status/{job_id} and pick
    up where it left off instead of hitting "Job status tidak ditemukan".
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into check_jobs (id, status, completed_blocks, total_blocks, eta_seconds, filename)
                values (%s, 'running', 0, %s, %s, %s)
                """,
                (job_id, total_blocks, eta_seconds, filename),
            )
        conn.commit()


def update_job_progress(
    job_id: str,
    completed_blocks: int,
    eta_seconds: float,
    error: Optional[Dict[str, Any]] = None,
) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            if error:
                cur.execute(
                    """
                    update check_jobs
                    set completed_blocks = %s, eta_seconds = %s,
                        errors = errors || %s::jsonb, updated_at = now()
                    where id = %s
                    """,
                    (completed_blocks, eta_seconds, json.dumps([error]), job_id),
                )
            else:
                cur.execute(
                    """
                    update check_jobs
                    set completed_blocks = %s, eta_seconds = %s, updated_at = now()
                    where id = %s
                    """,
                    (completed_blocks, eta_seconds, job_id),
                )
        conn.commit()


def complete_job(job_id: str, report: Dict[str, Any]) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update check_jobs
                set status = 'done', eta_seconds = 0, report = %s::jsonb, updated_at = now()
                where id = %s
                """,
                (json.dumps(report), job_id),
            )
        conn.commit()


def fail_job(job_id: str, error_message: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update check_jobs
                set status = 'error', error_message = %s, updated_at = now()
                where id = %s
                """,
                (error_message, job_id),
            )
        conn.commit()


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    select status, completed_blocks, total_blocks, eta_seconds,
                           errors, report, error_message, filename
                    from check_jobs where id = %s
                    """,
                    (job_id,),
                )
                row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Gagal mengambil status job dari Supabase: {e}")
        return None
