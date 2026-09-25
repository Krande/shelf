/**
 * Where to send the user back to after they sign in again.
 *
 * The path is read off `window.location` rather than react-router's
 * location on purpose: the reader keeps its position in `?page=` via
 * `history.replaceState` (see ReaderPage), which the router never learns
 * about. Reading the address bar directly is what makes "return to the
 * page I was reading" land on the right page rather than page 1.
 */

/** Longest `next` we'll carry. Guards against a pathological URL. */
const MAX_LENGTH = 2048;

/**
 * Accept only same-origin, absolute-path targets.
 *
 * `//host` and `/\host` are browser-protocol-relative URLs, so they'd
 * turn `?next=` into an open redirect off-site; control characters are
 * rejected because they can be used to smuggle one past a naive check.
 */
export function safeReturnTo(value: string | null | undefined): string | null {
  if (!value) return null;
  if (value.length > MAX_LENGTH) return null;
  if (!value.startsWith("/")) return null;
  if (value.startsWith("//") || value.startsWith("/\\")) return null;
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f\x7f]/.test(value)) return null;
  return value;
}

/** The page the user is on now, as a `next` target. */
export function currentReturnTo(): string | null {
  const { pathname, search, hash } = window.location;
  const path = `${pathname}${search}${hash}`;
  // "/" is where login lands anyway, and the login page itself is never
  // somewhere to come back to.
  if (path === "/" || pathname === "/login") return null;
  return safeReturnTo(path);
}

/** `/login`, carrying the page to come back to when there is one. */
export function loginPathWithReturn(
  target: string | null = currentReturnTo(),
): string {
  const next = safeReturnTo(target);
  return next ? `/login?next=${encodeURIComponent(next)}` : "/login";
}
