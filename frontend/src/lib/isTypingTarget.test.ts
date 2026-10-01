import { describe, expect, it } from "vitest";
import { isTypingTarget } from "./isTypingTarget";

describe("isTypingTarget", () => {
  it("claims every kind of text field", () => {
    expect(isTypingTarget(document.createElement("input"))).toBe(true);
    expect(isTypingTarget(document.createElement("textarea"))).toBe(true);
    expect(isTypingTarget(document.createElement("select"))).toBe(true);
    const editor = document.createElement("div");
    // As TipTap marks its editor root.
    editor.setAttribute("contenteditable", "true");
    document.body.append(editor);
    expect(isTypingTarget(editor)).toBe(true);
    editor.remove();
  });

  it("leaves the rest to the page", () => {
    expect(isTypingTarget(document.createElement("div"))).toBe(false);
    expect(isTypingTarget(document.body)).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
    expect(isTypingTarget(window)).toBe(false);
  });
});
