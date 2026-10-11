import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from working directory
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)


class Settings:
    # OpenRouter API settings (Primary LLM)
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL: str = os.getenv(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")

    # OpenRouter Fallback settings (dipakai otomatis jika OPENROUTER_* primer gagal/error)
    OPENROUTER_FALLBACK_API_KEY: str = os.getenv("OPENROUTER_FALLBACK_API_KEY", "")
    OPENROUTER_FALLBACK_BASE_URL: str = os.getenv(
        "OPENROUTER_FALLBACK_BASE_URL", "https://openrouter.ai/api/v1"
    )
    OPENROUTER_FALLBACK_MODEL: str = os.getenv("OPENROUTER_FALLBACK_MODEL", "openai/gpt-4o")

    # Moonshot AI / Kimi settings
    KIMI_API_KEY: str = os.getenv("KIMI_API_KEY", "")
    KIMI_BASE_URL: str = os.getenv("KIMI_BASE_URL", "https://integrate.api.nvidia.com/v1")
    KIMI_MODEL: str = os.getenv("KIMI_MODEL", "moonshotai/kimi-k3")

    # NVIDIA API settings (Nemotron 3.5 Lightning — LLM Reviewer)
    NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", "")
    NVIDIA_BASE_URL: str = os.getenv(
        "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
    )
    NVIDIA_LLM_MODEL: str = os.getenv(
        "NVIDIA_LLM_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"
    )
    NVIDIA_CHAT_MODEL: str = os.getenv(
        "NVIDIA_CHAT_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"
    )
    NVIDIA_CHAT_API_KEY: str = os.getenv("NVIDIA_CHAT_API_KEY", "")

    # Anthropic settings
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    # Groq API settings (Ultra-Fast Inference)
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_BASE_URL: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Supabase (Postgres + pgvector) — pedoman & riwayat hasil cek
    SUPABASE_DB_URL: str = os.getenv("SUPABASE_DB_URL", "")

    # Vector store & Chunking parameters
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./.chroma_data")
    CHUNK_SIZE_TOKENS: int = int(os.getenv("CHUNK_SIZE_TOKENS", "500"))
    CHUNK_OVERLAP_TOKENS: int = int(os.getenv("CHUNK_OVERLAP_TOKENS", "50"))
    RETRIEVAL_TOP_K: int = int(os.getenv("RETRIEVAL_TOP_K", "20"))
    RERANK_TOP_N: int = int(os.getenv("RERANK_TOP_N", "5"))

    # Jumlah blok yang diperiksa paralel per dokumen. Waktunya habis menunggu jawaban
    # model, bukan memakai CPU/RAM, jadi batas praktisnya adalah batas laju API. Di Render
    # free tier (512MB) pernah harus 1; di server dengan RAM lega aman dinaikkan.
    AI_MAX_WORKERS: int = max(1, int(os.getenv("AI_MAX_WORKERS", "5")))
    # Batas TOTAL panggilan LLM serentak dari semua pemeriksaan sekaligus (lihat llm_limiter.py).
    # Mencegah beberapa pengguna bersamaan melewati batas laju penyedia model.
    AI_MAX_CONCURRENT_LLM: int = max(1, int(os.getenv("AI_MAX_CONCURRENT_LLM", "16")))

    # OCR untuk PDF hasil scan (halaman berisi gambar tanpa teks), memakai Tesseract (lihat ocr.py).
    AI_OCR_ENABLED: bool = os.getenv("AI_OCR_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
    AI_OCR_MAX_PAGES: int = max(1, int(os.getenv("AI_OCR_MAX_PAGES", "150")))
    AI_OCR_DPI: int = max(100, int(os.getenv("AI_OCR_DPI", "200")))
    AI_OCR_LANGS: str = os.getenv("AI_OCR_LANGS", "ind+eng")
    AI_OCR_PAGE_TIMEOUT: int = max(10, int(os.getenv("AI_OCR_PAGE_TIMEOUT", "120")))

    # Simpan file .docx asli yang diunggah (untuk ekspor "Naskah Asli + Komentar"). BAWAAN MATI:
    # tanpa login di depan /ai-api, file asli yang tersimpan bisa diunduh siapa saja yang
    # mengetahui hash di laporan (riwayat laporan terbuka). Nyalakan hanya di lingkungan
    # tertutup (mis. laptop pengembang) atau setelah layanan AI dilindungi login.
    AI_STORE_SOURCE_FILES: bool = os.getenv("AI_STORE_SOURCE_FILES", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


settings = Settings()
