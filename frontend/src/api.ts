import type {
  ChatCreateRequest,
  ChatListItem,
  ChatRequest,
  ChatMessagesResponse,
  ChatResponse,
  ChatsResponse,
  DocItem,
  DocsResponse,
  HighlightResponse,
  IngestStatusResponse,
  UploadResponse
} from "./types";

function resolveApiBaseUrl(): string {
  const configured = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim();
  if (configured) return configured.replace(/\/+$/, "");
  if (typeof window !== "undefined") {
    const { protocol, hostname, port } = window.location;
    if (port === "5173") return `${protocol}//${hostname}:8000`;
    return `${protocol}//${hostname}${port ? `:${port}` : ""}`;
  }
  return "http://127.0.0.1:8000";
}

const API_BASE_URL = resolveApiBaseUrl();

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status} - ${text || response.statusText}`);
  }
  return (await response.json()) as T;
}

export async function postChat(payload: ChatRequest): Promise<ChatResponse> {
  return http<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function getChats(): Promise<ChatsResponse> {
  return http<ChatsResponse>("/chats");
}

export async function createChat(payload?: ChatCreateRequest): Promise<ChatListItem> {
  return http<ChatListItem>("/chats", {
    method: "POST",
    body: JSON.stringify(payload ?? {})
  });
}

export async function renameChat(chatId: string, title: string): Promise<ChatListItem> {
  return http<ChatListItem>(`/chats/${encodeURIComponent(chatId)}`, {
    method: "PATCH",
    body: JSON.stringify({ title })
  });
}

export async function deleteChat(chatId: string): Promise<{ ok: boolean }> {
  return http<{ ok: boolean }>(`/chats/${encodeURIComponent(chatId)}`, {
    method: "DELETE"
  });
}

export async function getChatMessages(chatId: string): Promise<ChatMessagesResponse> {
  return http<ChatMessagesResponse>(`/chats/${encodeURIComponent(chatId)}/messages`);
}

export async function postChatMessage(chatId: string, question: string): Promise<ChatResponse> {
  return http<ChatResponse>(`/chats/${encodeURIComponent(chatId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ question })
  });
}

export async function getDocs(): Promise<DocsResponse> {
  return http<DocsResponse>("/docs");
}

export async function updateDoc(docId: string, isEnabled: boolean): Promise<DocItem> {
  return http<DocItem>(`/docs/${encodeURIComponent(docId)}`, {
    method: "PATCH",
    body: JSON.stringify({ is_enabled: isEnabled })
  });
}

export async function deleteDoc(docId: string): Promise<{ ok: boolean }> {
  return http<{ ok: boolean }>(`/docs/${encodeURIComponent(docId)}`, {
    method: "DELETE"
  });
}

export async function startIngest(docsPath = "docs"): Promise<{ job_id: string; status: string }> {
  return http<{ job_id: string; status: string }>("/ingest", {
    method: "POST",
    body: JSON.stringify({ docs_path: docsPath })
  });
}

export async function getIngestJob(jobId: string): Promise<IngestStatusResponse> {
  return http<IngestStatusResponse>(`/ingest/${encodeURIComponent(jobId)}`);
}

export async function getHealth(): Promise<{ status: string }> {
  return http<{ status: string }>("/health");
}

export async function getHighlight(sourceId: string, snippet?: string): Promise<HighlightResponse> {
  const base = `/highlights/${encodeURIComponent(sourceId)}`;
  if (!snippet?.trim()) {
    return http<HighlightResponse>(base);
  }
  const query = new URLSearchParams({ snippet });
  return http<HighlightResponse>(`${base}?${query.toString()}`);
}

export function getPdfUrl(docId: string): string {
  return `${API_BASE_URL}/docs/${encodeURIComponent(docId)}/pdf`;
}

export async function getPageText(docId: string, pageNumber: number): Promise<{ text: string }> {
  return http<{ text: string }>(`/docs/${encodeURIComponent(docId)}/page/${pageNumber}/text`);
}

export function uploadDocument(
  file: File,
  onProgress?: (progressPercent: number) => void
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE_URL}/upload`);

    xhr.upload.onprogress = (event) => {
      if (!onProgress) return;
      if (!event.lengthComputable) {
        onProgress(0);
        return;
      }
      const progress = Math.round((event.loaded / event.total) * 100);
      onProgress(progress);
    };

    xhr.onload = () => {
      const body = xhr.responseText || "";
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(body) as UploadResponse);
        } catch {
          reject(new Error("Resposta invalida do servidor no upload."));
        }
        return;
      }
      let message = body.trim() || xhr.statusText || "Falha no upload.";
      try {
        const parsed = JSON.parse(body) as { detail?: string };
        if (parsed?.detail) {
          message = parsed.detail;
        }
      } catch {
        // Keep plain text message.
      }
      reject(new Error(`HTTP ${xhr.status} - ${message}`));
    };

    xhr.onerror = () => reject(new Error("Falha de rede durante upload."));
    xhr.onabort = () => reject(new Error("Upload cancelado."));
    xhr.send(formData);
  });
}

export { API_BASE_URL };
