/** Insights: summarize / extract / compare / analytics over selected documents + reports. */

import { useEffect, useState } from "react";
import { api, type AnalyticsResult, type Document, type IntelligenceResult, type Report } from "../lib/api";

type Mode = "summarize" | "extract" | "compare" | "analytics";

export default function Insights({ workspaceId }: { workspaceId: string }) {
  const [docs, setDocs] = useState<Document[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>("summarize");
  const [result, setResult] = useState<IntelligenceResult | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsResult | null>(null);
  const [query, setQuery] = useState("");
  const [focus, setFocus] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [reportTitle, setReportTitle] = useState("");
  const [reportBrief, setReportBrief] = useState("");

  const load = () => {
    if (!workspaceId) return;
    api.documents(workspaceId, { status: "ready", page_size: "100" }).then((p) => setDocs(p.items)).catch(() => setDocs([]));
    api.reports(workspaceId).then(setReports).catch(() => setReports([]));
  };

  useEffect(load, [workspaceId]);

  const toggleDoc = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const run = async () => {
    setError(null);
    setResult(null);
    setAnalytics(null);
    setBusy(true);
    try {
      if (mode === "summarize") {
        setResult(await api.summarize(workspaceId, selected, "overview"));
      } else if (mode === "extract") {
        setResult(await api.extract(workspaceId, selected, ["names", "dates", "amounts", "organizations"]));
      } else if (mode === "compare") {
        setResult(await api.compare(workspaceId, selected, focus));
      } else {
        if (selected.length !== 1) throw new Error("analytics needs exactly one document");
        if (!query.trim()) throw new Error("enter an analytics question");
        setAnalytics(await api.analytics(workspaceId, selected[0], query));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
    } finally {
      setBusy(false);
    }
  };

  const generateReport = async () => {
    if (!reportTitle.trim() || !selected.length) return;
    setBusy(true);
    setError(null);
    try {
      await api.createReport(workspaceId, reportTitle, selected, reportBrief, "pdf");
      setReportTitle("");
      setReportBrief("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "report failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl p-8">
      <h1 className="mb-1 text-2xl font-bold">Insights</h1>
      <p className="mb-6 text-sm text-slate-500">
        Summaries, field extraction, multi-document comparison and deterministic table analytics — every output cites its sources.
      </p>

      <div className="card mb-6">
        <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">Select documents</div>
        <div className="grid grid-cols-2 gap-1">
          {docs.map((d) => (
            <label key={d.id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={selected.includes(d.id)} onChange={() => toggleDoc(d.id)} />
              <span className="truncate">{d.title}</span>
            </label>
          ))}
          {!docs.length && <div className="text-sm text-slate-400">No ready documents.</div>}
        </div>
      </div>

      <div className="mb-4 flex gap-2">
        {(["summarize", "extract", "compare", "analytics"] as Mode[]).map((m) => (
          <button
            key={m}
            className={mode === m ? "btn-primary" : "btn-ghost"}
            onClick={() => setMode(m)}
          >
            {m}
          </button>
        ))}
        <button className="btn-primary ml-auto" onClick={() => void run()} disabled={busy || !selected.length}>
          {busy ? "Working…" : "Run"}
        </button>
      </div>

      {mode === "analytics" && (
        <input
          className="input mb-4"
          placeholder="e.g. total revenue by region for 2023"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      )}
      {mode === "compare" && (
        <input
          className="input mb-4"
          placeholder="Focus (optional): what to compare"
          value={focus}
          onChange={(e) => setFocus(e.target.value)}
        />
      )}

      {error && (
        <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>
      )}

      {result && (
        <div className="card mb-6">
          <div className="whitespace-pre-wrap text-sm">{result.answer}</div>
          {result.citations.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1">
              {result.citations.map((c, i) => (
                <span key={i} className="chip bg-emerald-50 text-emerald-800">
                  [E{c.evidence_id}] {c.document_title ?? "?"}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {analytics && (
        <div className="card mb-6">
          <div className="mb-2 text-xs uppercase tracking-wide text-slate-400">Sheet: {analytics.sheet}</div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left">
                {Object.keys(analytics.rows[0] ?? {}).map((col) => (
                  <th key={col} className="py-1 pr-4 font-medium">{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {analytics.rows.map((row, i) => (
                <tr key={i} className="border-b border-slate-100">
                  {Object.values(row).map((v, j) => (
                    <td key={j} className="py-1 pr-4">{String(v)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {analytics.explanation && (
            <p className="mt-3 text-sm text-slate-600">{analytics.explanation}</p>
          )}
        </div>
      )}

      <div className="card">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Reports (PDF/DOCX, cited)</div>
        <input className="input mb-2" placeholder="Report title" value={reportTitle} onChange={(e) => setReportTitle(e.target.value)} />
        <textarea
          className="input mb-2"
          rows={2}
          placeholder="Optional brief / focus for the report"
          value={reportBrief}
          onChange={(e) => setReportBrief(e.target.value)}
        />
        <button className="btn-primary" disabled={busy || !reportTitle.trim() || !selected.length} onClick={() => void generateReport()}>
          Generate report
        </button>
        <ul className="mt-4 space-y-1">
          {reports.map((r) => (
            <li key={r.id} className="flex items-center justify-between text-sm">
              <span className="truncate">{r.title}</span>
              <span className="flex items-center gap-2">
                <span className="chip bg-slate-100 text-slate-600">{r.citation_count} cites</span>
                <button
                  className="btn-ghost px-2 py-1 text-xs"
                  onClick={() =>
                    api.reportDownload(r.id).then((d) => window.open(d.url, "_blank"))
                  }
                >
                  Download
                </button>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
