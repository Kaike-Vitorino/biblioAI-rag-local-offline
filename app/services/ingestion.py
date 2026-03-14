from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

from app.config import Settings
from app.db.database import Database
from app.services.embedding import EmbeddingService
from app.services.text_utils import ChunkSlice, chunk_page_text
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)
SUPPORTED_DOC_EXTENSIONS = {".pdf", ".txt", ".md"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


class IngestionService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        embedding_service: EmbeddingService,
        vector_store: VectorStore,
    ):
        self.db = db
        self.settings = settings
        self.embedding_service = embedding_service
        self.vector_store = vector_store
        self._ingest_lock = threading.Lock()

    def start_ingest(self, docs_path: str | None = None) -> str:
        path = Path(docs_path or self.settings.docs_dir).resolve()
        path.mkdir(parents=True, exist_ok=True)
        job_id = f"ing_{uuid.uuid4().hex[:12]}"
        progress = {
            "files_total": 0,
            "files_done": 0,
            "pages_done": 0,
            "chunks_done": 0,
            "skipped": 0,
            "updated": 0,
            "removed": 0,
        }
        self.db.upsert_ingest_job(
            job_id=job_id,
            status="queued",
            docs_path=str(path),
            progress=progress,
            errors=[],
        )
        thread = threading.Thread(
            target=self._run_ingest, args=(job_id, path), daemon=True
        )
        thread.start()
        return job_id

    def start_ingest_if_idle(
        self, docs_path: str | None = None
    ) -> tuple[str | None, bool]:
        active = self.get_active_job_status()
        if active is not None:
            return active["job_id"], False
        return self.start_ingest(docs_path), True

    def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM ingest_jobs WHERE id = ?", [job_id])
        if row is None:
            return None
        return self._row_to_job_status(row)

    def get_latest_job_status(self) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM ingest_jobs ORDER BY created_at DESC LIMIT 1"
        )
        if row is None:
            return None
        return self._row_to_job_status(row)

    def get_active_job_status(self) -> dict[str, Any] | None:
        row = self.db.fetchone(
            """
            SELECT *
            FROM ingest_jobs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        if row is None:
            return None
        return self._row_to_job_status(row)

    @staticmethod
    def _row_to_job_status(row: Any) -> dict[str, Any]:
        return {
            "job_id": row["id"],
            "status": row["status"],
            "docs_path": row["docs_path"],
            "progress": json.loads(row["progress_json"] or "{}"),
            "errors": json.loads(row["errors_json"] or "[]"),
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    def _run_ingest(self, job_id: str, docs_path: Path) -> None:
        if not self._ingest_lock.acquire(blocking=False):
            self.db.upsert_ingest_job(
                job_id=job_id,
                status="failed",
                docs_path=str(docs_path),
                progress={"message": "Another ingestion is already running."},
                errors=["Another ingestion job is already running."],
                started_at=_utc_now_iso(),
                finished_at=_utc_now_iso(),
            )
            return

        started_at = _utc_now_iso()
        errors: list[str] = []
        progress = {
            "files_total": 0,
            "files_done": 0,
            "pages_done": 0,
            "chunks_done": 0,
            "skipped": 0,
            "updated": 0,
            "removed": 0,
        }
        try:
            files_to_process = self._discover_supported_files(docs_path)
            progress["removed"] = self._remove_deleted_docs(docs_path, files_to_process)
            progress["files_total"] = len(files_to_process)
            self.db.upsert_ingest_job(
                job_id=job_id,
                status="running",
                docs_path=str(docs_path),
                progress=progress,
                errors=errors,
                started_at=started_at,
            )

            for file_path in files_to_process:
                try:
                    result = self._process_file(file_path)
                    progress["files_done"] += 1
                    progress["pages_done"] += result["pages"]
                    progress["chunks_done"] += result["chunks"]
                    if result["skipped"]:
                        progress["skipped"] += 1
                    else:
                        progress["updated"] += 1
                except Exception as exc:
                    logger.exception("Failed to ingest file %s", file_path)
                    errors.append(f"{file_path}: {exc}")
                    progress["files_done"] += 1

                self.db.upsert_ingest_job(
                    job_id=job_id,
                    status="running",
                    docs_path=str(docs_path),
                    progress=progress,
                    errors=errors,
                    started_at=started_at,
                )

            self.vector_store.rebuild_from_db(self.db)
            final_status = "completed" if not errors else "completed_with_errors"
            self.db.upsert_ingest_job(
                job_id=job_id,
                status=final_status,
                docs_path=str(docs_path),
                progress=progress,
                errors=errors,
                started_at=started_at,
                finished_at=_utc_now_iso(),
            )
        except Exception as exc:
            logger.exception("Ingestion job failed")
            errors.append(str(exc))
            self.db.upsert_ingest_job(
                job_id=job_id,
                status="failed",
                docs_path=str(docs_path),
                progress=progress,
                errors=errors,
                started_at=started_at,
                finished_at=_utc_now_iso(),
            )
        finally:
            self._ingest_lock.release()

    @staticmethod
    def _discover_supported_files(docs_path: Path) -> list[Path]:
        files: list[Path] = []
        for path in docs_path.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in SUPPORTED_DOC_EXTENSIONS:
                files.append(path)
        return sorted(files, key=lambda p: str(p).lower())

    def _remove_deleted_docs(self, docs_path: Path, existing_files: list[Path]) -> int:
        existing_paths = {str(path.resolve()) for path in existing_files}
        rows = self.db.fetchall("SELECT id, file_path FROM docs")
        removed = 0
        for row in rows:
            file_path = Path(str(row["file_path"])).resolve()
            if file_path.suffix.lower() not in SUPPORTED_DOC_EXTENSIONS:
                continue
            try:
                file_path.relative_to(docs_path)
            except ValueError:
                continue
            if str(file_path) in existing_paths:
                continue
            doc_id = str(row["id"])
            with self.db.transaction() as cursor:
                cursor.execute("DELETE FROM docs WHERE id = ?", [doc_id])
                cursor.execute("DELETE FROM chunks_fts WHERE doc_id = ?", [doc_id])
            removed += 1
        return removed

    def _process_file(self, file_path: Path) -> dict[str, Any]:
        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            return self._process_pdf(file_path)
        if suffix in {".txt", ".md"}:
            return self._process_text_file(file_path)
        return {"skipped": True, "pages": 0, "chunks": 0}

    def _process_pdf(self, pdf_path: Path) -> dict[str, Any]:
        file_path = str(pdf_path.resolve())
        file_name = pdf_path.name
        sha256 = _sha256_file(pdf_path)
        existing = self.db.fetchone(
            "SELECT * FROM docs WHERE file_path = ?", [file_path]
        )

        if existing and existing["sha256"] == sha256:
            doc_id = existing["id"]
            if not self._doc_has_complete_embeddings(doc_id):
                logger.warning(
                    "Embeddings incompletos para %s. Regenerando embeddings.", file_name
                )
                self._create_embeddings_for_doc(doc_id)
            return {"skipped": True, "pages": 0, "chunks": 0}

        if existing:
            doc_id_to_delete = existing["id"]
            with self.db.transaction() as cursor:
                cursor.execute("DELETE FROM docs WHERE id = ?", [doc_id_to_delete])
                cursor.execute(
                    "DELETE FROM chunks_fts WHERE doc_id = ?", [doc_id_to_delete]
                )

        doc_id = f"doc_{uuid.uuid4().hex}"
        pages_to_insert: list[tuple[str, str, int, str]] = []
        chunks_to_insert: list[tuple[str, str, str, int, int, str, int, str]] = []
        chunks_fts_to_insert: list[tuple[str, str, str]] = []

        total_pages = 0
        try:
            with fitz.open(pdf_path) as pdf:
                total_pages = pdf.page_count
                for page_idx in range(pdf.page_count):
                    page_number = page_idx + 1
                    # Garantir conversão segura para string
                    raw_text = pdf[page_idx].get_text("text")
                    if isinstance(raw_text, str):
                        page_text = raw_text
                    else:
                        page_text = str(raw_text) if raw_text else ""

                    # Fallback simples para texto vazio
                    if not page_text.strip():
                        page_text = "[Imagem sem texto detectável]"

                    pages_to_insert.append(
                        (f"page_{uuid.uuid4().hex}", doc_id, page_number, page_text)
                    )

                    # Chunking
                    chunk_slices = chunk_page_text(
                        page_text=page_text,
                        page_number=page_number,
                        chunk_size=self.settings.chunk_size,
                        overlap=self.settings.chunk_overlap,
                    )
                    for local_idx, slice_item in enumerate(chunk_slices):
                        chunk_id = f"{doc_id}_p{page_number}_c{local_idx}"
                        offsets = {
                            "page_number": page_number,
                            "char_start_in_page": slice_item.char_start_in_page,
                            "char_end_in_page": slice_item.char_end_in_page,
                            "snippet": slice_item.text[:300],
                        }
                        chunks_to_insert.append(
                            (
                                f"chk_{uuid.uuid4().hex}",
                                doc_id,
                                chunk_id,
                                page_number,
                                page_number,
                                slice_item.text,
                                slice_item.token_count,
                                json.dumps(offsets, ensure_ascii=False),
                            )
                        )
                        chunks_fts_to_insert.append((slice_item.text, chunk_id, doc_id))
        except Exception as e:
            logger.error(f"Error processing PDF {pdf_path}: {e}")
            raise

        with self.db.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO docs(id, file_path, file_name, sha256, page_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                [doc_id, file_path, file_name, sha256, total_pages],
            )
            cursor.executemany(
                """
                INSERT INTO pages(id, doc_id, page_number, text)
                VALUES (?, ?, ?, ?)
                """,
                pages_to_insert,
            )
            cursor.executemany(
                """
                INSERT INTO chunks(id, doc_id, chunk_id, page_start, page_end, text, token_count, offsets_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                chunks_to_insert,
            )
            cursor.executemany(
                """
                INSERT INTO chunks_fts(text, chunk_id, doc_id)
                VALUES (?, ?, ?)
                """,
                chunks_fts_to_insert,
            )

        try:
            self._create_embeddings_for_doc(doc_id)
        except Exception:
            # Avoid leaving partially indexed docs that will be skipped by SHA on future runs.
            self._delete_doc_and_related(doc_id)
            raise
        return {"skipped": False, "pages": total_pages, "chunks": len(chunks_to_insert)}

    def _process_text_file(self, text_path: Path) -> dict[str, Any]:
        file_path = str(text_path.resolve())
        file_name = text_path.name
        sha256 = _sha256_file(text_path)
        existing = self.db.fetchone(
            "SELECT * FROM docs WHERE file_path = ?", [file_path]
        )

        if existing and existing["sha256"] == sha256:
            doc_id = existing["id"]
            if not self._doc_has_complete_embeddings(doc_id):
                logger.warning(
                    "Embeddings incompletos para %s. Regenerando embeddings.", file_name
                )
                self._create_embeddings_for_doc(doc_id)
            return {"skipped": True, "pages": 0, "chunks": 0}

        if existing:
            doc_id_to_delete = existing["id"]
            with self.db.transaction() as cursor:
                cursor.execute("DELETE FROM docs WHERE id = ?", [doc_id_to_delete])
                cursor.execute(
                    "DELETE FROM chunks_fts WHERE doc_id = ?", [doc_id_to_delete]
                )

        page_text = self._read_text_file(text_path)
        doc_id = f"doc_{uuid.uuid4().hex}"
        pages_to_insert = [(f"page_{uuid.uuid4().hex}", doc_id, 1, page_text)]
        chunks_to_insert: list[tuple[str, str, str, int, int, str, int, str]] = []
        chunks_fts_to_insert: list[tuple[str, str, str]] = []

        chunk_slices = chunk_page_text(
            page_text=page_text,
            page_number=1,
            chunk_size=self.settings.chunk_size,
            overlap=self.settings.chunk_overlap,
        )
        for local_idx, slice_item in enumerate(chunk_slices):
            chunk_id = f"{doc_id}_p1_c{local_idx}"
            offsets = {
                "page_number": 1,
                "char_start_in_page": slice_item.char_start_in_page,
                "char_end_in_page": slice_item.char_end_in_page,
                "snippet": slice_item.text[:300],
            }
            chunks_to_insert.append(
                (
                    f"chk_{uuid.uuid4().hex}",
                    doc_id,
                    chunk_id,
                    1,
                    1,
                    slice_item.text,
                    slice_item.token_count,
                    json.dumps(offsets, ensure_ascii=False),
                )
            )
            chunks_fts_to_insert.append((slice_item.text, chunk_id, doc_id))

        with self.db.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO docs(id, file_path, file_name, sha256, page_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                [doc_id, file_path, file_name, sha256, 1],
            )
            cursor.executemany(
                """
                INSERT INTO pages(id, doc_id, page_number, text)
                VALUES (?, ?, ?, ?)
                """,
                pages_to_insert,
            )
            if chunks_to_insert:
                cursor.executemany(
                    """
                    INSERT INTO chunks(id, doc_id, chunk_id, page_start, page_end, text, token_count, offsets_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    chunks_to_insert,
                )
                cursor.executemany(
                    """
                    INSERT INTO chunks_fts(text, chunk_id, doc_id)
                    VALUES (?, ?, ?)
                    """,
                    chunks_fts_to_insert,
                )

        if chunks_to_insert:
            try:
                self._create_embeddings_for_doc(doc_id)
            except Exception:
                self._delete_doc_and_related(doc_id)
                raise
        return {"skipped": False, "pages": 1, "chunks": len(chunks_to_insert)}

    @staticmethod
    def _read_text_file(text_path: Path) -> str:
        raw = text_path.read_bytes()
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
            try:
                decoded = raw.decode(encoding)
                if decoded:
                    return decoded
                return ""
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _create_embeddings_for_doc(self, doc_id: str) -> None:
        rows = self.db.fetchall(
            """
            SELECT chunk_id, text
            FROM chunks
            WHERE doc_id = ?
            ORDER BY chunk_id
            """,
            [doc_id],
        )
        if not rows:
            return

        batch_size = max(1, self.settings.embedding_batch_size)
        expected_dim = self._get_existing_embedding_dim()
        with self.db.transaction() as cursor:
            cursor.execute(
                """
                DELETE FROM embeddings
                WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE doc_id = ?)
                """,
                [doc_id],
            )
            for i in range(0, len(rows), batch_size):
                batch = rows[i : i + batch_size]
                texts = [row["text"] for row in batch]
                vectors = self.embedding_service.embed_texts(
                    texts, expected_dim=expected_dim
                )
                if expected_dim is None and vectors.shape[0] > 0:
                    expected_dim = int(vectors.shape[1])
                for row, vector in zip(batch, vectors):
                    vector_blob = vector.astype("float32").tobytes()
                    cursor.execute(
                        """
                        INSERT INTO embeddings(chunk_id, dim, vector)
                        VALUES (?, ?, ?)
                        """,
                        [row["chunk_id"], int(vector.shape[0]), vector_blob],
                    )

    def _doc_has_complete_embeddings(self, doc_id: str) -> bool:
        row = self.db.fetchone(
            """
            SELECT
                (SELECT count(*) FROM chunks WHERE doc_id = ?) AS chunks_count,
                (SELECT count(*) FROM embeddings e
                 JOIN chunks c ON c.chunk_id = e.chunk_id
                 WHERE c.doc_id = ?) AS emb_count
            """,
            [doc_id, doc_id],
        )
        if row is None:
            return False
        chunks_count = int(row["chunks_count"] or 0)
        emb_count = int(row["emb_count"] or 0)
        return chunks_count > 0 and chunks_count == emb_count

    def _delete_doc_and_related(self, doc_id: str) -> None:
        with self.db.transaction() as cursor:
            cursor.execute("DELETE FROM chunks_fts WHERE doc_id = ?", [doc_id])
            cursor.execute("DELETE FROM docs WHERE id = ?", [doc_id])

    def _get_existing_embedding_dim(self) -> int | None:
        row = self.db.fetchone("SELECT dim FROM embeddings LIMIT 1")
        if row is None:
            return None
        return int(row["dim"])
