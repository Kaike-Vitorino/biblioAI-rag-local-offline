from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    docs_path: str | None = None


class IngestResponse(BaseModel):
    job_id: str
    status: str


class IngestStatusResponse(BaseModel):
    job_id: str
    status: str
    docs_path: str
    progress: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: str | None = None


class ChatCreateRequest(BaseModel):
    title: str | None = None


class ChatRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ChatItem(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str | None = None


class ChatsResponse(BaseModel):
    chats: list[ChatItem]


class ChatHistoryMessage(BaseModel):
    id: str
    role: str
    content: dict[str, Any]
    created_at: str


class ChatMessagesResponse(BaseModel):
    chat_id: str
    messages: list[ChatHistoryMessage]


class DocItem(BaseModel):
    id: str
    file_path: str
    file_name: str
    sha256: str
    page_count: int


class DocsResponse(BaseModel):
    docs: list[DocItem]


class PageTextResponse(BaseModel):
    doc_id: str
    page_number: int
    text: str


class HighlightSegment(BaseModel):
    page_number: int
    start: int | None = None
    end: int | None = None
    snippet: str
    approximate: bool = False


class HighlightResponse(BaseModel):
    source_id: str
    doc_id: str
    page_start: int
    page_end: int
    method: str
    highlights: list[HighlightSegment]


class UploadResponse(BaseModel):
    ok: bool
    saved_as: str
    docs_dir: str
    triggered_ingest: bool
    job_id: str | None = None
