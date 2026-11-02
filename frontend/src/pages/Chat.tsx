/** Chat: conversations sidebar, SSE streaming answers, per-message citation list. */

import { useEffect, useRef, useState } from "react";
import { api, streamChat, type Citation, type Conversation, type Document } from "../lib/api";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  task_type?: string | null;
  citations?: Citation[];
  streaming?: boolean;
}

export default function Chat({ workspaceId }: { workspaceId: string }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [docs, setDocs] = useState<Document[]>([]);
  const [selectedDocs, setSelectedDocs] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!workspaceId) return;
    api.conversations(workspaceId).then(setConversations).catch(() => setConversations([]));
    api
      .documents(workspaceId, { status: "ready", page_size: "100" })
      .then((page) => setDocs(page.items))
      .catch(() => setDocs([]));
  }, [workspaceId]);

  useEffect(() => {
    if (!current) {
      setMessages([]);
      return;
    }
    api
      .messages(current)
      .then((list) =>
        setMessages(
          list.map((m) => ({
            id: m.id,
            role: m.role as "user" | "assistant",
            content: m.content,
            task_type: m.task_type,
            citations: m.citations,
          })),
        ),
      )
      .catch(() => setMessages([]));
  }, [current]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    setError(null);
    setBusy(true);
    const userMsg: ChatMessage = { id: crypto.randomUUID(), role: "user", content: text };
    const assistantId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      userMsg,
      { id: assistantId, role: "assistant", content: "", streaming: true },
    ]);
    setInput("");
    try {
      await streamChat(workspaceId, text, selectedDocs, current, {
        onDelta: (chunk) =>
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + chunk } : m)),
          ),
        onDone: (payload) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, streaming: false, task_type: payload.task_type, citations: payload.citations }
                : m,
            ),
          );
          if (!current) setCurrent(payload.conversation_id);
          api.conversations(workspaceId).then(setConversations).catch(() => undefined);
        },
        onError: (message) => {
          setError(message);
          setMessages((prev) => prev.filter((m) => m.id !== assistantId));
        },
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full">
      <div className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center justify-between px-3 py-3">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Conversations</span>
          <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setCurrent(null)}>
            + New
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-2">
          {conversations.map((c) => (
            <div
              key={c.id}
              className={`group mb-1 flex items-center justify-between rounded-md px-2 py-1.5 text-sm ${
                current === c.id ? "bg-brand-50 text-brand-700" : "hover:bg-slate-100"
              }`}
            >
              <button className="min-w-0 flex-1 truncate text-left" onClick={() => setCurrent(c.id)}>
                {c.title ?? "Untitled"}
              </button>
              <button
                className="ml-1 hidden text-xs text-rose-500 group-hover:block"
                onClick={() => {
                  void api.deleteConversation(c.id).then(() => {
                    if (current === c.id) setCurrent(null);
                    setConversations((prev) => prev.filter((x) => x.id !== c.id));
                  });
                }}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="border-b border-slate-200 bg-white px-4 py-2">
          <select
            className="input text-xs"
            value={selectedDocs.join(",")}
            onChange={(e) => setSelectedDocs(e.target.value ? e.target.value.split(",") : [])}
          >
            <option value="">All documents in workspace</option>
            {docs.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title}
              </option>
            ))}
          </select>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {messages.map((m) => (
            <div key={m.id} className={`mb-4 ${m.role === "user" ? "text-right" : ""}`}>
              <div
                className={`inline-block max-w-[85%] whitespace-pre-wrap rounded-lg px-4 py-2 text-sm ${
                  m.role === "user" ? "bg-brand-600 text-white" : "bg-white shadow-sm border border-slate-200"
                }`}
              >
                {m.content || (m.streaming ? "…" : "")}
                {m.streaming && <span className="ml-1 animate-pulse">▌</span>}
              </div>
              {m.task_type && m.role === "assistant" && (
                <div className="mt-1 text-left text-[10px] uppercase tracking-wide text-slate-400">
                  task: {m.task_type}
                </div>
              )}
              {m.citations && m.citations.length > 0 && <CitationList citations={m.citations} />}
            </div>
          ))}
          <div ref={bottom} />
        </div>

        {error && (
          <div className="mx-6 mb-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
            {error}
          </div>
        )}

        <form
          className="border-t border-slate-200 bg-white p-4"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <div className="flex gap-2">
            <input
              className="input"
              placeholder="Ask about your documents…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={busy}
            />
            <button className="btn-primary" disabled={busy || !input.trim()}>
              {busy ? "Thinking…" : "Send"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function CitationList({ citations }: { citations: Citation[] }) {
  return (
    <div className="mt-2 max-w-[85%] text-left">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Citations</div>
      <ul className="mt-1 space-y-1">
        {citations.map((c, i) => (
          <li
            key={`${c.evidence_id}-${i}`}
            className={`chip ${
              c.valid ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-700"
            }`}
            title={c.rejection_reason ?? c.quote ?? ""}
          >
            [E{c.evidence_id}] {c.document_title ?? "unknown"}
            {c.page ? ` p.${c.page}` : ""}
            {c.sheet ? ` · ${c.sheet}` : ""}
            {c.valid ? "" : " — rejected"}
          </li>
        ))}
      </ul>
    </div>
  );
}
