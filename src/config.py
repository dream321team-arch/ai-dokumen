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

    # NVIDIA API settings (Embeddings, Reranking & LLM Fallback)
    NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", "")
    NVIDIA_BASE_URL: str = os.getenv(
        "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
    )
    NVIDIA_LLM_MODEL: str = os.getenv(
        "NVIDIA_LLM_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"
    )
    NVIDIA_CHAT_MODEL: str = os.getenv(
        "NVIDIA_CHAT_MODEL", "openai/gpt-oss-120b"
    )
    NVIDIA_CHAT_API_KEY: str = os.getenv("NVIDIA_CHAT_API_KEY", "")
    NVIDIA_EMBED_URL: str = os.getenv(
        "NVIDIA_EMBED_URL", "https://integrate.api.nvidia.com/v1/embeddings"
    )
    NVIDIA_EMBED_MODEL: str = os.getenv(
        "NVIDIA_EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2"
    )
    NVIDIA_RERANK_URL: str = os.getenv(
        "NVIDIA_RERANK_URL",
        "https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-nemotron-rerank-vl-1b-v2/reranking",
    )

    # Anthropic settings
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    # Groq API settings (Ultra-Fast Inference)
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_BASE_URL: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Vector store & Chunking parameters
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./.chroma_data")
    CHUNK_SIZE_TOKENS: int = int(os.getenv("CHUNK_SIZE_TOKENS", "500"))
    CHUNK_OVERLAP_TOKENS: int = int(os.getenv("CHUNK_OVERLAP_TOKENS", "50"))
    RETRIEVAL_TOP_K: int = int(os.getenv("RETRIEVAL_TOP_K", "20"))
    RERANK_TOP_N: int = int(os.getenv("RERANK_TOP_N", "5"))


settings = Settings()
