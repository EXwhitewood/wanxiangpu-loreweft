import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import { Mark } from "@tiptap/core";
import { useEffect, useState, useCallback } from "react";
import { countWords } from "../../utils/wordCount";

const AiHighlight = Mark.create({
  name: "aiHighlight",

  parseHTML() {
    return [{ tag: "span.ai-highlight" }];
  },

  renderHTML() {
    return ["span", { class: "ai-highlight" }, 0];
  },
});

interface TiptapEditorProps {
  content: string;
  onChange: (content: string) => void;
  highlightNew?: boolean;
  onHighlightDismissed?: () => void;
}

export default function TiptapEditor({ content, onChange, highlightNew, onHighlightDismissed }: TiptapEditorProps) {
  const [wordCount, setWordCount] = useState(0);
  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({
        placeholder: "开始书写你的故事...",
      }),
      AiHighlight,
    ],
    content,
    onCreate: ({ editor: ed }) => {
      setWordCount(countWords(ed.getText()));
    },
    onUpdate: ({ editor: ed }) => {
      setWordCount(countWords(ed.getText()));
      onChange(ed.getHTML());
    },
    onTransaction: ({ editor: ed }) => {
      setWordCount(countWords(ed.getText()));
    },
    editorProps: {
      attributes: {
        class: "editor-area ProseMirror outline-none min-h-[400px]",
      },
    },
  });

  useEffect(() => {
    if (editor && content !== editor.getHTML()) {
      editor.commands.setContent(content);
      setWordCount(countWords(editor.getText()));
    }
  }, [content, editor]);

  useEffect(() => {
    if (!editor || !highlightNew) return;
    const text = editor.getText();
    if (!text.trim()) return;
    editor.chain().selectAll().setMark("aiHighlight").run();
  }, [editor, highlightNew]);

  const handleClick = useCallback(() => {
    if (editor && highlightNew) {
      editor.chain().selectAll().unsetMark("aiHighlight").run();
      editor.commands.blur();
      if (onHighlightDismissed) {
        onHighlightDismissed();
      }
    }
  }, [editor, highlightNew, onHighlightDismissed]);

  return (
    <div className="relative flex h-full flex-col" onClick={handleClick}>
      <div className="editor-area flex-1 overflow-y-auto rounded-xl border border-pine-200/40 bg-white/50 p-6">
        <EditorContent editor={editor} />
      </div>
      <div className="mt-2 flex shrink-0 items-center justify-between text-xs text-pine-700">
        <span>
          {highlightNew && (
            <span className="text-magic-400">✨ 新生成内容 · 点击任意位置取消高亮</span>
          )}
        </span>
        <span>{wordCount} 字</span>
      </div>
    </div>
  );
}
