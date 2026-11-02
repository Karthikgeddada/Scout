/** Discover: upload (signed URL flow) + document library + search + status polling. */

import { useEffect, useRef, useState } from "react";
import { api, type Document } from "../lib/api";

export default function Discover({ workspaceId }: { workspaceId: string }) {
  const [docs, setDocs] = useState<Document[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [uploading, setUploading] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = (search = q) => {
    if (!workspaceId) return;
    api
      .documents(workspaceId, search ? { q: search } : {})
      .then((page) => {
        setDocs(page.items);
        setTotal(page.total);
      })
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    load("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  // Poll processing documents every 3s until everything settles.
  useEffect(() => {
    const pending = docs.some((d) => !["ready", "failed"].includes(d.status));
    if (!pending) return;
    const timer = setTimeout(() => load(), 3000);
    return () => clearTimeout(timer);
  }, [docs, workspaceId]);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setError(null);
    for (const file of Array.from(files)) {
      const key = `${file.name}:${file.size}`;
      try {
        setUploading((prev) => ({ ...prev, [key]: 10 }));
        const { document_id, upload_url } = await api.requestUploadUrl(
          workspaceId,
          file.name,
          file.size,
          file.type,
        );
        setUploading((prev) => ({ ...prev, [key]: 40 }));
        const res = await api.uploadFile(upload_url, file);
        if (!res.ok) throw new Error(`storage upload failed (${res.status})`);
        setUploading((prev) => ({ ...prev, [key]: 70 }));
        await api.completeUpload(document_id);
        setUploading((prev) => ({ ...prev, [key]: 100 }));
      } catch (e) {
        setError(e instanceof Error ? e.message : "upload failed");
      } finally {
        setTimeout(() => setUploading((prev) => {
            const next = { ...prev };
            delete next[key];
            return next;
          }), 1500);
        load();
      }
    }
  };

  return (
    <div className="mx-auto max-w-4xl p-8">
      <h1 className="mb-1 text-2xl font-bold">Discover</h1>
      <p className="mb-6 text-sm text-slate-500">
        Upload documents (PDF, DOCX, XLSX, CSV, PPTX, images, TXT, MD) — they are parsed, indexed and become queryable.
      </p>

      <div
        className="card mb-6 border-dashed border-slate-300 py-10 text-center"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          void upload(e.dataTransfer.files);
        }}
      >
        <p className="mb-3 text-sm text-slate-500">Drag & drop files here, or</p>
        <button className="btn-primary" onClick={() => fileInput.current?.click()}>
          Choose files
        </button>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            void upload(e.target.files);
            e.target.value = "";
          }}
        />
        {Object.entries(uploading).map(([key, pct]) => (
          <div key={key} className="mx-auto mt-4 max-w-xs text-left text-xs text-slate-500">
            {key.split(":")[0]}
            <div className="h-1.5 w-full rounded bg-slate-200">
              <div className="h-1.5 rounded bg-brand-500 transition-all" style={{ width: `${pct}%` }} />
            </div>
          </div>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}

      <form
        className="mb-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          load();
        }}
      >
        <input
          className="input"
          placeholder="Search documents by title or filename…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button className="btn-ghost">Search</button>
      </form>

      <ul className="space-y-2">
        {docs.map((doc) => (
          <li key={doc.id} className="card flex items-center justify-between gap-4">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{doc.title}</div>
              <div className="text-xs text-slate-400">
                {doc.fmt.toUpperCase()} · v{doc.version} · {new Date(doc.created_at).toLocaleString()}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <StatusChip status={doc.status} />
              {doc.status === "ready" && (
                <button className="btn-ghost text-xs" onClick={() => void api.reprocess(doc.id).then(() => load())}>
                  Reprocess
                </button>
              )}
              <button
                className="btn-ghost text-xs text-rose-600"
                onClick={() => {
                  if (window.confirm(`Delete "${doc.title}"?`)) {
                    void api.deleteDocument(doc.id).then(() => load());
                  }
                }}
              >
                Delete
              </button>
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-4 text-xs text-slate-400">
        {total} document{total === 1 ? "" : "s"}
      </p>
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const styles: Record<string, string> = {
    ready: "bg-emerald-100 text-emerald-700",
    failed: "bg-rose-100 text-rose-700",
    processing: "bg-amber-100 text-amber-700",
    queued: "bg-amber-100 text-amber-700",
    pending: "bg-slate-200 text-slate-600",
  };
  return <span className={`chip ${styles[status] ?? "bg-slate-100 text-slate-600"}`}>{status}</span>;
}
