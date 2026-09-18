import { describe, expect, it } from "vitest";
import {
  CanvasBudget,
  clampPixelMultiplier,
  TOTAL_PIXEL_BUDGET,
} from "./canvasBudget";

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

describe("CanvasBudget", () => {
  it("lets the first page have what it asked for", () => {
    const b = new CanvasBudget();
    expect(b.allow("p1", 2, 600, 800)).toBe(2);
  });

  it("holds later pages back once the budget is spent", () => {
    const b = new CanvasBudget();
    // One page claiming the whole budget.
    b.set("p1", TOTAL_PIXEL_BUDGET);
    // The next has nothing to spend, so it renders at its CSS size.
    expect(b.allow("p2", 4, 2000, 2000)).toBe(1);
  });

  it("does not count a page as competing with itself", () => {
    const b = new CanvasBudget();
    b.set("p1", TOTAL_PIXEL_BUDGET);
    // Re-rendering p1 at a new scale should see a free budget: what it
    // holds now is about to be replaced.
    expect(b.allow("p1", 2, 600, 800)).toBe(2);
  });

  it("frees the budget when a page unmounts", () => {
    const b = new CanvasBudget();
    b.set("p1", TOTAL_PIXEL_BUDGET);
    expect(b.allow("p2", 4, 2000, 2000)).toBe(1);
    b.release("p1");
    expect(b.allow("p2", 4, 2000, 2000)).toBeGreaterThan(1);
  });

  it("shares what is left rather than refusing outright", () => {
    const b = new CanvasBudget();
    b.set("p1", TOTAL_PIXEL_BUDGET / 2);
    // Half the budget left over a 1 MP page affords about 8.6x, so ask
    // for more than that and the answer should be what is left, not a
    // refusal and not the full request.
    const m = b.allow("p2", 12, 1000, 1000);
    expect(m).toBeGreaterThan(1);
    expect(m).toBeLessThan(12);
    expect(1000 * m * (1000 * m)).toBeLessThanOrEqual(
      TOTAL_PIXEL_BUDGET / 2 + 1,
    );
  });

  it("tracks what is held", () => {
    const b = new CanvasBudget();
    b.set("p1", 1000);
    b.set("p2", 2000);
    expect(b.used).toBe(3000);
    b.release("p1");
    expect(b.used).toBe(2000);
  });

  it("is defensive about a page with no size", () => {
    expect(new CanvasBudget().allow("p", 4, 0, 0)).toBe(1);
  });
});
