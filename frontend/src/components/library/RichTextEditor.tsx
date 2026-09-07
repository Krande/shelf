import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  Bold,
  Code,
  Italic,
  List,
  ListOrdered,
  Quote,
  Strikethrough,
} from "lucide-react";
import { useEffect } from "react";

/**
 * Minimal TipTap editor backed by StarterKit. Emits HTML via
 * `onUpdate` so the parent form can store it as `data.note` directly.
 * The toolbar is intentionally compact — we ship the formatting
 * verbs that round-trip cleanly through HTML and skip the more
 * exotic ones (tables, images) until they have a concrete need.
 */
export default function RichTextEditor({
  value,
  onChange,
  disabled = false,
  placeholder = "Write a note…",
}: {
  value: string;
  onChange: (html: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const editor = useEditor({
    extensions: [StarterKit],
    content: value,
    editable: !disabled,
    immediatelyRender: false,
    editorProps: {
      attributes: {
        class:
          "tiptap min-h-[160px] rounded border px-3 py-2 text-sm outline-none focus:ring-1",
        "data-placeholder": placeholder,
      },
    },
    onUpdate: ({ editor: e }) => onChange(e.getHTML()),
  });

  // Keep the editor in sync when the form is reopened with different
  // initial content (edit-of-different-item).
  useEffect(() => {
    if (editor && value !== editor.getHTML()) {
      editor.commands.setContent(value, { emitUpdate: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  useEffect(() => {
    if (editor) editor.setEditable(!disabled);
  }, [disabled, editor]);

  if (!editor) {
    return (
      <div
        className="min-h-[160px] rounded border px-3 py-2 text-sm"
        style={{
          backgroundColor: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
      />
    );
  }

  const Btn = ({
    onClick,
    active,
    label,
    Icon,
  }: {
    onClick: () => void;
    active: boolean;
    label: string;
    Icon: typeof Bold;
  }) => (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="rounded p-1 hover:opacity-80"
      style={{
        color: active ? "var(--color-accent)" : "var(--color-text-muted)",
      }}
    >
      <Icon className="h-3.5 w-3.5" />
    </button>
  );

  return (
    <div
      className="rounded border"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
        color: "var(--color-text)",
      }}
    >
      <div
        className="flex items-center gap-0.5 border-b px-2 py-1"
        style={{ borderColor: "var(--color-border)" }}
      >
        <Btn
          onClick={() => editor.chain().focus().toggleBold().run()}
          active={editor.isActive("bold")}
          label="Bold"
          Icon={Bold}
        />
        <Btn
          onClick={() => editor.chain().focus().toggleItalic().run()}
          active={editor.isActive("italic")}
          label="Italic"
          Icon={Italic}
        />
        <Btn
          onClick={() => editor.chain().focus().toggleStrike().run()}
          active={editor.isActive("strike")}
          label="Strikethrough"
          Icon={Strikethrough}
        />
        <Btn
          onClick={() => editor.chain().focus().toggleCode().run()}
          active={editor.isActive("code")}
          label="Inline code"
          Icon={Code}
        />
        <span
          className="mx-1 h-4 w-px"
          style={{ backgroundColor: "var(--color-border)" }}
        />
        <Btn
          onClick={() => editor.chain().focus().toggleBulletList().run()}
          active={editor.isActive("bulletList")}
          label="Bulleted list"
          Icon={List}
        />
        <Btn
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
          active={editor.isActive("orderedList")}
          label="Numbered list"
          Icon={ListOrdered}
        />
        <Btn
          onClick={() => editor.chain().focus().toggleBlockquote().run()}
          active={editor.isActive("blockquote")}
          label="Quote"
          Icon={Quote}
        />
      </div>
      <EditorContent editor={editor} />
    </div>
  );
}
