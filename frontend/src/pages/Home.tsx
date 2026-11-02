/** Home: quick question box, recent documents and conversations, digest stats. */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Conversation, type Document } from "../lib/api";

export default function Home({ workspaceId }: { workspaceId: string }) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [quickQuestion, setQuickQuestion] = useState("");

  useEffect(() => {
    if (!workspaceId) return;
    api
      .recent(workspaceId)
      .then((data) => {
        setDocuments(data.documents ?? []);
        setConversations(data.conversations ?? []);
      })
      .catch(() => {
        setDocuments([]);
        setConversations([]);
      });
  }, [workspaceId]);

  const ready = documents.filter((d) => d.status === "ready").length;
  const processing = documents.filter((d) => !["ready", "failed", "deleted"].includes(d.status)).length;

  return (
    <div className="mx-auto max-w-4xl p-8">
      <h1 className="mb-1 text-2xl font-bold">Home</h1>
      <p className="mb-6 text-sm text-slate-500">
        Ask anything about your documents — SCOUT answers only from your files, with verifiable citations.
      </p>

      <div className="card mb-8">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!quickQuestion.trim()) return;
            window.location.hash = `#/chat?q=${encodeURIComponent(quickQuestion)}`;
          }}
        >
          <input
            className="input"
            placeholder="Ask a question about your documents…"
            value={quickQuestion}
            onChange={(e) => setQuickQuestion(e.target.value)}
          />
        </form>
      </div>

      <div className="mb-8 grid grid-cols-3 gap-4">
        <div className="card">
          <div className="text-xs uppercase tracking-wide text-slate-400">Documents</div>
          <div className="text-2xl font-semibold">{documents.length}</div>
        </div>
        <div className="card">
          <div className="text-xs uppercase tracking-wide text-slate-400">Ready</div>
          <div className="text-2xl font-semibold">{ready}</div>
        </div>
        <div className="card">
          <div className="text-xs uppercase tracking-wide text-slate-400">Processing</div>
          <div className="text-2xl font-semibold">{processing}</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <div>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Recent documents</h2>
          <ul className="space-y-2">
            {documents.map((d) => (
              <li key={d.id} className="card flex items-center justify-between py-2">
                <Link to="/discover" className="truncate pr-2 text-sm font-medium hover:text-brand-600">
                  {d.title}
                </Link>
                <StatusChip status={d.status} />
              </li>
            ))}
            {!documents.length && <li className="text-sm text-slate-400">No documents yet.</li>}
          </ul>
        </div>
        <div>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">Recent conversations</h2>
          <ul className="space-y-2">
            {conversations.map((c) => (
              <li key={c.id} className="card py-2">
                <Link to="/chat" className="text-sm font-medium hover:text-brand-600">
                  {c.title ?? "Untitled conversation"}
                </Link>
              </li>
            ))}
            {!conversations.length && <li className="text-sm text-slate-400">No conversations yet.</li>}
          </ul>
        </div>
      </div>
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const styles: Record<string, string> = {
    ready: "bg-emerald-100 text-emerald-700",
    failed: "bg-rose-100 text-rose-700",
    dead: "bg-rose-100 text-rose-700",
    processing: "bg-amber-100 text-amber-700",
    queued: "bg-amber-100 text-amber-700",
    pending: "bg-slate-200 text-slate-600",
  };
  return <span className={`chip ${styles[status] ?? "bg-slate-100 text-slate-600"}`}>{status}</span>;
}
