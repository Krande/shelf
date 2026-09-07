import { useEffect, useState } from "react";
import { Navigate } from "react-router";
import { LogIn } from "lucide-react";
import { apiFetch } from "@/api/client";
import { startLogin, useAuth } from "@/auth/session";

interface ProvidersResponse {
  providers: string[];
}

export default function LoginPage() {
  const auth = useAuth();
  const [providers, setProviders] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<ProvidersResponse>("/auth/providers")
      .then((r) => setProviders(r.providers))
      .catch((e: Error) => setError(e.message));
  }, []);

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
        {providers && providers.length === 0 && (
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
              Sign in with {p[0].toUpperCase() + p.slice(1)}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
