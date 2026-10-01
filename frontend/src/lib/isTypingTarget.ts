/** Whether a key event belongs to a text field rather than to the page's
 *  own shortcuts: inputs, textareas, selects and rich-text editors. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    // The attribute as well as the property: a rich-text editor marks
    // its root, and jsdom doesn't implement `isContentEditable`.
    target.isContentEditable === true ||
    target.closest('[contenteditable="true"]') !== null
  );
}
