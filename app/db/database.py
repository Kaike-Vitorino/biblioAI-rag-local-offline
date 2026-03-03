from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS docs (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(doc_id, page_number),
    FOREIGN KEY(doc_id) REFERENCES docs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL UNIQUE,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    offsets_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(doc_id) REFERENCES docs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pages_doc_page ON pages(doc_id, page_number);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_page ON chunks(doc_id, page_start, page_end);
CREATE INDEX IF NOT EXISTS idx_chunks_chunk_id ON chunks(chunk_id);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id TEXT PRIMARY KEY,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ingest_jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    docs_path TEXT NOT NULL,
    progress_json TEXT NOT NULL DEFAULT '{}',
    errors_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'Novo chat',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS retrieval_logs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    question TEXT NOT NULL,
    searched_terms_json TEXT NOT NULL,
    selected_evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    chunk_id UNINDEXED,
    doc_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(SCHEMA_SQL)
            self._run_migrations()
            self.conn.commit()

    def _run_migrations(self) -> None:
        # docs.is_enabled
        if not self._column_exists("docs", "is_enabled"):
            self.conn.execute("ALTER TABLE docs ADD COLUMN is_enabled INTEGER NOT NULL DEFAULT 1")
            self.conn.execute("UPDATE docs SET is_enabled = 1 WHERE is_enabled IS NULL")

        # conversations.title
        if not self._column_exists("conversations", "title"):
            self.conn.execute("ALTER TABLE conversations ADD COLUMN title TEXT")
            self.conn.execute("UPDATE conversations SET title = COALESCE(title, 'Novo chat')")
            self.conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_conversations_title_default
                AFTER INSERT ON conversations
                FOR EACH ROW
                WHEN NEW.title IS NULL OR trim(NEW.title) = ''
                BEGIN
                    UPDATE conversations SET title = 'Novo chat' WHERE id = NEW.id;
                END;
                """
            )

        # conversations.updated_at
        if not self._column_exists("conversations", "updated_at"):
            self.conn.execute("ALTER TABLE conversations ADD COLUMN updated_at TEXT")
            self.conn.execute(
                """
                UPDATE conversations
                SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
                """
            )

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        cursor = self.conn.execute(f"PRAGMA table_info({table_name})")
        rows = cursor.fetchall()
        return any(str(row[1]) == column_name for row in rows)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("BEGIN")
                yield cursor
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            finally:
                cursor.close()

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> sqlite3.Cursor:
        with self._lock:
            cursor = self.conn.execute(sql, params or [])
            self.conn.commit()
            return cursor

    def executemany(self, sql: str, params: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        with self._lock:
            cursor = self.conn.executemany(sql, params)
            self.conn.commit()
            return cursor

    def fetchone(self, sql: str, params: Sequence[Any] | None = None) -> sqlite3.Row | None:
        with self._lock:
            cursor = self.conn.execute(sql, params or [])
            return cursor.fetchone()

    def fetchall(self, sql: str, params: Sequence[Any] | None = None) -> list[sqlite3.Row]:
        with self._lock:
            cursor = self.conn.execute(sql, params or [])
            return cursor.fetchall()

    def upsert_ingest_job(
        self,
        job_id: str,
        status: str,
        docs_path: str,
        progress: dict[str, Any] | None = None,
        errors: list[str] | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        progress_json = json.dumps(progress or {}, ensure_ascii=False)
        errors_json = json.dumps(errors or [], ensure_ascii=False)
        self.execute(
            """
            INSERT INTO ingest_jobs(id, status, docs_path, progress_json, errors_json, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                docs_path=excluded.docs_path,
                progress_json=excluded.progress_json,
                errors_json=excluded.errors_json,
                started_at=COALESCE(excluded.started_at, ingest_jobs.started_at),
                finished_at=excluded.finished_at
            """,
            [job_id, status, docs_path, progress_json, errors_json, started_at, finished_at],
        )
