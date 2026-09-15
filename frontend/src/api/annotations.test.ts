import { describe, expect, it } from "vitest";
import { annotationLink } from "./annotations";

const ATT = "11111111-1111-1111-1111-111111111111";
const ANN = "22222222-2222-2222-2222-222222222222";

describe("annotationLink", () => {
  it("points at the reader with the annotation as a query param", () => {
    expect(annotationLink(ATT, ANN)).toBe(
      `/reader/${ATT}?annotation=${ANN}`,
    );
  });

  it("is relative by default, so it works under any origin", () => {
    expect(annotationLink(ATT, ANN).startsWith("/")).toBe(true);
  });

  it("takes an origin for something pasteable elsewhere", () => {
    expect(annotationLink(ATT, ANN, "https://shelf.example.com")).toBe(
      `https://shelf.example.com/reader/${ATT}?annotation=${ANN}`,
    );
  });

  it("does not double the slash on a trailing-slash origin", () => {
    expect(annotationLink(ATT, ANN, "https://shelf.example.com/")).toBe(
      `https://shelf.example.com/reader/${ATT}?annotation=${ANN}`,
    );
    expect(annotationLink(ATT, ANN, "https://shelf.example.com///")).toBe(
      `https://shelf.example.com/reader/${ATT}?annotation=${ANN}`,
    );
  });

  it("escapes ids rather than trusting them into the URL", () => {
    const link = annotationLink("a/b", "c&d=e");
    expect(link).toBe("/reader/a%2Fb?annotation=c%26d%3De");
    // And it survives a round trip through the parser the reader uses.
    const url = new URL(link, "https://shelf.example.com");
    expect(url.pathname).toBe("/reader/a%2Fb");
    expect(url.searchParams.get("annotation")).toBe("c&d=e");
  });

  it("produces a URL the reader route actually matches", () => {
    const url = new URL(
      annotationLink(ATT, ANN),
      "https://shelf.example.com",
    );
    expect(url.pathname).toBe(`/reader/${ATT}`);
    expect(url.searchParams.get("annotation")).toBe(ANN);
  });
});
