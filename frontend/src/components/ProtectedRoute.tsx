import { useEffect, type ReactNode } from "react";
import { Navigate } from "react-router";
import { useAuth } from "@/auth/session";
import { loginPathWithReturn } from "@/auth/returnTo";

/**
 * Guards a route on the cookie-session auth state. Anonymous users are
 * pushed to /login (which renders the IdP picker / "Sign in" button).
 * The login page itself triggers the full-page redirect to the chosen
 * OIDC provider.
 *
 * The page they were on rides along in `?next=`, so an expired session
 * costs them a sign-in rather than their place in a document. It's read
 * off the address bar in the render that decides to redirect, which is
 * still the reader's URL at that point — `?page=` included, which is how
 * they come back to the page they were on and not page 1.
 */
export default function ProtectedRoute({ children }: { children: ReactNode }) {
  const auth = useAuth();

  useEffect(() => {
    // Hint for keyboard-navigation users; nothing to do otherwise.
    if (auth.status === "anonymous") {
      document.title = "Sign in — Shelf";
    }
  }, [auth.status]);

  if (auth.status === "loading") {
    return (
      <div className="flex h-screen items-center justify-center">
        <p style={{ color: "var(--color-text-muted)" }}>Loading…</p>
      </div>
    );
  }
  if (auth.status === "anonymous") {
    return <Navigate to={loginPathWithReturn()} replace />;
  }
  return <>{children}</>;
}
