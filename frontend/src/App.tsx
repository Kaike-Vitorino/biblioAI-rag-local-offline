import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import {
  API_BASE_URL,
  createChat,
  deleteChat,
  getChatMessages,
  getChats,
  getDocs,
  getHealth,
  getHighlight,
  getIngestJob,
  postChatMessage,
  renameChat,
  startIngest,
  uploadDocument
} from "./api";
import ChatInput from "./components/ChatInput";
import ChatMessage from "./components/ChatMessage";
import SourceViewer from "./components/SourceViewer";
import type { ChatHistoryMessage, ChatListItem, ChatResponse, DocItem, IngestStatusResponse, SourceSelection } from "./types";
import type { SourceLike } from "./components/SourceChip";

type UserTurn = {
  id: string;
  role: "user";
  text: string;
  createdAt?: string;
};

type AssistantTurn = {
  id: string;
  role: "assistant";
  response: ChatResponse | null;
  error?: string;
  createdAt?: string;
};

type Turn = UserTurn | AssistantTurn;

function makeId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatDate(dateString: string | undefined | null): string {
  if (!dateString) return "";
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit" });
}

function formatIngestProgress(status: IngestStatusResponse): string {
  const progress = status.progress || {};
  const filesDone = progress.files_done ?? 0;
  const filesTotal = progress.files_total ?? 0;
  const chunksDone = progress.chunks_done ?? 0;
  return `${status.status} | arquivos ${filesDone}/${filesTotal} | chunks ${chunksDone}`;
}

function normalizeHistoryMessageToTurn(item: ChatHistoryMessage): Turn | null {
  if (item.role === "user") {
    const question = String(item.content?.question ?? item.content?.text ?? "").trim();
    if (!question) return null;
    return {
      id: item.id,
      role: "user",
      text: question,
      createdAt: item.created_at
    };
  }
  if (item.role === "assistant") {
    const content = item.content as Partial<ChatResponse>;
    if (!content || typeof content !== "object") return null;
    const response: ChatResponse = {
      conversation_id: String(content.conversation_id ?? ""),
      question: String(content.question ?? ""),
      searched_terms: Array.isArray(content.searched_terms) ? content.searched_terms as string[] : [],
      all_references: Array.isArray(content.all_references) ? content.all_references : [],
      not_found: Boolean(content.not_found),
      message: content.message ? String(content.message) : null,
      synopsis: String(content.synopsis ?? ""),
      key_points: Array.isArray(content.key_points) ? content.key_points as string[] : [],
      suggested_qa: Array.isArray(content.suggested_qa) ? content.suggested_qa : [],
      claims: Array.isArray(content.claims) ? content.claims : [],
      sources: Array.isArray(content.sources) ? content.sources : []
    };
    return {
      id: item.id,
      role: "assistant",
      response,
      createdAt: item.created_at
    };
  }
  return null;
}

export default function App() {
  const [backendStatus, setBackendStatus] = useState<"connecting" | "online" | "offline">("connecting");
  const [chats, setChats] = useState<ChatListItem[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loading, setLoading] = useState(false);
  const [menuChatId, setMenuChatId] = useState<string | null>(null);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [mobileViewerOpen, setMobileViewerOpen] = useState(false);

  const [docs, setDocs] = useState<DocItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadMessage, setUploadMessage] = useState<string>("");
  const [ingestStatus, setIngestStatus] = useState<IngestStatusResponse | null>(null);

  const [viewerError, setViewerError] = useState<string | null>(null);
  const [viewerSources, setViewerSources] = useState<SourceSelection[]>([]);
  const [viewerIndex, setViewerIndex] = useState(0);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);

  const activeViewerSource = viewerSources.length ? viewerSources[Math.min(viewerIndex, viewerSources.length - 1)] : null;
  const viewerHasSources = viewerSources.length > 0;

  async function refreshDocs() {
    const data = await getDocs();
    setDocs(data.docs);
  }

  async function loadMessages(chatId: string) {
    const payload = await getChatMessages(chatId);
    const mapped = payload.messages
      .map(normalizeHistoryMessageToTurn)
      .filter((item): item is Turn => item !== null);
    setTurns(mapped);
  }

  async function refreshChats(preferredChatId?: string | null) {
    const payload = await getChats();
    let list = payload.chats;
    if (!list.length) {
      const created = await createChat({ title: "Novo chat" });
      list = [created];
    }
    setChats(list);
    const nextActive = preferredChatId && list.some((chat) => chat.id === preferredChatId)
      ? preferredChatId
      : list[0].id;
    setActiveChatId(nextActive);
    await loadMessages(nextActive);
  }

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      for (let attempt = 0; attempt < 12; attempt += 1) {
        try {
          const health = await getHealth();
          if (cancelled) return;
          if (health.status === "ok") {
            setBackendStatus("online");
            break;
          }
        } catch {
          if (!cancelled) setBackendStatus("connecting");
        }
        await sleep(1000);
      }
      if (cancelled) return;
      try {
        const health = await getHealth();
        if (cancelled) return;
        setBackendStatus(health.status === "ok" ? "online" : "offline");
      } catch {
        if (!cancelled) setBackendStatus("offline");
      }
      if (cancelled) return;
      try {
        await Promise.all([refreshDocs(), refreshChats(null)]);
      } catch {
        if (!cancelled) {
          setDocs([]);
          setChats([]);
          setTurns([]);
        }
      }
    }
    bootstrap().catch(() => {
      if (!cancelled) setBackendStatus("offline");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!messageListRef.current) return;
    messageListRef.current.scrollTop = messageListRef.current.scrollHeight;
  }, [turns, loading]);

  async function createAndSelectNewChat() {
    const created = await createChat({ title: "Novo chat" });
    setChats((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
    setActiveChatId(created.id);
    setTurns([]);
    setMenuChatId(null);
    setMobileSidebarOpen(false);
  }

  async function handleSelectChat(chatId: string) {
    if (chatId === activeChatId) {
      setMenuChatId(null);
      setMobileSidebarOpen(false);
      return;
    }
    setMenuChatId(null);
    setActiveChatId(chatId);
    setTurns([]);
    await loadMessages(chatId);
    setMobileSidebarOpen(false);
  }

  async function handleRenameChat(chat: ChatListItem) {
    const nextTitle = window.prompt("Novo nome do chat:", chat.title)?.trim();
    if (!nextTitle) return;
    const updated = await renameChat(chat.id, nextTitle);
    setChats((prev) => prev.map((item) => (item.id === chat.id ? updated : item)));
    setMenuChatId(null);
  }

  async function handleDeleteChat(chat: ChatListItem) {
    const confirmed = window.confirm(`Apagar o chat "${chat.title}"?`);
    if (!confirmed) return;
    await deleteChat(chat.id);
    const remaining = chats.filter((item) => item.id !== chat.id);
    setMenuChatId(null);
    if (!remaining.length) {
      await createAndSelectNewChat();
      return;
    }
    const nextActive = activeChatId === chat.id ? remaining[0].id : activeChatId;
    setChats(remaining);
    if (nextActive) {
      setActiveChatId(nextActive);
      await loadMessages(nextActive);
    }
  }

  async function monitorIngestJob(jobId: string) {
    const startedAt = Date.now();
    while (Date.now() - startedAt <= 60 * 60 * 1000) {
      const state = await getIngestJob(jobId);
      setIngestStatus(state);
      setUploadMessage(`Ingestao: ${formatIngestProgress(state)}`);
      const statusValue = state.status.toLowerCase();
      if (statusValue === "completed" || statusValue === "completed_with_errors") {
        await refreshDocs();
        if (statusValue === "completed_with_errors") {
          setUploadMessage(`Ingestao concluida com avisos: ${(state.errors || []).join(" | ")}`);
        } else {
          setUploadMessage("Ingestao concluida.");
        }
        return;
      }
      if (statusValue === "failed") {
        throw new Error(`Ingestao falhou: ${(state.errors || []).join(" | ")}`);
      }
      await sleep(1500);
    }
    throw new Error("Ingestao nao terminou no tempo esperado.");
  }

  async function handleFilesUpload(files: File[]) {
    if (!files.length) return;
    setUploading(true);
    setIngestStatus(null);
    try {
      for (const file of files) {
        setUploadProgress(0);
        setUploadMessage(`Enviando: ${file.name}`);
        const uploaded = await uploadDocument(file, (progress) => setUploadProgress(progress));
        setUploadMessage(`Salvo: ${uploaded.saved_as}`);
        setUploadProgress(100);
        await refreshDocs();
        if (uploaded.job_id) {
          await monitorIngestJob(uploaded.job_id);
        }
      }
      setUploadMessage("Pronto.");
    } catch (error) {
      setUploadMessage(error instanceof Error ? error.message : "Falha no upload.");
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  }

  async function handleManualIngest() {
    if (backendStatus !== "online") return;
    setUploading(true);
    setUploadMessage("Iniciando ingestao...");
    try {
      const started = await startIngest("docs");
      await monitorIngestJob(started.job_id);
    } catch (error) {
      setUploadMessage(error instanceof Error ? error.message : "Falha ao iniciar ingestao.");
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  }

  function onFileInputChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files ?? []);
    event.currentTarget.value = "";
    void handleFilesUpload(selected);
  }

  async function handleSend(question: string) {
    let chatId = activeChatId;
    if (!chatId) {
      const created = await createChat({ title: "Novo chat" });
      setChats((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
      setActiveChatId(created.id);
      chatId = created.id;
    }

    const userTurn: UserTurn = { id: makeId("u"), role: "user", text: question };
    setTurns((prev) => [...prev, userTurn]);
    setLoading(true);
    try {
      const response = await postChatMessage(chatId, question);
      const assistantTurn: AssistantTurn = {
        id: makeId("a"),
        role: "assistant",
        response
      };
      setTurns((prev) => [...prev, assistantTurn]);
      const refreshed = await getChats();
      setChats(refreshed.chats);
    } catch (error) {
      const assistantTurn: AssistantTurn = {
        id: makeId("a"),
        role: "assistant",
        response: null,
        error: error instanceof Error ? error.message : "Falha ao consultar o backend."
      };
      setTurns((prev) => [...prev, assistantTurn]);
    } finally {
      setLoading(false);
    }
  }

  async function openSource(source: SourceLike) {
    setViewerError(null);
    try {
      const sourceQuote = "quote" in source ? source.quote : "";
      const details = await getHighlight(source.source_id, sourceQuote);
      const first = details.highlights[0];
      const page = first?.page_number ?? source.page_start ?? details.page_start;
      const snippet = first?.snippet ?? sourceQuote ?? "";
      const fileName = source.file_name ?? details.doc_id;
      const selection: SourceSelection = {
        sourceId: source.source_id,
        docId: details.doc_id,
        fileName,
        pageNumber: page,
        snippet,
        isPdf: fileName.toLowerCase().endsWith(".pdf"),
        start: first?.start,
        end: first?.end,
        method: details.method
      };
      const key = `${selection.sourceId}::${selection.pageNumber}`;
      setViewerSources((prev) => {
        const existingIndex = prev.findIndex((item) => `${item.sourceId}::${item.pageNumber}` === key);
        if (existingIndex >= 0) {
          setViewerIndex(existingIndex);
          return prev;
        }
        const next = [selection, ...prev];
        setViewerIndex(0);
        return next.slice(0, 30);
      });
      setMobileViewerOpen(true);
    } catch (error) {
      setViewerError(error instanceof Error ? error.message : "Falha ao abrir fonte.");
    }
  }

  function renderViewerPanel() {
    return (
      <div className="viewer-shell">
        <div className="viewer-topbar">
          <h2>Fontes</h2>
          <div className="viewer-source-nav">
            <button
              type="button"
              className="small-action"
              disabled={!viewerHasSources}
              onClick={() => {
                if (!viewerHasSources) return;
                setViewerIndex((prev) => (prev - 1 + viewerSources.length) % viewerSources.length);
              }}
            >
              Fonte anterior
            </button>
            <button
              type="button"
              className="small-action"
              disabled={!viewerHasSources}
              onClick={() => {
                if (!viewerHasSources) return;
                setViewerIndex((prev) => (prev + 1) % viewerSources.length);
              }}
            >
              Proxima fonte
            </button>
          </div>
        </div>
        {viewerHasSources ? (
          <div className="viewer-source-list">
            {viewerSources.map((item, idx) => (
              <button
                key={`${item.sourceId}-${idx}`}
                type="button"
                className={`viewer-source-pill ${idx === viewerIndex ? "active" : ""}`}
                onClick={() => setViewerIndex(idx)}
              >
                {item.fileName} · p. {item.pageNumber}
              </button>
            ))}
          </div>
        ) : (
          <p className="viewer-empty-tip">Clique em uma citacao no chat para abrir a fonte.</p>
        )}
        {viewerError ? <p className="error-text viewer-error">{viewerError}</p> : null}
        <div className="viewer-body">
          <SourceViewer source={activeViewerSource} />
        </div>
      </div>
    );
  }

  const activeChat = useMemo(() => chats.find((item) => item.id === activeChatId) ?? null, [chats, activeChatId]);

  return (
    <div className="app-root">
      {mobileSidebarOpen ? (
        <button
          type="button"
          className="sidebar-backdrop mobile-only"
          aria-label="Fechar menu lateral"
          onClick={() => {
            setMobileSidebarOpen(false);
            setMenuChatId(null);
          }}
        />
      ) : null}

      <aside className={`sidebar-column ${mobileSidebarOpen ? "open" : ""}`}>
        <div className="sidebar-header">
          <h1>Spiritism RAG</h1>
          <button
            type="button"
            className="sidebar-close mobile-only"
            onClick={() => {
              setMobileSidebarOpen(false);
              setMenuChatId(null);
            }}
          >
            ✕
          </button>
        </div>

        <button
          type="button"
          className="new-chat-button"
          disabled={backendStatus !== "online"}
          onClick={() => {
            void createAndSelectNewChat();
          }}
        >
          + Novo chat
        </button>

        <div className="sidebar-upload">
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.txt,.md"
            multiple
            onChange={onFileInputChange}
            style={{ display: "none" }}
          />
          <button
            type="button"
            className="sidebar-action"
            disabled={backendStatus !== "online" || uploading}
            onClick={() => fileInputRef.current?.click()}
          >
            Adicionar arquivos
          </button>
          <button
            type="button"
            className="sidebar-action"
            disabled={backendStatus !== "online" || uploading}
            onClick={() => {
              void handleManualIngest();
            }}
          >
            Ingerir agora
          </button>
          {uploading ? <progress className="upload-progress" max={100} value={uploadProgress} /> : null}
          {uploadMessage ? <p className="sidebar-status">{uploadMessage}</p> : null}
          <p className="sidebar-status">Biblioteca: {docs.length} docs</p>
        </div>

        <div className="chat-list">
          {menuChatId ? (
            <button
              type="button"
              className="chat-menu-backdrop"
              aria-label="Fechar menu do chat"
              onClick={() => setMenuChatId(null)}
            />
          ) : null}
          {chats.map((chat) => (
            <article
              key={chat.id}
              className={`chat-list-item ${chat.id === activeChatId ? "active" : ""} ${menuChatId === chat.id ? "menu-open" : ""}`}
            >
              <button
                type="button"
                className="chat-select"
                onClick={() => {
                  void handleSelectChat(chat.id);
                }}
              >
                <span className="chat-title">{chat.title}</span>
                <span className="chat-date">{formatDate(chat.updated_at || chat.created_at)}</span>
              </button>
              <button
                type="button"
                className="chat-menu-button"
                onClick={(event) => {
                  event.stopPropagation();
                  setMenuChatId((prev) => (prev === chat.id ? null : chat.id));
                }}
              >
                ⋯
              </button>
              {menuChatId === chat.id ? (
                <div className="chat-menu" onClick={(event) => event.stopPropagation()}>
                  <button
                    type="button"
                    onClick={() => {
                      void handleRenameChat(chat);
                    }}
                  >
                    Renomear
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      void handleDeleteChat(chat);
                    }}
                  >
                    Apagar
                  </button>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      </aside>

      <section className="chat-column">
        <header className="chat-header">
          <div className="chat-header-left">
            <button type="button" className="hamburger mobile-only" onClick={() => setMobileSidebarOpen(true)}>
              ☰
            </button>
            <h2>{activeChat?.title || "Novo chat"}</h2>
          </div>
          <div className="chat-header-right">
            <span className="backend-pill">Backend: {API_BASE_URL}</span>
            <button type="button" className="small-action mobile-only" onClick={() => setMobileViewerOpen(true)}>
              Fontes
            </button>
          </div>
        </header>

        {backendStatus === "connecting" ? <p className="status-line">Conectando ao backend...</p> : null}
        {backendStatus === "offline" ? <p className="status-line error-text">Backend indisponivel.</p> : null}

        <div className="message-list" ref={messageListRef}>
          {!turns.length ? (
            <article className="assistant-card intro">
              <h3>Pergunte sobre os documentos locais</h3>
              <p>Respostas com citacoes clicaveis, sem internet.</p>
            </article>
          ) : null}
          {turns.map((turn) => {
            if (turn.role === "user") {
              return (
                <article key={turn.id} className="user-bubble">
                  {turn.text}
                </article>
              );
            }
            if (turn.error) {
              return (
                <article key={turn.id} className="assistant-card">
                  <h3>Erro</h3>
                  <p className="error-text">{turn.error}</p>
                </article>
              );
            }
            return (
              <div key={turn.id}>{turn.response ? <ChatMessage response={turn.response} onOpenSource={openSource} /> : null}</div>
            );
          })}
          {loading ? (
            <article className="assistant-card typing-card">
              <p>Digitando...</p>
            </article>
          ) : null}
        </div>

        <ChatInput disabled={loading || backendStatus !== "online"} onSend={handleSend} />
      </section>

      <aside className="viewer-column">{renderViewerPanel()}</aside>

      <div className={`viewer-modal ${mobileViewerOpen ? "open" : ""}`}>
        <div className="viewer-modal-backdrop" onClick={() => setMobileViewerOpen(false)} />
        <div className="viewer-modal-content">
          <div className="viewer-modal-header">
            <h2>Fontes</h2>
            <button type="button" className="small-action" onClick={() => setMobileViewerOpen(false)}>
              Fechar
            </button>
          </div>
          {renderViewerPanel()}
        </div>
      </div>
    </div>
  );
}
