from __future__ import annotations

import hashlib
import logging
from typing import Any

import numpy as np
import requests

from app.config import Settings
from app.services.cache import TTLCache

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.embedding_provider
        self.model = settings.embedding_model
        self.ollama_base_url = settings.ollama_base_url
        self._query_cache: TTLCache[np.ndarray] = TTLCache(
            max_items=settings.cache_max_items,
            ttl_seconds=settings.cache_ttl_seconds,
        )
        self._st_model: Any | None = None

    def embed_query(self, text: str) -> np.ndarray:
        cache_key = f"q::{text.strip().lower()}"
        cached = self._query_cache.get(cache_key)
        if cached is not None:
            return cached
        vector = self.embed_texts([text])[0]
        self._query_cache.set(cache_key, vector)
        return vector

    def embed_texts(self, texts: list[str], expected_dim: int | None = None) -> np.ndarray:
        clean_texts = [t.strip() for t in texts]
        if not clean_texts:
            return np.zeros((0, 1), dtype=np.float32)
        if self.provider == "ollama":
            return self._embed_with_ollama(clean_texts, expected_dim=expected_dim)
        if self.provider == "sentence_transformers":
            return self._embed_with_sentence_transformers(clean_texts)
        if self.provider == "hash":
            dim = expected_dim or 384
            return self._embed_with_hash(clean_texts, dim=dim)
        raise RuntimeError(
            f"Unsupported EMBEDDING_PROVIDER '{self.provider}'. Use ollama, sentence_transformers, or hash."
        )

    def _embed_with_ollama(self, texts: list[str], expected_dim: int | None = None) -> np.ndarray:
        batch_payload = {"model": self.model, "input": texts}
        try:
            response = requests.post(
                f"{self.ollama_base_url}/api/embed",
                json=batch_payload,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings")
            if embeddings and isinstance(embeddings, list):
                matrix = np.asarray(embeddings, dtype=np.float32)
                if expected_dim is not None and matrix.shape[1] != expected_dim:
                    matrix = self._coerce_matrix_dim(matrix, expected_dim)
                return matrix
        except Exception as exc:
            logger.warning("Batch embeddings via /api/embed failed, falling back to /api/embeddings: %s", exc)

        target_dim = expected_dim
        vectors: list[list[float]] = []
        for text in texts:
            vector = self._embed_one_ollama(text)
            if vector is not None:
                if target_dim is None:
                    target_dim = len(vector)
                elif len(vector) != target_dim:
                    vector = self._coerce_vector_dim(vector, target_dim)
                vectors.append(vector)
                continue

            if target_dim is None:
                target_dim = self._probe_embedding_dim() or 768
            logger.warning("Using hash fallback embedding for a chunk due Ollama failure.")
            vectors.append(self._hash_vector(text, target_dim))
        return np.asarray(vectors, dtype=np.float32)

    def _embed_with_sentence_transformers(self, texts: list[str]) -> np.ndarray:
        if self._st_model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is not installed. Install it or switch EMBEDDING_PROVIDER."
                ) from exc
            model_name = self.settings.embedding_model_path or self.model
            self._st_model = SentenceTransformer(model_name)
        vectors = self._st_model.encode(texts, normalize_embeddings=False, show_progress_bar=False)
        return np.asarray(vectors, dtype=np.float32)

    def _embed_with_hash(self, texts: list[str], dim: int = 384) -> np.ndarray:
        # Deterministic fallback for offline development when no local embedding model is available.
        vectors = np.zeros((len(texts), dim), dtype=np.float32)
        for row, text in enumerate(texts):
            vectors[row] = np.asarray(self._hash_vector(text, dim), dtype=np.float32)
        return vectors

    def _embed_one_ollama(self, text: str) -> list[float] | None:
        candidates = [text]
        if len(text) > 6000:
            candidates.append(text[:6000])
        if len(text) > 3500:
            candidates.append(text[:3500])
        if len(text) > 2000:
            candidates.append(text[:2000])
        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                resp = requests.post(
                    f"{self.ollama_base_url}/api/embeddings",
                    json={"model": self.model, "prompt": candidate},
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                vector = data.get("embedding")
                if vector and isinstance(vector, list):
                    return vector
            except Exception as exc:
                logger.warning("Ollama embedding request failed for one chunk: %s", exc)
        return None

    def _probe_embedding_dim(self) -> int | None:
        vector = self._embed_one_ollama("teste")
        return len(vector) if vector else None

    @staticmethod
    def _hash_vector(text: str, dim: int) -> list[float]:
        vector = np.zeros(dim, dtype=np.float32)
        words = text.lower().split()
        if not words:
            return vector.tolist()
        for word in words:
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], byteorder="little", signed=False) % dim
            vector[idx] += 1.0
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector /= norm
        return vector.tolist()

    @staticmethod
    def _coerce_vector_dim(vector: list[float], dim: int) -> list[float]:
        arr = np.asarray(vector, dtype=np.float32)
        if arr.shape[0] == dim:
            return arr.tolist()
        if arr.shape[0] > dim:
            arr = arr[:dim]
        else:
            arr = np.pad(arr, (0, dim - arr.shape[0]))
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr = arr / norm
        return arr.tolist()

    @staticmethod
    def _coerce_matrix_dim(matrix: np.ndarray, dim: int) -> np.ndarray:
        if matrix.shape[1] == dim:
            return matrix
        if matrix.shape[1] > dim:
            matrix = matrix[:, :dim]
        else:
            pad = np.zeros((matrix.shape[0], dim - matrix.shape[1]), dtype=np.float32)
            matrix = np.hstack([matrix, pad])
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms
