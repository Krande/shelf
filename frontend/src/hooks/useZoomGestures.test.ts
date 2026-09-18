import { describe, expect, it } from "vitest";
import { clampZoom, MAX_ZOOM, MIN_ZOOM } from "./useZoomGestures";

describe("clampZoom", () => {
  it("leaves an ordinary level alone", () => {
    expect(clampZoom(1)).toBe(1);
    expect(clampZoom(2.5)).toBe(2.5);
  });

  it("holds the floor, so a page can never collapse to nothing", () => {
    expect(clampZoom(0)).toBe(MIN_ZOOM);
    expect(clampZoom(-4)).toBe(MIN_ZOOM);
  });

  it("holds the ceiling, which is what keeps canvases drawable", () => {
    expect(clampZoom(50)).toBe(MAX_ZOOM);
  });

  it("is idempotent at the bounds", () => {
    expect(clampZoom(clampZoom(99))).toBe(MAX_ZOOM);
    expect(clampZoom(clampZoom(0))).toBe(MIN_ZOOM);
  });

  it("brackets 1, so the fit scale is always reachable", () => {
    expect(MIN_ZOOM).toBeLessThan(1);
    expect(MAX_ZOOM).toBeGreaterThan(1);
  });
});
