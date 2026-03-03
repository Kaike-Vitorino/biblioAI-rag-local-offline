from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str
    host: str
    port: int
    docs_dir: Path
    data_dir: Path
    db_path: Path
    faiss_index_path: Path
    faiss_map_path: Path
    model: str
    ollama_base_url: str
    temperature: float
    num_ctx: int
    topk: int
    lexical_topn: int
    vector_topn: int
    chunk_size: int
    chunk_overlap: int
    embedding_provider: str
    embedding_model: str
    embedding_model_path: str
    embedding_batch_size: int
    cache_ttl_seconds: int
    cache_max_items: int
    cors_origins: list[str]
    query_planner_enabled: bool
    query_planner_model: str
    query_planner_timeout: int
    upload_max_mb: int

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.getenv("APP_ROOT", Path.cwd())).resolve()
        data_dir = Path(os.getenv("DATA_DIR", root / "data")).resolve()
        docs_dir = Path(os.getenv("DOCS_DIR", root / "docs")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        docs_dir.mkdir(parents=True, exist_ok=True)

        settings = cls(
            app_name=os.getenv("APP_NAME", "Local RAG"),
            host=os.getenv("BACKEND_HOST", os.getenv("HOST", "0.0.0.0")),
            port=_env_int("BACKEND_PORT", _env_int("PORT", 8000)),
            docs_dir=docs_dir,
            data_dir=data_dir,
            db_path=Path(os.getenv("DB_PATH", str(data_dir / "rag.db"))).resolve(),
            faiss_index_path=Path(os.getenv("FAISS_INDEX_PATH", str(data_dir / "chunks.faiss"))).resolve(),
            faiss_map_path=Path(os.getenv("FAISS_MAP_PATH", str(data_dir / "chunks_map.json"))).resolve(),
            model=os.getenv("MODEL", "qwen3:8b"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            temperature=_env_float("TEMPERATURE", 0.1),
            num_ctx=_env_int("NUM_CTX", 8192),
            topk=_env_int("FINAL_CHUNKS", _env_int("TOPK", 12)),
            lexical_topn=_env_int("LEXICAL_TOPN", 40),
            vector_topn=_env_int("VECTOR_TOPN", 40),
            chunk_size=_env_int("CHUNK_SIZE", 1000),
            chunk_overlap=_env_int("OVERLAP", _env_int("CHUNK_OVERLAP", 120)),
            embedding_provider=os.getenv("EMBEDDING_PROVIDER", "ollama").lower(),
            embedding_model=os.getenv("EMBED_MODEL", os.getenv("EMBEDDING_MODEL", "nomic-embed-text")),
            embedding_model_path=os.getenv("EMBEDDING_MODEL_PATH", ""),
            embedding_batch_size=_env_int("EMBEDDING_BATCH_SIZE", 32),
            cache_ttl_seconds=_env_int("CACHE_TTL_SECONDS", 900),
            cache_max_items=_env_int("CACHE_MAX_ITEMS", 512),
            cors_origins=[
                origin.strip()
                for origin in os.getenv(
                    "CORS_ORIGINS",
                    "http://127.0.0.1:5173,http://localhost:5173",
                ).split(",")
                if origin.strip()
            ],
            query_planner_enabled=os.getenv("QUERY_PLANNER_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"},
            query_planner_model=os.getenv("QUERY_PLANNER_MODEL", os.getenv("MODEL", "qwen3:8b")),
            query_planner_timeout=_env_int("QUERY_PLANNER_TIMEOUT", 20),
            upload_max_mb=_env_int("UPLOAD_MAX_MB", 50),
        )

        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        settings.faiss_index_path.parent.mkdir(parents=True, exist_ok=True)
        settings.faiss_map_path.parent.mkdir(parents=True, exist_ok=True)
        return settings


settings = Settings.from_env()
