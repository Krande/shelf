import { describe, expect, it } from "vitest";
import { wheelScaleFactor } from "./usePinchZoom";

/** Percentage change one wheel event applies, for readable assertions. */
function stepPercent(deltaY: number, deltaMode = 0): number {
  return Math.abs(1 - wheelScaleFactor(deltaY, deltaMode)) * 100;
}

describe("wheelScaleFactor", () => {
  it("makes one mouse notch a step you can aim with", () => {
    // 100px is the usual notch. Unclamped this was exp(1) — a 172%
    // jump per notch, which is what made the zoom unusable.
    expect(stepPercent(100)).toBeGreaterThan(5);
    expect(stepPercent(100)).toBeLessThan(15);
  });

  it("treats a bigger notch the same, rather than scaling with it", () => {
    // Browsers and mice disagree about notch size; past the clamp they
    // should all land on the same step.
    expect(wheelScaleFactor(100, 0)).toBeCloseTo(wheelScaleFactor(240, 0), 6);
  });

  it("keeps a trackpad pinch fine-grained", () => {
    // Small deltas arrive dozens per second; each should barely move,
    // or a pinch overshoots.
    expect(stepPercent(2)).toBeLessThan(3);
    expect(stepPercent(2)).toBeGreaterThan(0);
  });

  it("zooms in on a negative delta and out on a positive one", () => {
    expect(wheelScaleFactor(-100, 0)).toBeGreaterThan(1);
    expect(wheelScaleFactor(100, 0)).toBeLessThan(1);
  });

  it("is symmetric, so a notch back undoes a notch forward", () => {
    expect(wheelScaleFactor(-100, 0) * wheelScaleFactor(100, 0)).toBeCloseTo(
      1,
      6,
    );
  });

  it("normalises line and page delta modes", () => {
    // deltaMode 1 counts lines, 2 counts pages. Read as pixels they
    // would be near-zero steps; converted, both saturate the clamp.
    expect(wheelScaleFactor(3, 1)).toBeCloseTo(wheelScaleFactor(100, 0), 6);
    expect(wheelScaleFactor(1, 2)).toBeCloseTo(wheelScaleFactor(100, 0), 6);
  });
});
