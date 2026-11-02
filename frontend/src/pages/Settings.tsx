/** Settings: account info, workspace details, usage metering. */

import { useEffect, useState } from "react";
import { api, type Workspace } from "../lib/api";

export default function Settings({ workspaceId }: { workspaceId: string }) {
  const [me, setMe] = useState<Record<string, unknown> | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [usage, setUsage] = useState<Array<{ kind: string; month: string; units: number }>>([]);

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
    api.workspaces().then(setWorkspaces).catch(() => setWorkspaces([]));
    if (workspaceId) api.usage(workspaceId).then(setUsage).catch(() => setUsage([]));
  }, [workspaceId]);

  const current = workspaces.find((w) => w.id === workspaceId);

  return (
    <div className="mx-auto max-w-3xl p-8">
      <h1 className="mb-6 text-2xl font-bold">Settings</h1>

      <div className="card mb-6">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Account</h2>
        <dl className="space-y-1 text-sm">
          {Object.entries(me ?? {}).map(([k, v]) => (
            <div key={k} className="flex justify-between">
              <dt className="text-slate-500">{k}</dt>
              <dd className="font-medium">{String(v)}</dd>
            </div>
          ))}
          {!me && <div className="text-slate-400">Not signed in.</div>}
        </dl>
      </div>

      <div className="card mb-6">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Current workspace</h2>
        {current ? (
          <dl className="space-y-1 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-500">Name</dt>
              <dd className="font-medium">{current.name}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Slug</dt>
              <dd className="font-mono text-xs">{current.slug}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Created</dt>
              <dd className="font-medium">{new Date(current.created_at).toLocaleString()}</dd>
            </div>
          </dl>
        ) : (
          <div className="text-sm text-slate-400">No workspace selected.</div>
        )}
      </div>

      <div className="card">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Usage this workspace</h2>
        {usage.length ? (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left">
                <th className="py-1 pr-4 font-medium">Month</th>
                <th className="py-1 pr-4 font-medium">Kind</th>
                <th className="py-1 font-medium">Units</th>
              </tr>
            </thead>
            <tbody>
              {usage.map((u, i) => (
                <tr key={i} className="border-b border-slate-100">
                  <td className="py-1 pr-4">{u.month}</td>
                  <td className="py-1 pr-4">{u.kind}</td>
                  <td className="py-1">{u.units}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="text-sm text-slate-400">No usage recorded yet.</div>
        )}
      </div>
    </div>
  );
}
