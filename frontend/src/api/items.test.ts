import { beforeEach, describe, expect, it } from "vitest";
import {
  ALL_SEARCH_SCOPES,
  isDefaultScope,
  isSearchScope,
  scopeLabel,
  scopeRankOrder,
  searchMyItems,
} from "./items";
import { resetSessionExpiryForTests } from "@/auth/expiry";
import { mockFetch } from "@/test/utils";

/** The URL the last request went to. */
function requestedUrl(fn: ReturnType<typeof mockFetch>): string {
  const last = fn.mock.calls.at(-1);
  return String(last?.[0]);
}

beforeEach(() => resetSessionExpiryForTests());

describe("searchMyItems", () => {
  let fetchMock: ReturnType<typeof mockFetch>;

  beforeEach(() => {
    fetchMock = mockFetch({ "/api/me/items": { body: { items: [], total: 0 } } });
  });

  it("searches every readable space in one request", async () => {
    // One call, not one per space — which is also what keeps a document
    // that two of the user's spaces can reach from being listed twice.
    await searchMyItems({ q: "acme" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(requestedUrl(fetchMock)).toBe("/api/me/items?q=acme");
  });

  it("leaves space= off until the user narrows", async () => {
    await searchMyItems({ q: "acme", spaces: undefined });
    expect(requestedUrl(fetchMock)).not.toContain("space=");
  });

  it("names each selected space when narrowed", async () => {
    await searchMyItems({ q: "acme", spaces: ["u-ab12cd34", "standards"] });
    const url = requestedUrl(fetchMock);
    expect(url).toContain("space=u-ab12cd34");
    expect(url).toContain("space=standards");
  });

  it("sends an empty space= when every space is filtered out", async () => {
    // Dropping the param entirely would read as "all of them" at the
    // other end, turning "nothing selected" into "everything".
    await searchMyItems({ q: "acme", spaces: [] });
    expect(requestedUrl(fetchMock)).toContain("space=");
    expect(requestedUrl(fetchMock)).toMatch(/space=(&|$)/);
  });

  it("omits scope= when nothing is narrowed", async () => {
    await searchMyItems({ q: "acme", scope: [...ALL_SEARCH_SCOPES] });
    expect(requestedUrl(fetchMock)).not.toContain("scope=");
  });

  it("carries a narrowed scope", async () => {
    await searchMyItems({ q: "acme", scope: ["title"] });
    expect(requestedUrl(fetchMock)).toContain("scope=title");
  });

  it("sends the whole scope once a metadata field is added", async () => {
    // Longer than the default, but not the default: every built-in has to
    // go along with the field or the server would search only the field.
    await searchMyItems({ q: "acme", scope: [...ALL_SEARCH_SCOPES, "field:edition"] });
    const url = requestedUrl(fetchMock);
    expect(url).toContain("scope=field%3Aedition");
    expect(url).toContain("scope=designation");
    expect(url).toContain("scope=fulltext");
  });

  it("knows the default only when it is exactly the built-ins", () => {
    expect(isDefaultScope([...ALL_SEARCH_SCOPES].reverse())).toBe(true);
    expect(isDefaultScope(["title"])).toBe(false);
    expect(isDefaultScope([...ALL_SEARCH_SCOPES, "field:edition"])).toBe(false);
  });

  it("accepts the built-ins and well-formed field scopes only", () => {
    expect(isSearchScope("designation")).toBe(true);
    expect(isSearchScope("field:edition")).toBe(true);
    expect(isSearchScope("field:")).toBe(false);
    expect(isSearchScope("none")).toBe(false);
  });

  it("labels a field scope by its field", () => {
    expect(scopeLabel("designation")).toBe("Designation");
    expect(scopeLabel("field:standardBody")).toBe("Issuing Body");
  });

  it("groups field hits between the metadata and the PDF body", () => {
    expect(scopeRankOrder(["field:edition", "title"])).toEqual([
      "title",
      "designation",
      "creators",
      "abstract",
      "extra",
      "field:edition",
      "fulltext",
    ]);
  });

  it("trims the query and skips a blank one", async () => {
    await searchMyItems({ q: "  acme  " });
    expect(requestedUrl(fetchMock)).toBe("/api/me/items?q=acme");
    await searchMyItems({ q: "   " });
    expect(requestedUrl(fetchMock)).toBe("/api/me/items");
  });
});
