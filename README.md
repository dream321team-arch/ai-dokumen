# AI Document Checker (Standalone Python)

Aplikasi Python standalone yang berfungsi untuk melakukan **pemeriksaan kepatuhan dokumen (compliance review)** terhadap Dokumen Pedoman (guidelines) menggunakan RAG (Retrieval-Augmented Generation) berbasis NVIDIA API (Embeddings & Reranking) dan Anthropic Claude (Structured Output).

---

## 🛠️ Tech Stack

- **Python 3.11+**
- **Anthropic SDK (`anthropic`)**: Penggunaan model `claude-sonnet-4-6` via structured tool output.
- **NVIDIA API (`httpx`)**:
  - Embedding: `nvidia/llama-nemotron-embed-1b-v2`
  - Reranking: `llama-nemotron-rerank-vl-1b-v2`
- **PyMuPDF (`fitz`)**: Ekstraksi teks berbasis blok/layout dari file PDF.
- **ChromaDB (`chromadb`)**: Vector database lokal (in-process, persistent ke disk).
- **Pydantic**: Skema validasi data & pembentukan JSON report.

---

## 📁 Struktur Folder

```
.
├── .env.example                # Template variabel lingkungan
├── requirements.txt            # Daftar dependency
├── README.md                   # Panduan penggunaan
├── rules/                      # Folder tempat menyimpan PDF Dokumen Pedoman
├── input/                      # Folder tempat menyimpan PDF Dokumen Upload (draf)
├── output/                     # Folder hasil laporan JSON
├── src/
│   ├── __init__.py
│   ├── config.py               # Konfigurasi & load .env
│   ├── pdf_extractor.py        # Ekstraktor teks PDF per halaman & blok
│   ├── chunker.py              # Chunking pedoman & splitting draf upload
│   ├── nvidia_client.py        # Client HTTP API NVIDIA (embeddings & reranking)
│   ├── vector_store.py         # Wrapper ChromaDB (index & search)
│   ├── claude_reviewer.py      # Anthropic SDK tool reviewer
│   ├── schemas.py              # Model data Pydantic
│   ├── pipeline.py             # Orkestrasi indexing & checking
│   └── cli.py                  # Entrypoint CLI
├── tests/
│   └── test_pipeline.py        # Unit test dasar
└── run.py                      # Executable CLI script
```

---

## 🚀 Panduan Setup & Penggunaan

### 1. Instalasi Dependency

```bash
pip install -r requirements.txt
```

### 2. Konfigurasi Environment Variable (`.env`)

Salin file `.env.example` menjadi `.env`:

```bash
cp .env.example .env
```

Buka file `.env` dan isi API Key yang sesuai:

```env
ANTHROPIC_API_KEY=your_anthropic_api_key_here
NVIDIA_API_KEY=your_nvidia_api_key_here
```

---

### 3. Mengindeks Dokumen Pedoman

Letakkan file PDF Dokumen Pedoman (misalnya 3 file PDF pedoman) di dalam folder `./rules/`.
Kemudian jalankan perintah indexing:

```bash
python run.py index-pedoman --rules-dir ./rules
```

ChromaDB akan membuat dan menyimpan vector embeddings secara lokal di `./.chroma_data`.

---

### 4. Memeriksa Dokumen Upload

Letakkan file PDF dokumen yang ingin dicek di folder `./input/` (misal `./input/draf.pdf`).
Jalankan perintah pengujian compliance:

```bash
python run.py check --input ./input/draf.pdf --output ./output/hasil.json
```

---

## 📊 Hasil Output Laporan (JSON)

Hasil pengujian disimpan dalam format JSON terstruktur di `./output/hasil.json`:

```json
{
  "document_checked": "draf.pdf",
  "checked_at": "2026-08-13T10:30:00",
  "summary": {
    "total_blocks": 15,
    "sesuai": 12,
    "perlu_revisi": 2,
    "tidak_ditemukan_rujukan": 1
  },
  "blocks": [
    {
      "block_id": "block_1",
      "original_text": "...",
      "status": "perlu_revisi",
      "issue": "...",
      "rule_reference": {
        "document": "Pedoman_A.pdf",
        "page": 14,
        "section": "BAB II Pasal 3"
      },
      "suggested_revision": "..."
    }
  ]
}
```

---

## 🧪 Pengujian (Unit Test)

Jalankan pengujian unit test dengan pytest:

```bash
pytest tests/
```
