import { useMemo } from "react";
import { marked } from "marked";
import { sanitizeMarkdownHtml } from "@/utils/sanitizeMarkdownHtml";

interface MarkdownContentProps {
  content: string;
  className?: string;
}

export default function MarkdownContent({ content, className }: MarkdownContentProps) {
  const html = useMemo(() => {
    marked.setOptions({
      gfm: true,
      breaks: true,
    });
    return sanitizeMarkdownHtml(marked(content) as string);
  }, [content]);

  return (
    <div
      className={`chat-markdown ${className || ""}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
