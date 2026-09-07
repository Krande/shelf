import type { ReactNode } from "react";
import Header from "./Header";
import ErrorBoundary from "../common/ErrorBoundary";
import { useAuth } from "@/auth/session";

export default function AppShell({ children }: { children: ReactNode }) {
  const auth = useAuth();
  if (auth.status !== "authenticated") return null;
  return (
    <div className="flex h-screen flex-col">
      <Header user={auth.user} />
      <main className="flex-1 overflow-auto">
        <ErrorBoundary>{children}</ErrorBoundary>
      </main>
    </div>
  );
}
