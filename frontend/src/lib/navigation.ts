/**
 * Full-page navigations, in one place.
 *
 * Several auth flows deliberately reload rather than router-navigate: the
 * OAuth dance has to happen in the top-level browsing context, and after
 * the active identity changes every cached query holds the previous
 * account's data. Routing those through one function keeps that decision
 * documented in a single spot — and makes it mockable, since jsdom won't
 * let a test intercept `window.location.assign` directly.
 */

export function hardNavigate(url: string): void {
  window.location.assign(url);
}

/**
 * Reload the app after the active account changes. Anything cached under
 * the previous identity — the library list, item details, space list —
 * has to be thrown away, and a reload is the one way to be sure.
 */
export function reloadAsNewAccount(): void {
  hardNavigate("/");
}

/** Send the browser to the login page after the session ends. */
export function goToLogin(): void {
  hardNavigate("/login");
}
