import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router";
import App from "./App";
import { ApiError } from "./api/client";
import { getPref, PREF_PALETTE, PREF_THEME } from "./auth/prefs";
import { applyTheme } from "./auth/theme";
import "./index.css";

// Apply the saved theme + palette synchronously, before React mounts,
// so the first paint isn't a flash of light-on-dark / wrong palette.
// useThemeSync then keeps both in sync as the user toggles the prefs.
applyTheme(getPref(PREF_THEME), getPref(PREF_PALETTE));

// Don't retry 4xx — 401 means "not logged in", 404 means "doesn't exist".
// Server errors get a couple of retries to ride out a brief blip.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status < 500) return false;
        return failureCount < 2;
      },
      staleTime: 5_000,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
