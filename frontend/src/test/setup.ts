import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
});

// jsdom implements neither of these, and the popover/menu components use
// them for outside-click handling and scroll-into-view. Stubbing here
// keeps every test file from having to know that.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = vi.fn();
}

// `fetch` is stubbed per-test. Failing loudly here beats a test silently
// hitting the network and hanging on CI.
vi.stubGlobal(
  "fetch",
  vi.fn(() => {
    throw new Error(
      "Unmocked fetch. Stub it with mockFetch() from src/test/utils.tsx.",
    );
  }),
);
