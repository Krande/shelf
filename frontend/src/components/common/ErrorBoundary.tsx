import { Component, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

interface Props {
  children: ReactNode;
  fallbackMessage?: string;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8">
          <AlertTriangle className="h-6 w-6 text-yellow-500" />
          <p className="text-sm" style={{ color: "var(--color-text-muted)" }}>
            {this.props.fallbackMessage ?? "Something went wrong"}
          </p>
          <p
            className="max-w-md text-center text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            {this.state.error.message}
          </p>
          <button
            onClick={() => this.setState({ error: null })}
            className="mt-2 rounded border px-3 py-1 text-xs hover:opacity-80"
            style={{
              borderColor: "var(--color-border)",
              color: "var(--color-text)",
            }}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
