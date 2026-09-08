import json
import logging
from typing import List, Dict, Any, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from src.config import settings
from src.schemas import CheckReport

logger = logging.getLogger(__name__)


def _connect():
    if not settings.SUPABASE_DB_URL:
        raise ValueError("SUPABASE_DB_URL belum diisi di .env - tidak bisa menyimpan riwayat.")
    # connect_timeout mencegah nge-hang tanpa batas kalau koneksi ke Supabase
    # lambat/putus - lebih baik gagal cepat & jelas daripada diam selamanya.
    return psycopg2.connect(settings.SUPABASE_DB_URL, connect_timeout=10)


def save_check_history(report: CheckReport) -> Optional[str]:
    """
    Saves one compliance-check run to the `check_history` table in Supabase.
    Unlike the old file-based approach, every run gets its own row - re-checking
    the same document name no longer overwrites/loses a previous result.

    Returns the new row's id (as str), or None if saving failed.
    """
    try:
        score = report.compliance_score.overall_score if report.compliance_score else None
        grade = report.compliance_score.grade if report.compliance_score else None
        full_report_json = report.model_dump(mode="json")

        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into check_history (document_name, score, grade, summary, full_report)
                    values (%s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        report.document_checked,
                        score,
                        grade,
                        json.dumps(report.summary),
                        json.dumps(full_report_json),
                    ),
                )
                row_id = cur.fetchone()[0]
            conn.commit()
        return str(row_id)
    except Exception as e:
        logger.error(f"Gagal menyimpan riwayat cek ke Supabase: {e}")
        return None


def list_check_history(limit: int = 100) -> List[Dict[str, Any]]:
    """Returns past check runs (newest first), without the heavy full_report field."""
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    select id, document_name, checked_at, score, grade, summary
                    from check_history
                    order by checked_at desc
                    limit %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Gagal mengambil riwayat cek dari Supabase: {e}")
        return []


def get_check_history_by_id(history_id: str) -> Optional[Dict[str, Any]]:
    """Returns one past check run's full report by its id."""
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "select id, document_name, checked_at, score, grade, full_report "
                    "from check_history where id = %s",
                    (history_id,),
                )
                row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Gagal mengambil detail riwayat cek dari Supabase: {e}")
        return None
