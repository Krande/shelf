import { afterEach, describe, expect, it } from "vitest";
import {
  currentReturnTo,
  loginPathWithReturn,
  safeReturnTo,
} from "./returnTo";

/** Point the address bar somewhere, the way a real navigation would. */
function at(path: string): void {
  window.history.replaceState(null, "", path);
}

afterEach(() => at("/"));

describe("safeReturnTo", () => {
  it.each([
    "/",
    "/library",
    "/reader/33333333-4444-5555-6666-777777777777?page=57",
    "/settings/account#tokens",
  ])("keeps the same-origin path %s", (path) => {
    expect(safeReturnTo(path)).toBe(path);
  });

  it.each([
    null,
    undefined,
    "",
    "https://evil.example/phish",
    // Protocol-relative, in both spellings a browser accepts.
    "//evil.example/phish",
    "/\\evil.example/phish",
    "javascript:alert(1)",
    // Relative: would resolve against /auth/login/, not against the SPA.
    "library",
    "/library\nLocation: https://evil.example",
  ])("refuses %s", (value) => {
    expect(safeReturnTo(value)).toBeNull();
  });

  it("refuses an absurdly long path", () => {
    expect(safeReturnTo("/" + "a".repeat(4096))).toBeNull();
  });
});

describe("currentReturnTo", () => {
  it("carries the reader's page, which only the address bar knows", () => {
    // ReaderPage writes ?page= with history.replaceState, so this is the
    // one place the page being read is readable from.
    at("/reader/abc?page=57");
    expect(currentReturnTo()).toBe("/reader/abc?page=57");
  });

  it("has nothing to return to from the root", () => {
    at("/");
    expect(currentReturnTo()).toBeNull();
  });

  it("never points back at the login page itself", () => {
    at("/login?next=%2Flibrary");
    expect(currentReturnTo()).toBeNull();
  });
});

describe("loginPathWithReturn", () => {
  it("encodes the target as ?next=", () => {
    at("/reader/abc?page=57");
    expect(loginPathWithReturn()).toBe(
      "/login?next=%2Freader%2Fabc%3Fpage%3D57",
    );
  });

  it("is a plain /login when there is nowhere to go back to", () => {
    at("/");
    expect(loginPathWithReturn()).toBe("/login");
  });

  it("drops a target it would not accept", () => {
    expect(loginPathWithReturn("https://evil.example")).toBe("/login");
  });
});
