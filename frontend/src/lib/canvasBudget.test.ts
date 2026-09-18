import { describe, expect, it } from "vitest";
import { clampPixelMultiplier } from "./canvasBudget";

// A4 at fit-width on a desktop container: ~1190 × 1684 CSS px.
const A4_W = 1190;
const A4_H = 1684;

describe("clampPixelMultiplier", () => {
  it("leaves a reasonable request alone", () => {
    // dpr 2, no oversample — the everyday case, well inside every cap.
    expect(clampPixelMultiplier(2, 600, 850)).toBe(2);
  });

  it("caps the area before a page becomes undrawable", () => {
    // dpr 2 × oversample 2 on a fit-width A4 asks for 4800 × 6736,
    // which is 32 MP and roughly 130 MB for one page.
    const m = clampPixelMultiplier(4, A4_W, A4_H);
    expect(m).toBeLessThan(4);
    expect(A4_W * m * (A4_H * m)).toBeLessThanOrEqual(20e6 + 1);
  });

  it("caps the longest side, which is the harder browser limit", () => {
    // A tall drawing sheet: area is fine, height is not.
    const m = clampPixelMultiplier(8, 500, 4000);
    expect(4000 * m).toBeLessThanOrEqual(8192);
  });

  it("never drops below 1", () => {
    // A page already past the caps at its CSS size still has to render;
    // soft beats blank.
    expect(clampPixelMultiplier(2, 20000, 20000)).toBe(1);
  });

  it("is defensive about a page with no size yet", () => {
    expect(clampPixelMultiplier(2, 0, 0)).toBe(1);
    expect(clampPixelMultiplier(2, Number.NaN, 100)).toBe(1);
  });

  it("does not raise a request that was already modest", () => {
    expect(clampPixelMultiplier(1, A4_W, A4_H)).toBe(1);
  });
});
