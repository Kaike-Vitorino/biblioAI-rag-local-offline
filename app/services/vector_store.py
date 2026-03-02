from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import faiss
import numpy as np

from app.config import Settings
from app.db.database import Database

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.index_path: Path = settings.faiss_index_path
        self.map_path: Path = settings.faiss_map_path
        self._lock = threading.RLock()
        self._index: faiss.Index | None = None
        self._chunk_ids: list[str] = []
        self._dim = 0

    def load(self) -> bool:
        with self._lock:
            if not self.index_path.exists() or not self.map_path.exists():
                return False
            self._index = faiss.read_index(str(self.index_path))
            self._chunk_ids = json.loads(self.map_path.read_text(encoding="utf-8"))
            self._dim = self._index.d
            logger.info("Loaded FAISS index with %s vectors (dim=%s).", len(self._chunk_ids), self._dim)
            return True

    def rebuild_from_db(self, db: Database) -> None:
        rows = db.fetchall("SELECT chunk_id, vector FROM embeddings ORDER BY chunk_id")
        with self._lock:
            if not rows:
                self._index = None
                self._chunk_ids = []
                self._dim = 0
                if self.index_path.exists():
                    self.index_path.unlink(missing_ok=True)
                if self.map_path.exists():
                    self.map_path.unlink(missing_ok=True)
                logger.info("No embeddings found. Cleared FAISS index artifacts.")
                return

            vectors: list[np.ndarray] = []
            chunk_ids: list[str] = []
            for row in rows:
                vector = np.frombuffer(row["vector"], dtype=np.float32)
                vectors.append(vector)
                chunk_ids.append(row["chunk_id"])

            matrix = np.vstack(vectors).astype(np.float32)
            self._dim = matrix.shape[1]
            faiss.normalize_L2(matrix)
            index = faiss.IndexFlatIP(self._dim)
            index.add(matrix)
            self._index = index
            self._chunk_ids = chunk_ids
            faiss.write_index(index, str(self.index_path))
            self.map_path.write_text(json.dumps(chunk_ids, ensure_ascii=False), encoding="utf-8")
            logger.info("Rebuilt FAISS index with %s vectors.", len(chunk_ids))

    def search(self, query_vector: np.ndarray, top_n: int) -> list[tuple[str, float]]:
        with self._lock:
            if self._index is None or not self._chunk_ids:
                return []
            vector = np.asarray(query_vector, dtype=np.float32).reshape(1, -1)
            if vector.shape[1] != self._dim:
                logger.warning(
                    "Query vector dim (%s) does not match index dim (%s). Returning empty vector results.",
                    vector.shape[1],
                    self._dim,
                )
                return []
            faiss.normalize_L2(vector)
            scores, indices = self._index.search(vector, max(1, top_n))
            results: list[tuple[str, float]] = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= len(self._chunk_ids):
                    continue
                results.append((self._chunk_ids[idx], float(score)))
            return results

