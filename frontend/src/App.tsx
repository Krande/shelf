import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router";
import LoginPage from "@/pages/LoginPage";
import HomePage from "@/pages/HomePage";
import LibraryPage from "@/pages/LibraryPage";
import SettingsPage from "@/pages/SettingsPage";
import ProtectedRoute from "@/components/ProtectedRoute";
import { PREF_SHOW_LANDING, usePref } from "@/auth/prefs";
import { useThemeSync } from "@/auth/theme";

// pdfjs is heavy (~250 kB gz). Load it only when the user actually
// opens a PDF.
const ReaderPage = lazy(() => import("@/pages/ReaderPage"));

/**
 * `/` decides between the centered search landing and the library
 * itself based on the user's pref. The toggle lives in /settings —
 * read+rendered there, also read here. usePref re-renders this
 * component whenever the value flips, so navigating from settings
 * back to "/" reflects the change immediately.
 */
function RootRoute() {
  const [showLanding] = usePref(PREF_SHOW_LANDING);
  if (showLanding) {
    return (
      <ProtectedRoute>
        <HomePage />
      </ProtectedRoute>
    );
  }
  return <Navigate to="/library" replace />;
}

export default function App() {
  useThemeSync();
  return (
    <Routes>
      <Route path="/" element={<RootRoute />} />
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/home"
        element={
          <ProtectedRoute>
            <HomePage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/library"
        element={
          <ProtectedRoute>
            <LibraryPage />
          </ProtectedRoute>
        }
      />
      <Route path="/settings" element={<Navigate to="/settings/account" replace />} />
      <Route
        path="/settings/:tab"
        element={
          <ProtectedRoute>
            <SettingsPage />
          </ProtectedRoute>
        }
      />
      {/* Admin moved into Settings as a tab. Kept as a redirect so any
          bookmark or doc link from when it was top-level still lands. */}
      <Route path="/admin" element={<Navigate to="/settings/admin" replace />} />
      <Route
        path="/reader/:attachmentId"
        element={
          <ProtectedRoute>
            <Suspense fallback={null}>
              <ReaderPage />
            </Suspense>
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
