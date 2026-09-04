import pytest
from fastapi.testclient import TestClient
from src.web_app import app

client = TestClient(app)


def test_get_index():
    response = client.get("/")
    assert response.status_code == 200
    assert "AI Document Checker" in response.text


def test_get_api_status():
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "openrouter_configured" in data
    assert "indexed_chunks" in data
    assert "guideline_files" in data
    assert "supported_formats" in data


def test_get_api_guidelines():
    response = client.get("/api/guidelines")
    assert response.status_code == 200
    data = response.json()
    assert "guidelines" in data
    assert isinstance(data["guidelines"], list)


def test_get_api_reports():
    response = client.get("/api/reports")
    assert response.status_code == 200
    data = response.json()
    assert "reports" in data
    assert isinstance(data["reports"], list)


def test_check_unsupported_upload():
    response = client.post(
        "/api/check",
        files={"file": ("test.xyz", b"hello world", "application/octet-stream")}
    )
    assert response.status_code == 400
    assert "tidak didukung" in response.json()["detail"]


def test_check_docx_upload():
    # Test uploading sample docx created during test
    from pathlib import Path
    sample_docx = Path("tests/sample.docx")
    if sample_docx.exists():
        with open(sample_docx, "rb") as f:
            response = client.post(
                "/api/check",
                files={"file": ("sample.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
            )
            assert response.status_code == 200
            data = response.json()
            assert "document_checked" in data
            assert data["document_checked"] == "sample.docx"
            assert "summary" in data
            assert "blocks" in data
