/**
 * App shell: workspace selection, 5-primary-nav layout (Home / Discover /
 * Chat / Insights / Settings), lazy pages. Auth via Supabase email/password
 * sign-in; the access token is handed to the API client (localStorage-backed).
 */

import { useEffect, useState, type FormEvent } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, setToken, type Workspace } from "./lib/api";
import { supabase } from "./lib/supabase";
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
  const [authed, setAuthed] = useState(false);
  const [booting, setBooting] = useState(true);

  // Keep the API client's bearer token in sync with the Supabase session
  // (covers automatic refresh when the 1-hour access token expires).
  useEffect(() => {
    const { data: sub } = supabase.auth.onAuthStateChange((event, s) => {
      if (s && (event === "SIGNED_IN" || event === "TOKEN_REFRESHED")) setToken(s.access_token);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    (async () => {
      const { data } = await supabase.auth.getSession();
      const session = data.session;
      if (!session) {
        setBooting(false);
        return;
      }
      setToken(session.access_token);
      setAuthed(true);
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

  const logout = async () => {
    await supabase.auth.signOut();
    setToken(null);
    window.location.reload();
  };

  if (booting) {
    return <div className="flex h-full items-center justify-center text-slate-500">Loading SCOUT…</div>;
  }

  if (!booting && !authed) {
    return <SignInScreen />;
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <div className="card max-w-md text-center">
          <h1 className="mb-2 text-lg font-semibold">Something went wrong</h1>
          <p className="mb-4 text-sm text-slate-600">{error}</p>
          <div className="flex gap-2">
            <button className="btn-primary flex-1" onClick={() => window.location.reload()}>
              Retry
            </button>
            <button className="btn-ghost" onClick={logout}>
              Sign out
            </button>
          </div>
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

/** Email/password sign-in against Supabase Auth. */
function SignInScreen() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [showToken, setShowToken] = useState(false);
  const [tokenInput, setTokenInput] = useState("");

  const signIn = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setAuthError(null);
    const { error } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });
    if (error) {
      setAuthError(error.message);
      setBusy(false);
      return;
    }
    const { data } = await supabase.auth.getSession();
    if (data.session) {
      setToken(data.session.access_token);
      window.location.reload();
    } else {
      setAuthError("Signed in, but no session was returned. Try again.");
      setBusy(false);
    }
  };

  const useToken = () => {
    if (!tokenInput.trim()) return;
    setToken(tokenInput.trim());
    window.location.reload();
  };

  return (
    <div className="flex h-full items-center justify-center">
      <form onSubmit={signIn} className="card w-full max-w-sm">
        <h1 className="mb-1 text-lg font-semibold">Sign in to SCOUT</h1>
        <p className="mb-4 text-sm text-slate-600">Use your account email and password.</p>
        <input
          className="input mb-3"
          type="email"
          placeholder="Email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          className="input mb-3"
          type="password"
          placeholder="Password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {authError && <p className="mb-3 text-sm text-red-600">{authError}</p>}
        <button
          className="btn-primary w-full"
          type="submit"
          disabled={busy || !email.trim() || !password}
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <button
          type="button"
          className="btn-ghost mt-2 w-full text-xs"
          onClick={() => setShowToken((v) => !v)}
        >
          {showToken ? "Hide token input" : "Paste token instead (dev)"}
        </button>
        {showToken && (
          <>
            <input
              className="input mt-2"
              placeholder="Paste Supabase access token"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
            />
            <button
              type="button"
              className="btn-primary mt-2 w-full"
              onClick={useToken}
              disabled={!tokenInput.trim()}
            >
              Use token
            </button>
          </>
        )}
      </form>
    </div>
  );
}
