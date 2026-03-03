export type Citation = {
  source_id: string;
  chunk_id: string;
  doc_id: string;
  file_name: string;
  file_path: string;
  page_start: number;
  page_end: number;
  quote: string;
};

export type Claim = {
  claim_id: string;
  text: string;
  citations: Citation[];
};

export type QASuggestion = {
  question: string;
  answer: string;
  citations: Citation[];
};

export type SourceUsed = {
  source_id: string;
  chunk_id: string;
  doc_id: string;
  file_name: string;
  file_path: string;
  page_start: number;
  page_end: number;
};

export type AllReference = SourceUsed & {
  score?: number;
  focus_match?: boolean;
};

export type ChatResponse = {
  conversation_id: string;
  question: string;
  searched_terms: string[];
  not_found: boolean;
  message?: string | null;
  synopsis: string;
  key_points: string[];
  suggested_qa: QASuggestion[];
  claims: Claim[];
  sources: SourceUsed[];
  all_references?: AllReference[];
};

export type ChatRequest = {
  conversation_id?: string;
  question: string;
};

export type ChatCreateRequest = {
  title?: string;
};

export type ChatRenameRequest = {
  title: string;
};

export type ChatListItem = {
  id: string;
  title: string;
  created_at: string;
  updated_at?: string | null;
};

export type ChatsResponse = {
  chats: ChatListItem[];
};

export type ChatHistoryMessage = {
  id: string;
  role: "user" | "assistant" | string;
  content: Record<string, unknown>;
  created_at: string;
};

export type ChatMessagesResponse = {
  chat_id: string;
  messages: ChatHistoryMessage[];
};

export type DocItem = {
  id: string;
  file_path: string;
  file_name: string;
  sha256: string;
  page_count: number;
  is_enabled: boolean;
};

export type DocsResponse = {
  docs: DocItem[];
};

export type IngestStatusResponse = {
  job_id: string;
  status: string;
  docs_path: string;
  progress: {
    files_total?: number;
    files_done?: number;
    pages_done?: number;
    chunks_done?: number;
    skipped?: number;
    updated?: number;
  };
  errors: string[];
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

export type UploadResponse = {
  ok: boolean;
  saved_as: string;
  docs_dir: string;
  triggered_ingest: boolean;
  job_id?: string | null;
};

export type HighlightSegment = {
  page_number: number;
  start?: number | null;
  end?: number | null;
  snippet: string;
  approximate: boolean;
};

export type HighlightResponse = {
  source_id: string;
  doc_id: string;
  page_start: number;
  page_end: number;
  method: string;
  highlights: HighlightSegment[];
};

export type SourceSelection = {
  sourceId: string;
  docId: string;
  fileName: string;
  pageNumber: number;
  snippet: string;
  isPdf: boolean;
  start?: number | null;
  end?: number | null;
  method: string;
};
