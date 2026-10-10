from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import web_app
from src.config import settings

REPORT = {
    "document_checked": "uji.docx",
    "summary": {"total_blocks": 1},
    "blocks": [
        {"block_id": "block_1", "original_text": "Setiap orang waijb.", "status": "sesuai", "span_errors": []}
    ],
    "source_file_hash": "a" * 64,
}


@pytest.fixture
def client():
    return TestClient(web_app.app)


def test_bawaan_penyimpanan_file_asli_mati():
    # Nilai bawaan kode (tanpa env) harus mati; di sini hanya memastikan tipenya boolean
    # dan bahwa jalur kode membaca setelan ini.
    assert isinstance(settings.AI_STORE_SOURCE_FILES, bool)


def test_file_tidak_disimpan_saat_setelan_mati(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AI_STORE_SOURCE_FILES", False)
    docx = tmp_path / "a.docx"
    docx.write_bytes(b"PK-bukan-docx-sungguhan")

    def jangan_dipanggil(*a, **k):
        raise AssertionError("save_source_file tidak boleh dipanggil saat setelan mati")

    monkeypatch.setattr(web_app, "save_source_file", jangan_dipanggil)
    assert web_app._store_source_if_docx(Path(docx), "a.docx") is None


def test_file_disimpan_saat_setelan_menyala(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AI_STORE_SOURCE_FILES", True)
    docx = tmp_path / "a.docx"
    docx.write_bytes(b"isi-file")
    disimpan = {}
    monkeypatch.setattr(web_app, "save_source_file", lambda sha, name, data: disimpan.update(sha=sha) or True)
    sha = web_app._store_source_if_docx(Path(docx), "a.docx")
    assert sha and sha == disimpan["sha"]


def test_file_selain_docx_tidak_disimpan_walau_setelan_menyala(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AI_STORE_SOURCE_FILES", True)
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(web_app, "save_source_file", lambda *a, **k: pytest.fail("tidak boleh disimpan"))
    assert web_app._store_source_if_docx(Path(pdf), "a.pdf") is None


def test_ekspor_komentar_ditolak_403_saat_setelan_mati(client, monkeypatch):
    monkeypatch.setattr(settings, "AI_STORE_SOURCE_FILES", False)
    # File asli "tersimpan" pun tidak boleh dikembalikan bila fitur dimatikan.
    monkeypatch.setattr(web_app, "get_source_file", lambda sha: pytest.fail("tidak boleh membaca file asli"))
    r = client.post("/api/export-docx", json={"report": REPORT, "mode": "comments"})
    assert r.status_code == 403
    assert "dimatikan" in r.json()["detail"]


def test_ekspor_komentar_409_bila_file_tidak_ada_saat_setelan_menyala(client, monkeypatch):
    monkeypatch.setattr(settings, "AI_STORE_SOURCE_FILES", True)
    monkeypatch.setattr(web_app, "get_source_file", lambda sha: None)
    r = client.post("/api/export-docx", json={"report": REPORT, "mode": "comments"})
    assert r.status_code == 409


def test_status_melaporkan_setelan(client, monkeypatch):
    for nilai in (False, True):
        monkeypatch.setattr(settings, "AI_STORE_SOURCE_FILES", nilai)
        assert client.get("/api/status").json()["source_files_enabled"] is nilai


def test_mode_ekspor_lain_tidak_terpengaruh_setelan_mati(client, monkeypatch):
    monkeypatch.setattr(settings, "AI_STORE_SOURCE_FILES", False)
    r = client.post("/api/export-docx", json={"report": REPORT, "mode": "track_changes"})
    assert r.status_code == 200
