/**
 * App shell: workspace selection, 5-primary-nav layout (Home / Discover /
 * Chat / Insights / Settings), lazy pages. JWT lives in localStorage only
 * for development; production auth flows via Supabase-hosted login page.
 */

import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, setToken, type Workspace } from "./lib/api";
import Home from "./pages/Home";
import Discover from "./pages/Discover";
import Chat from "./pages/Chat";
import Insights from "./pages/Insights";
import Settings from "./pages/Settings";

const NAV = [
  { to: "/", label: "Home", end: true },
  { to: "/discover", label: "Discover" },
  { to: "/chat", label: "Chat" },
  { to: "/insights", label: "Insights" },
  { to: "/settings", label: "Settings" },
];

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [current, setCurrent] = useState<string>(localStorage.getItem("scout_ws") ?? "");
  const [error, setError] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const list = await api.workspaces();
        setWorkspaces(list);
        if (list.length && !list.some((w) => w.id === current)) {
          localStorage.setItem("scout_ws", list[0].id);
          setCurrent(list[0].id);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "failed to load workspaces");
      } finally {
        setBooting(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const switchWorkspace = (id: string) => {
    localStorage.setItem("scout_ws", id);
    setCurrent(id);
  };

  const createWorkspace = async () => {
    const name = window.prompt("Workspace name");
    if (!name) return;
    const ws = await api.createWorkspace(name);
    setWorkspaces((prev) => [...prev, ws]);
    switchWorkspace(ws.id);
  };

  const logout = () => {
    setToken(null);
    window.location.reload();
  };

  if (booting) {
    return <div className="flex h-full items-center justify-center text-slate-500">Loading SCOUT…</div>;
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <div className="card max-w-md text-center">
          <h1 className="mb-2 text-lg font-semibold">Not signed in</h1>
          <p className="mb-4 text-sm text-slate-600">{error}</p>
          <input
            className="input mb-3"
            placeholder="Paste Supabase access token (dev mode)"
            onKeyDown={async (e) => {
              if (e.key === "Enter") {
                setToken((e.target as HTMLInputElement).value);
                window.location.reload();
              }
            }}
          />
          <button className="btn-primary w-full" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col border-r border-black/5 bg-white/70 backdrop-blur-xl">
        <div className="px-4 py-4">
          <span className="text-lg font-semibold tracking-tight text-brand-500">SCOUT</span>
        </div>
        <nav className="flex flex-1 flex-col gap-1 px-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? "bg-brand-500/10 text-brand-600" : "text-slate-600 hover:bg-black/5"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-black/5 p-3">
          <select
            className="input mb-2"
            value={current}
            onChange={(e) => switchWorkspace(e.target.value)}
          >
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
          <button className="btn-ghost w-full text-xs" onClick={createWorkspace}>
            + New workspace
          </button>
          <button className="btn-ghost w-full text-xs" onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="h-full flex-1 overflow-y-auto">
        <Routes>
          <Route path="/" element={<Home workspaceId={current} />} />
          <Route path="/discover" element={<Discover workspaceId={current} />} />
          <Route path="/chat" element={<Chat workspaceId={current} />} />
          <Route path="/insights" element={<Insights workspaceId={current} />} />
          <Route path="/settings" element={<Settings workspaceId={current} />} />
        </Routes>
      </main>
    </div>
  );
}
