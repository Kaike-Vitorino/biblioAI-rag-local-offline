from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db.database import Database
from app.models.schemas import (
    ChatCreateRequest,
    ChatHistoryMessage,
    ChatItem,
    ChatRequest,
    ChatRenameRequest,
    ChatMessagesResponse,
    ChatsResponse,
    DocItem,
    DocUpdateRequest,
    DocsResponse,
    HighlightResponse,
    IngestRequest,
    IngestResponse,
    IngestStatusResponse,
    PageTextResponse,
    UploadResponse,
)
from app.services.chat import ChatService
from app.services.embedding import EmbeddingService
from app.services.highlights import HighlightService
from app.services.ingestion import IngestionService
from app.services.llm import LLMService
from app.services.query_planner import QueryPlanner
from app.services.retrieval import RetrievalService
from app.services.upload import UploadService, UploadValidationError
from app.services.validation import ResponseValidator
from app.services.vector_store import VectorStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/api-docs",
    redoc_url="/api-redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = Database(settings.db_path)
embedding_service = EmbeddingService(settings)
vector_store = VectorStore(settings)
query_planner = QueryPlanner(settings)
retrieval_service = RetrievalService(db, settings, embedding_service, vector_store, query_planner=query_planner)
ingestion_service = IngestionService(db, settings, embedding_service, vector_store)
llm_service = LLMService(settings)
validator = ResponseValidator()
chat_service = ChatService(db, retrieval_service, llm_service, validator)
highlight_service = HighlightService(db)
upload_service = UploadService(settings, ingestion_service)


@app.on_event("startup")
def startup_event() -> None:
    loaded = vector_store.load()
    if not loaded:
        logger.info("No persisted FAISS index found. Rebuilding from SQLite embeddings.")
        vector_store.rebuild_from_db(db)


@app.get("/health")
def health() -> dict[str, str | int]:
    return {
        "status": "ok",
        "pid": os.getpid(),
        "build_hash": os.getenv("APP_BUILD_HASH", ""),
    }


@app.post("/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest | None = None) -> IngestResponse:
    docs_path = request.docs_path if request else None
    job_id = ingestion_service.start_ingest(docs_path)
    return IngestResponse(job_id=job_id, status="queued")


@app.post("/upload", response_model=UploadResponse)
async def upload_document(file: UploadFile = File(...)) -> UploadResponse:
    try:
        result = await upload_service.save_and_trigger_ingest(file)
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        logger.exception("Unhandled upload failure")
        raise HTTPException(status_code=500, detail="Falha ao processar upload do arquivo.") from exc
    return UploadResponse(
        ok=True,
        saved_as=result.saved_as,
        docs_dir=result.docs_dir,
        triggered_ingest=result.triggered_ingest,
        job_id=result.job_id,
    )


@app.get("/ingest/status", response_model=IngestStatusResponse)
def ingest_status_latest(job_id: str | None = None) -> IngestStatusResponse:
    status = ingestion_service.get_job_status(job_id) if job_id else ingestion_service.get_latest_job_status()
    if status is None:
        raise HTTPException(status_code=404, detail="Ingest job not found.")
    return IngestStatusResponse(**status)


@app.get("/ingest/{job_id}", response_model=IngestStatusResponse)
def ingest_status(job_id: str) -> IngestStatusResponse:
    status = ingestion_service.get_job_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Ingest job not found.")
    return IngestStatusResponse(**status)


@app.post("/chat")
def chat(request: ChatRequest) -> dict:
    try:
        return chat_service.answer(question=request.question, conversation_id=request.conversation_id)
    except Exception:
        logger.exception("Unhandled chat failure")
        fallback = validator.minimal_not_found()
        return {
            "conversation_id": request.conversation_id or "",
            "question": request.question,
            "searched_terms": [],
            "all_references": [],
            "not_found": fallback["not_found"],
            "message": "não encontrado nos documentos",
            "synopsis": fallback["synopsis"],
            "key_points": fallback["key_points"],
            "suggested_qa": fallback["suggested_qa"],
            "claims": fallback["claims"],
            "sources": fallback["sources"],
        }


@app.get("/chats", response_model=ChatsResponse)
def list_chats() -> ChatsResponse:
    rows = db.fetchall(
        """
        SELECT id, title, created_at, updated_at
        FROM conversations
        ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
        """
    )
    chats = [
        ChatItem(
            id=str(row["id"]),
            title=str(row["title"] or "Novo chat"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"] or row["created_at"]),
        )
        for row in rows
    ]
    return ChatsResponse(chats=chats)


@app.post("/chats", response_model=ChatItem)
def create_chat(request: ChatCreateRequest | None = None) -> ChatItem:
    chat_id = f"conv_{uuid.uuid4().hex}"
    title = (request.title if request else None) or "Novo chat"
    title = title.strip() or "Novo chat"
    db.execute(
        """
        INSERT INTO conversations(id, title, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        """,
        [chat_id, title[:120]],
    )
    row = db.fetchone("SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?", [chat_id])
    if row is None:
        raise HTTPException(status_code=500, detail="Falha ao criar chat.")
    return ChatItem(
        id=str(row["id"]),
        title=str(row["title"] or "Novo chat"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"] or row["created_at"]),
    )


@app.patch("/chats/{chat_id}", response_model=ChatItem)
def rename_chat(chat_id: str, request: ChatRenameRequest) -> ChatItem:
    row = db.fetchone("SELECT id FROM conversations WHERE id = ?", [chat_id])
    if row is None:
        raise HTTPException(status_code=404, detail="Chat nao encontrado.")
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Titulo nao pode ser vazio.")
    db.execute(
        """
        UPDATE conversations
        SET title = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        [title[:120], chat_id],
    )
    updated = db.fetchone("SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?", [chat_id])
    if updated is None:
        raise HTTPException(status_code=500, detail="Falha ao atualizar chat.")
    return ChatItem(
        id=str(updated["id"]),
        title=str(updated["title"] or "Novo chat"),
        created_at=str(updated["created_at"]),
        updated_at=str(updated["updated_at"] or updated["created_at"]),
    )


@app.delete("/chats/{chat_id}")
def delete_chat(chat_id: str) -> dict[str, bool]:
    row = db.fetchone("SELECT id FROM conversations WHERE id = ?", [chat_id])
    if row is None:
        raise HTTPException(status_code=404, detail="Chat nao encontrado.")
    db.execute("DELETE FROM conversations WHERE id = ?", [chat_id])
    return {"ok": True}


@app.get("/chats/{chat_id}/messages", response_model=ChatMessagesResponse)
def get_chat_messages(chat_id: str) -> ChatMessagesResponse:
    row = db.fetchone("SELECT id FROM conversations WHERE id = ?", [chat_id])
    if row is None:
        raise HTTPException(status_code=404, detail="Chat nao encontrado.")
    rows = db.fetchall(
        """
        SELECT id, role, content_json, created_at
        FROM messages
        WHERE conversation_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        [chat_id],
    )
    messages: list[ChatHistoryMessage] = []
    for msg_row in rows:
        try:
            content = json.loads(msg_row["content_json"] or "{}")
            if not isinstance(content, dict):
                content = {}
        except Exception:
            content = {}
        messages.append(
            ChatHistoryMessage(
                id=str(msg_row["id"]),
                role=str(msg_row["role"]),
                content=content,
                created_at=str(msg_row["created_at"]),
            )
        )
    return ChatMessagesResponse(chat_id=chat_id, messages=messages)


@app.post("/chats/{chat_id}/messages")
def post_chat_message(chat_id: str, request: ChatRequest) -> dict:
    row = db.fetchone("SELECT id FROM conversations WHERE id = ?", [chat_id])
    if row is None:
        raise HTTPException(status_code=404, detail="Chat nao encontrado.")
    try:
        return chat_service.answer(question=request.question, conversation_id=chat_id)
    except Exception:
        logger.exception("Unhandled chat message failure")
        fallback = validator.minimal_not_found()
        return {
            "conversation_id": chat_id,
            "question": request.question,
            "searched_terms": [],
            "all_references": [],
            "not_found": fallback["not_found"],
            "message": "nao encontrado nos documentos",
            "synopsis": fallback["synopsis"],
            "key_points": fallback["key_points"],
            "suggested_qa": fallback["suggested_qa"],
            "claims": fallback["claims"],
            "sources": fallback["sources"],
        }


@app.get("/docs", response_model=DocsResponse)
def list_docs() -> DocsResponse:
    rows = db.fetchall("SELECT id, file_path, file_name, sha256, page_count, is_enabled FROM docs ORDER BY file_name")
    docs = [
        DocItem(
            id=row["id"],
            file_path=row["file_path"],
            file_name=row["file_name"],
            sha256=row["sha256"],
            page_count=row["page_count"],
            is_enabled=bool(row["is_enabled"]),
        )
        for row in rows
    ]
    return DocsResponse(docs=docs)




@app.patch("/docs/{doc_id}", response_model=DocItem)
def update_doc(doc_id: str, request: DocUpdateRequest) -> DocItem:
    row = db.fetchone("SELECT id FROM docs WHERE id = ?", [doc_id])
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    db.execute("UPDATE docs SET is_enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", [1 if request.is_enabled else 0, doc_id])
    updated = db.fetchone("SELECT id, file_path, file_name, sha256, page_count, is_enabled FROM docs WHERE id = ?", [doc_id])
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update document.")
    return DocItem(
        id=updated["id"],
        file_path=updated["file_path"],
        file_name=updated["file_name"],
        sha256=updated["sha256"],
        page_count=updated["page_count"],
        is_enabled=bool(updated["is_enabled"]),
    )


@app.delete("/docs/{doc_id}")
def delete_doc(doc_id: str) -> dict[str, bool]:
    row = db.fetchone("SELECT id, file_path FROM docs WHERE id = ?", [doc_id])
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    file_path = Path(str(row["file_path"]))
    db.execute("DELETE FROM docs WHERE id = ?", [doc_id])
    try:
        if file_path.exists():
            file_path.unlink()
    except Exception:
        logger.warning("Failed to remove file from disk: %s", file_path)
    vector_store.rebuild_from_db(db)
    return {"ok": True}

@app.get("/docs/{doc_id}/pdf")
def get_doc_pdf(doc_id: str) -> FileResponse:
    row = db.fetchone("SELECT file_path, file_name FROM docs WHERE id = ?", [doc_id])
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    file_path = Path(row["file_path"]).resolve()
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found on disk.")
    if file_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Document is not a PDF.")
    return FileResponse(path=file_path, filename=row["file_name"], media_type="application/pdf")


@app.get("/docs/{doc_id}/page/{page_number}/text", response_model=PageTextResponse)
def get_page_text(doc_id: str, page_number: int) -> PageTextResponse:
    row = db.fetchone(
        """
        SELECT text
        FROM pages
        WHERE doc_id = ? AND page_number = ?
        """,
        [doc_id, page_number],
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Page not found.")
    return PageTextResponse(doc_id=doc_id, page_number=page_number, text=row["text"])


@app.get("/highlights/{source_id}", response_model=HighlightResponse)
def get_highlight(source_id: str, snippet: str | None = None) -> HighlightResponse:
    highlight = highlight_service.get_highlight(source_id, preferred_snippet=snippet)
    if highlight is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return HighlightResponse(**highlight)


frontend_dist_dir = Path(__file__).resolve().parents[1] / "frontend" / "dist"
frontend_index_file = frontend_dist_dir / "index.html"


@app.get("/", response_model=None)
def frontend_root():
    if frontend_index_file.exists():
        return FileResponse(str(frontend_index_file))
    return {"status": "ok", "message": "Frontend build not found. Run launcher.py to build the UI."}


app.mount("/", StaticFiles(directory=str(frontend_dist_dir), html=True, check_dir=False), name="frontend")
