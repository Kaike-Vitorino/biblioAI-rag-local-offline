from __future__ import annotations

import errno
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.config import Settings
from app.services.ingestion import IngestionService


ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}
ALLOWED_MIME_BY_EXTENSION = {
    ".pdf": {"application/pdf", "application/x-pdf", "application/octet-stream"},
    ".txt": {"text/plain", "text/markdown", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "text/x-markdown", "application/octet-stream"},
}


@dataclass
class UploadResult:
    saved_as: str
    docs_dir: str
    triggered_ingest: bool
    job_id: str | None


class UploadValidationError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class UploadService:
    def __init__(self, settings: Settings, ingestion_service: IngestionService):
        self.settings = settings
        self.ingestion_service = ingestion_service
        self.max_bytes = max(1, settings.upload_max_mb) * 1024 * 1024

    async def save_and_trigger_ingest(self, upload_file: UploadFile) -> UploadResult:
        original_name = upload_file.filename or "arquivo"
        sanitized_name = self._sanitize_filename(original_name)
        extension = Path(sanitized_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise UploadValidationError("Tipo nao suportado. Envie apenas .pdf, .txt ou .md.", status_code=415)

        self._validate_mime(upload_file, extension)
        docs_dir = self.settings.docs_dir.resolve()
        docs_dir.mkdir(parents=True, exist_ok=True)
        target_path = self._resolve_unique_path(docs_dir, sanitized_name)

        total_size = 0
        sample = b""
        chunk_size = 1024 * 1024
        try:
            with target_path.open("wb") as handle:
                while True:
                    chunk = await upload_file.read(chunk_size)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > self.max_bytes:
                        raise UploadValidationError(
                            f"Arquivo grande demais. Limite: {self.settings.upload_max_mb} MB.",
                            status_code=413,
                        )
                    if len(sample) < 4096:
                        missing = 4096 - len(sample)
                        sample += chunk[:missing]
                    handle.write(chunk)
        except UploadValidationError:
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            raise
        except OSError as exc:
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            if exc.errno == errno.ENOSPC:
                raise UploadValidationError("Sem espaco em disco para salvar o arquivo.", status_code=507) from exc
            raise UploadValidationError("Falha ao salvar arquivo no servidor.", status_code=500) from exc
        finally:
            await upload_file.close()

        if total_size <= 0:
            target_path.unlink(missing_ok=True)
            raise UploadValidationError("Arquivo vazio. Selecione um arquivo com conteudo.", status_code=400)

        if extension == ".pdf":
            if not sample.lstrip().startswith(b"%PDF"):
                target_path.unlink(missing_ok=True)
                raise UploadValidationError(
                    "Arquivo PDF invalido. Verifique se o arquivo selecionado e um PDF real.",
                    status_code=415,
                )

        job_id, started_new_job = self.ingestion_service.start_ingest_if_idle(str(docs_dir))
        return UploadResult(
            saved_as=target_path.name,
            docs_dir=self._docs_dir_label(docs_dir),
            triggered_ingest=started_new_job,
            job_id=job_id,
        )

    @staticmethod
    def _sanitize_filename(file_name: str) -> str:
        raw_name = Path(file_name or "arquivo").name
        normalized = unicodedata.normalize("NFKD", raw_name)
        ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
        ascii_name = ascii_name.replace("\x00", "").strip()
        if not ascii_name:
            ascii_name = "arquivo"
        suffix = Path(ascii_name).suffix.lower()
        stem = Path(ascii_name).stem
        stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem)
        stem = re.sub(r"\s+", " ", stem).strip(" ._-")
        if not stem:
            stem = "arquivo"
        return f"{stem}{suffix}"

    @staticmethod
    def _resolve_unique_path(docs_dir: Path, file_name: str) -> Path:
        candidate = docs_dir / file_name
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        for index in range(1, 10000):
            alternative = docs_dir / f"{stem} ({index}){suffix}"
            if not alternative.exists():
                return alternative
        raise UploadValidationError("Nao foi possivel definir nome unico para o arquivo.", status_code=500)

    @staticmethod
    def _docs_dir_label(docs_dir: Path) -> str:
        cwd = Path.cwd().resolve()
        try:
            rel = os.path.relpath(docs_dir, cwd)
            return rel.replace("\\", "/")
        except Exception:
            return str(docs_dir)

    @staticmethod
    def _validate_mime(upload_file: UploadFile, extension: str) -> None:
        content_type = (upload_file.content_type or "").strip().lower()
        if not content_type:
            return
        allowed = ALLOWED_MIME_BY_EXTENSION.get(extension, set())
        if content_type in allowed:
            return
        raise UploadValidationError(
            f"Tipo MIME nao suportado para {extension}: {content_type}.",
            status_code=415,
        )
