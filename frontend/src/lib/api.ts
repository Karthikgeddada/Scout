/** Typed API client + shared domain types + SSE streaming helper. */

export const API_BASE = import.meta.env.VITE_API_URL ?? "";

// ---------- types ----------

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  created_at: string;
}

export interface Document {
  id: string;
  workspace_id: string;
  title: string;
  source_filename: string;
  fmt: string;
  status: string;
  version: number;
  collection: string | null;
  created_at: string;
  updated_at: string;
}

export interface Conversation {
  id: string;
  workspace_id: string;
  title: string | null;
  mode: string;
  document_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface Citation {
  evidence_id: number;
  document_id: string | null;
  document_title: string | null;
  page: number | null;
  sheet: string | null;
  locator: string | null;
  quote: string | null;
  valid: boolean;
  rejection_reason: string | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: string;
  content: string;
  task_type: string | null;
  citations: Citation[];
  created_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface IntelligenceResult {
  answer: string;
  task_type: string;
  citations: Citation[];
  evidence: Array<{ evidence_id: number; document_title: string; pages: number[]; sheet: string | null }>;
  computed: unknown;
}

export interface AnalyticsResult {
  document_id: string;
  sheet: string | null;
  rows: Array<Record<string, string | number>>;
  provenance: { sheet: string | null; source_rows: number[] };
  plan_summary: Record<string, unknown>;
  explanation: string | null;
}

export interface Report {
  id: string;
  kind: string;
  title: string;
  status: string;
  format: string | null;
  citation_count: number;
  created_at: string;
}

// ---------- auth token ----------

let accessToken: string | null = localStorage.getItem("scout_token");

export function setToken(token: string | null): void {
  accessToken = token;
  if (token) localStorage.setItem("scout_token", token);
  else localStorage.removeItem("scout_token");
}

export class ApiError extends Error {
  code: string;
  requestId: string | null;
  constructor(code: string, message: string, requestId: string | null) {
    super(message);
    this.code = code;
    this.requestId = requestId;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init.headers,
    },
  });
  if (!res.ok) {
    let code = "http_error";
    let message = `HTTP ${res.status}`;
    let requestId: string | null = null;
    try {
      const body = await res.json();
      if (body?.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
        requestId = body.error.request_id ?? null;
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(code, message, requestId);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  me: () => request<Record<string, unknown>>("/v1/users/me"),
  workspaces: () => request<Workspace[]>("/v1/workspaces"),
  createWorkspace: (name: string) =>
    request<Workspace>("/v1/workspaces", { method: "POST", body: JSON.stringify({ name }) }),

  documents: (ws: string, params: Record<string, string> = {}) => {
    const q = new URLSearchParams({ workspace_id: ws, ...params }).toString();
    return request<Page<Document>>(`/v1/documents?${q}`);
  },
  recent: (ws: string) =>
    request<{ documents: Document[]; conversations: Conversation[] }>(
      `/v1/home/recent?workspace_id=${ws}`,
    ),
  requestUploadUrl: (ws: string, filename: string, sizeBytes: number, mime?: string, collection?: string) =>
    request<{ document_id: string; upload_url: string; storage_path: string; expires_in: number }>(
      "/v1/documents/upload-url",
      {
        method: "POST",
        body: JSON.stringify({ workspace_id: ws, filename, size_bytes: sizeBytes, mime, collection }),
      },
    ),
  completeUpload: (documentId: string) =>
    request<Document>("/v1/documents/complete-upload", {
      method: "POST",
      body: JSON.stringify({ document_id: documentId }),
    }),
  uploadFile: (uploadUrl: string, file: File) =>
    fetch(uploadUrl, { method: "PUT", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } }),
  documentStatus: (id: string) =>
    request<{ document_id: string; status: string; stage: string | null; progress: number; error: string | null }>(
      `/v1/documents/${id}/status`,
    ),
  deleteDocument: (id: string) => request<void>(`/v1/documents/${id}`, { method: "DELETE" }),
  reprocess: (id: string) =>
    request<Document>(`/v1/documents/${id}/reprocess`, { method: "POST" }),

  conversations: (ws: string) =>
    request<Conversation[]>(`/v1/conversations?workspace_id=${ws}`),
  messages: (convId: string) => request<Message[]>(`/v1/conversations/${convId}/messages`),
  deleteConversation: (convId: string) => request<void>(`/v1/conversations/${convId}`, { method: "DELETE" }),

  ask: (ws: string, query: string, documentIds: string[]) =>
    request<IntelligenceResult>("/v1/intelligence/ask", {
      method: "POST",
      body: JSON.stringify({ workspace_id: ws, query, document_ids: documentIds }),
    }),
  summarize: (ws: string, documentIds: string[], style: string) =>
    request<IntelligenceResult>("/v1/intelligence/summarize", {
      method: "POST",
      body: JSON.stringify({ workspace_id: ws, document_ids: documentIds, style }),
    }),
  extract: (ws: string, documentIds: string[], fields: string[]) =>
    request<IntelligenceResult>("/v1/intelligence/extract", {
      method: "POST",
      body: JSON.stringify({ workspace_id: ws, document_ids: documentIds, fields }),
    }),
  compare: (ws: string, documentIds: string[], focus: string) =>
    request<IntelligenceResult>("/v1/intelligence/compare", {
      method: "POST",
      body: JSON.stringify({ workspace_id: ws, document_ids: documentIds, focus }),
    }),

  analytics: (ws: string, documentId: string, query: string) =>
    request<AnalyticsResult>("/v1/analytics/query", {
      method: "POST",
      body: JSON.stringify({ workspace_id: ws, document_id: documentId, query }),
    }),

  createReport: (ws: string, title: string, documentIds: string[], brief: string, format: string) =>
    request<Report>("/v1/reports", {
      method: "POST",
      body: JSON.stringify({ workspace_id: ws, title, document_ids: documentIds, brief, format }),
    }),
  reports: (ws: string) => request<Report[]>(`/v1/reports?workspace_id=${ws}`),
  reportDownload: (id: string) => request<{ url: string }>(`/v1/reports/${id}/download`),

  usage: (ws: string) =>
    request<Array<{ kind: string; month: string; units: number }>>(`/v1/usage?workspace_id=${ws}`),
};

// ---------- SSE streaming chat ----------

export interface StreamHandlers {
  onDelta: (text: string) => void;
  onDone: (payload: { message_id: string; conversation_id: string; task_type: string; citations: Citation[] }) => void;
  onError: (message: string) => void;
}

export async function streamChat(
  workspaceId: string,
  query: string,
  documentIds: string[],
  conversationId: string | null,
  handlers: StreamHandlers,
): Promise<void> {
  const res = await fetch(`${API_BASE}/v1/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    body: JSON.stringify({
      workspace_id: workspaceId,
      conversation_id: conversationId,
      document_ids: documentIds,
      query,
    }),
  });
  if (!res.ok || !res.body) {
    handlers.onError(`stream failed (HTTP ${res.status})`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const raw of events) {
      const type = /^event: (.+)$/m.exec(raw)?.[1];
      const data = /^data: (.+)$/m.exec(raw)?.[1];
      if (!type || !data) continue;
      const payload = JSON.parse(data);
      if (type === "delta") handlers.onDelta(payload.text);
      else if (type === "done") handlers.onDone(payload);
      else if (type === "error") handlers.onError(payload.message);
    }
  }
}
