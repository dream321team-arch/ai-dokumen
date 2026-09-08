import os
import shutil
import json
import time
import uuid
import threading
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.config import settings
from src.pipeline import index_pedoman, check_document
from src.document_extractor import SUPPORTED_EXTENSIONS, extract_document_pages
from src.chunker import split_into_blocks
from src.vector_store import VectorStore
from src.docx_exporter import export_clean_docx, export_track_changes_docx, export_audit_report_docx
from src.doc_assistant import DocumentAssistant
from src.schemas import CheckReport
from src.history_store import save_check_history, list_check_history, get_check_history_by_id
from src.job_store import create_job, update_job_progress, complete_job, fail_job, get_job

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Document Checker Web UI",
    description="Interface web interaktif untuk pemeriksaan kepatuhan dan kebahasaan dokumen.",
    version="2.0.0",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directories
rules_dir = Path("./rules")
input_dir = Path("./input")
output_dir = Path("./output")
static_dir = Path(__file__).resolve().parent / "static"

rules_dir.mkdir(parents=True, exist_ok=True)
input_dir.mkdir(parents=True, exist_ok=True)
output_dir.mkdir(parents=True, exist_ok=True)
static_dir.mkdir(parents=True, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# OpenRouter model yang digunakan
active_runtime_model = settings.OPENROUTER_MODEL

# Status job proses check disimpan di Supabase (bukan dict in-memory) supaya
# tahan restart server (mis. instance Render free-tier yang bisa restart
# mendadak) - progress tidak hilang begitu saja pas frontend lagi polling.
# Nilai ini cuma dipakai sebagai estimasi ETA awal (sebelum ada data progress
# nyata) - berdasarkan observasi run sungguhan, ~20-30 detik/request lebih
# akurat daripada 8 detik yang lama.
DEFAULT_SECONDS_PER_BLOCK = 25.0


def _is_supported_file(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in SUPPORTED_EXTENSIONS


@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_path = static_dir / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="Index HTML page not found")
    return index_path.read_text(encoding="utf-8")


@app.get("/api/status")
async def get_status():
    """Returns system status and configuration."""
    try:
        store = VectorStore()
        total_chunks = store.count()
    except Exception as e:
        logger.warning(f"Could not read vector store count: {e}")
        total_chunks = 0

    guidelines = [
        f.name for f in rules_dir.iterdir()
        if f.is_file() and _is_supported_file(f.name)
    ]

    return {
        "openrouter_configured": bool(settings.OPENROUTER_API_KEY.strip()),
        "active_model": settings.OPENROUTER_MODEL,
        "indexed_chunks": total_chunks,
        "guideline_files_count": len(guidelines),
        "guideline_files": guidelines,
        "supported_formats": list(SUPPORTED_EXTENSIONS),
        "chroma_dir": settings.CHROMA_PERSIST_DIR,
    }


@app.get("/api/guidelines")
async def list_guidelines():
    files = []
    for f in rules_dir.iterdir():
        if f.is_file() and _is_supported_file(f.name):
            files.append({
                "name": f.name,
                "extension": f.suffix.lower(),
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })
    return {"guidelines": files}


@app.post("/api/upload-guidelines")
async def upload_guidelines(files: List[UploadFile] = File(...)):
    uploaded = []
    for file in files:
        if not _is_supported_file(file.filename):
            continue
        dest_path = rules_dir / file.filename
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        uploaded.append(file.filename)
    return {"uploaded": uploaded, "total": len(uploaded)}


@app.post("/api/index-guidelines")
async def trigger_index_guidelines():
    try:
        count = index_pedoman(str(rules_dir))
        return {
            "status": "success",
            "message": f"Berhasil mengindeks {count} chunk pedoman ke database vektor.",
            "indexed_chunks": count,
        }
    except Exception as e:
        logger.error(f"Error during guideline indexing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/check")
async def check_uploaded_document(file: UploadFile = File(...)):
    if not _is_supported_file(file.filename):
        supported_str = ", ".join(SUPPORTED_EXTENSIONS)
        raise HTTPException(
            status_code=400,
            detail=f"Format file '{file.filename}' tidak didukung. Format yang didukung: {supported_str}",
        )

    dest_path = input_dir / file.filename
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        # Gunakan active_runtime_model (auto)
        report = check_document(str(dest_path), model_override=active_runtime_model)

        # Simpan ke riwayat Supabase - setiap run dapat baris sendiri, tidak
        # menimpa hasil sebelumnya walau nama dokumennya sama.
        save_check_history(report)

        return report.model_dump()

    except Exception as e:
        logger.error(f"Error checking document '{file.filename}': {e}")
        raise HTTPException(
            status_code=500, detail=f"Gagal memproses pengujian dokumen: {str(e)}"
        )


@app.post("/api/check/start")
async def start_check_job(
    file: UploadFile = File(...),
    selected_refs: Optional[str] = Form(None),
):
    """
    Starts a document check as a background job and immediately returns a job_id.
    Progress (blocks completed, ETA, live errors) can be polled via
    GET /api/check/status/{job_id}.
    """
    if not _is_supported_file(file.filename):
        supported_str = ", ".join(SUPPORTED_EXTENSIONS)
        raise HTTPException(
            status_code=400,
            detail=f"Format file '{file.filename}' tidak didukung. Format yang didukung: {supported_str}",
        )

    dest_path = input_dir / file.filename
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    refs_list: Optional[List[str]] = None
    if selected_refs:
        try:
            parsed = json.loads(selected_refs)
            if isinstance(parsed, list) and parsed:
                refs_list = [str(x) for x in parsed]
        except Exception:
            logger.warning(f"Gagal parse selected_refs: {selected_refs}")

    # Quick pre-extraction (no LLM calls) just to count blocks for an immediate ETA
    try:
        pages = extract_document_pages(str(dest_path))
        total_blocks = len(split_into_blocks(pages))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gagal membaca dokumen: {e}")

    job_id = uuid.uuid4().hex
    max_workers = min(3, total_blocks) if total_blocks else 1  # samain dengan pipeline.py
    initial_eta = (DEFAULT_SECONDS_PER_BLOCK * total_blocks / max_workers) if total_blocks else 0.0

    create_job(job_id, total_blocks=total_blocks, filename=file.filename, eta_seconds=initial_eta)

    started_at = time.time()

    def _on_progress(info: Dict[str, Any]):
        completed = info["completed"]
        total = info["total"]
        elapsed = time.time() - started_at
        eta = max(0.0, (elapsed / completed) * (total - completed)) if completed > 0 else initial_eta
        error = {"block_id": info["block_id"], "message": info["error"]} if info.get("error") else None
        try:
            update_job_progress(job_id, completed_blocks=completed, eta_seconds=eta, error=error)
        except Exception as e:
            logger.warning(f"Gagal update progress job {job_id} ke Supabase: {e}")

    def _run_job():
        try:
            report = check_document(
                str(dest_path),
                model_override=active_runtime_model,
                selected_references=refs_list,
                progress_callback=_on_progress,
            )
            # Simpan ke riwayat Supabase - setiap run dapat baris sendiri, tidak
            # menimpa hasil sebelumnya walau nama dokumennya sama.
            save_check_history(report)
            complete_job(job_id, report.model_dump(mode="json"))
        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            try:
                fail_job(job_id, str(e))
            except Exception as fe:
                logger.error(f"Gagal menandai job {job_id} sebagai error di Supabase: {fe}")

    threading.Thread(target=_run_job, daemon=True).start()

    return {"job_id": job_id, "total_blocks": total_blocks}


@app.get("/api/check/status/{job_id}")
async def get_check_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    return job


class ExportDocxRequest(BaseModel):
    report: Dict[str, Any]
    mode: str = "clean"
    accepted_block_ids: Optional[List[str]] = None


@app.post("/api/export-docx")
async def export_report_to_docx(req: ExportDocxRequest):
    try:
        report_obj = CheckReport(**req.report)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid report structure: {e}")

    filename_base = Path(report_obj.document_checked).stem

    if req.mode == "track_changes":
        buf = export_track_changes_docx(report_obj)
        filename = f"{filename_base}_revisi_track_changes.docx"
    elif req.mode == "audit_report":
        score = report_obj.compliance_score.overall_score if report_obj.compliance_score else 85.0
        grade = report_obj.compliance_score.grade if report_obj.compliance_score else "B"
        buf = export_audit_report_docx(report_obj, score, grade)
        filename = f"{filename_base}_laporan_audit.docx"
    else:
        buf = export_clean_docx(report_obj, req.accepted_block_ids)
        filename = f"{filename_base}_naskah_bersih.docx"

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class ChatRequest(BaseModel):
    message: str
    chat_history: Optional[List[Dict[str, str]]] = None
    document_context: Optional[str] = None
    block_context: Optional[str] = None
    model: Optional[str] = None


@app.post("/api/assistant/chat")
async def chat_with_assistant(req: ChatRequest):
    assistant = DocumentAssistant()
    response_text = assistant.chat(
        user_message=req.message,
        chat_history=req.chat_history,
        document_context=req.document_context,
        block_context=req.block_context,
    )
    return {"reply": response_text}


@app.get("/api/reports")
async def list_reports():
    """Riwayat SEMUA hasil cek dari Supabase - setiap run tercatat sebagai baris
    terpisah, bukan cuma hasil terakhir per nama dokumen."""
    reports = list_check_history()
    return {"reports": reports}


@app.get("/api/reports/{history_id}")
async def get_report_file(history_id: str):
    row = get_check_history_by_id(history_id)
    if not row:
        raise HTTPException(status_code=404, detail="Laporan tidak ditemukan.")
    return row["full_report"]
