import { type FormEvent, useEffect, useState } from "react";
import { Navigate } from "react-router";
import { LogIn } from "lucide-react";
import { apiFetch } from "@/api/client";
import { fetchProviders, providerLabel } from "@/api/providers";
import { startLogin, useAuth } from "@/auth/session";
import { hardNavigate } from "@/lib/navigation";

export default function LoginPage() {
  const auth = useAuth();
  const [providers, setProviders] = useState<string[] | null>(null);
  const [devLogin, setDevLogin] = useState(false);
  const [devEmail, setDevEmail] = useState("dev@localhost");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchProviders()
      .then((r) => {
        setProviders(r.providers);
        setDevLogin(r.dev_login);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  async function submitDevLogin(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/auth/dev-login", {
        method: "POST",
        body: JSON.stringify({ email: devEmail }),
      });
      // Full reload rather than a router navigate: the session cookie is
      // set on this response, and a reload is the simplest way to make
      // every cached query re-run with it attached.
      hardNavigate("/library");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  if (auth.status === "authenticated") return <Navigate to="/library" replace />;

  return (
    <div className="flex h-screen items-center justify-center px-4">
      <div
        className="w-full max-w-sm rounded-lg border p-8 shadow-sm"
        style={{
          backgroundColor: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
      >
        <h1 className="mb-1 font-mono text-lg font-medium uppercase tracking-[0.22em]">
          Shelf
        </h1>
        <p
          className="mb-6 text-sm"
          style={{ color: "var(--color-text-muted)" }}
        >
          Sign in to continue.
        </p>
        {error && (
          <p className="mb-4 text-sm text-red-600" role="alert">
            {error}
          </p>
        )}
        {providers === null && !error && (
          <p style={{ color: "var(--color-text-muted)" }}>Loading…</p>
        )}
        {providers && providers.length === 0 && !devLogin && (
          <p
            className="text-sm"
            style={{ color: "var(--color-text-muted)" }}
          >
            No identity providers are configured.
          </p>
        )}
        <div className="flex flex-col gap-2">
          {providers?.map((p) => (
            <button
              key={p}
              onClick={() => startLogin(p)}
              className="flex items-center justify-center gap-2 rounded px-4 py-2 text-sm font-medium text-white hover:opacity-90"
              style={{ backgroundColor: "var(--color-accent)" }}
            >
              <LogIn className="h-4 w-4" />
              Sign in with {providerLabel(p)}
            </button>
          ))}
        </div>

        {/* Local-development escape hatch. SHELF_DEV_LOGIN_ENABLED gates it
            server-side; without this the login page is a dead end on a
            fresh checkout, where no OIDC provider is configured yet. */}
        {devLogin && (
          <form onSubmit={submitDevLogin} className="mt-4">
            {providers && providers.length > 0 && (
              <div
                className="mb-4 border-t pt-4 text-center text-xs uppercase tracking-widest"
                style={{
                  borderColor: "var(--color-border)",
                  color: "var(--color-text-muted)",
                }}
              >
                or
              </div>
            )}
            <label
              className="mb-1 block text-xs uppercase tracking-widest"
              style={{ color: "var(--color-text-muted)" }}
              htmlFor="dev-login-email"
            >
              Dev login
            </label>
            <input
              id="dev-login-email"
              type="email"
              required
              value={devEmail}
              onChange={(e) => setDevEmail(e.target.value)}
              className="mb-2 w-full rounded border px-3 py-2 text-sm"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-surface)",
              }}
            />
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded border px-4 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
              style={{ borderColor: "var(--color-border)" }}
            >
              {busy ? "Signing in…" : "Continue without a provider"}
            </button>
            <p
              className="mt-2 text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              Local development only — mints a session for any address.
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
