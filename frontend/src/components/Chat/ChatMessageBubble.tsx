import { useState, type ReactNode } from "react";
import { Copy, Check, RefreshCw } from "lucide-react";
import MarkdownContent from "@/components/Common/MarkdownContent";

interface ChatMessageBubbleProps {
  role: "user" | "assistant" | "system";
  content: ReactNode;
  copyText?: string;
  timestamp?: string;
  onRegenerate?: () => void;
}

function formatTimestamp(ts: string) {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export default function ChatMessageBubble({
  role,
  content,
  copyText,
  timestamp,
  onRegenerate,
}: ChatMessageBubbleProps) {
  const [copied, setCopied] = useState(false);
  const [hovered, setHovered] = useState(false);

  const handleCopy = async () => {
    try {
      let textContent: string;
      if (copyText !== undefined) {
        textContent = copyText;
      } else if (typeof content === "string") {
        textContent = content;
      } else if (content instanceof Array) {
        textContent = content
          .map((c) => (typeof c === "string" ? c : ""))
          .filter(Boolean)
          .join("");
      } else {
        textContent = "";
      }
      let copiedSuccessfully = false;
      if (navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(textContent);
          copiedSuccessfully = true;
        } catch {
          // Clipboard permissions can be unavailable in embedded windows;
          // fall through to the compatibility path below.
        }
      }
      if (!copiedSuccessfully) {
        const textarea = document.createElement("textarea");
        textarea.value = textContent;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        textarea.setAttribute("readonly", "");
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        copiedSuccessfully = document.execCommand("copy");
        textarea.remove();
      }
      if (!copiedSuccessfully) throw new Error("copy command failed");
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  };

  if (role === "system") {
    return (
      <div className="flex justify-center">
        <div className="chat-bubble-system rounded-lg px-3 py-1.5 text-xs">
          {content}
        </div>
      </div>
    );
  }

  const isStringContent = typeof content === "string";

  return (
    <div
      className={`flex ${role === "user" ? "justify-end" : "justify-start"}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div className="group relative max-w-[85%]">
        <div
            className={`select-text rounded-xl px-3.5 py-2.5 text-sm leading-relaxed ${
            role === "user"
              ? "chat-bubble-user whitespace-pre-wrap"
              : "chat-bubble-assistant"
          }`}
          style={{ userSelect: "text", WebkitUserSelect: "text" }}
        >
          {role === "user"
            ? content
            : isStringContent
              ? <MarkdownContent content={content as string} />
              : content
          }
        </div>

        {(hovered || timestamp) && role === "assistant" && (
          <div className="absolute -bottom-5 left-0 flex items-center gap-2">
            {timestamp && (
              <span className="text-[10px]" style={{ color: "var(--text-faint)" }}>
                {formatTimestamp(timestamp)}
              </span>
            )}
            {(hovered || timestamp) && (
              <div className="flex items-center gap-1">
                <button
                  onClick={handleCopy}
                  type="button"
                  aria-label="复制消息"
                  className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] transition-colors"
                  style={{ color: "var(--text-dim)" }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.backgroundColor = "var(--bg-tertiary)";
                    e.currentTarget.style.color = "var(--text-tertiary)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.backgroundColor = "transparent";
                    e.currentTarget.style.color = "var(--text-dim)";
                  }}
                >
                  {copied ? (
                    <Check className="h-3 w-3 text-jade-400" />
                  ) : (
                    <Copy className="h-3 w-3" />
                  )}
                  {copied ? "已复制" : "复制"}
                </button>
                {onRegenerate && (
                  <button
                    onClick={onRegenerate}
                    className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] transition-colors"
                    style={{ color: "var(--text-dim)" }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.backgroundColor = "var(--bg-tertiary)";
                      e.currentTarget.style.color = "var(--text-tertiary)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.backgroundColor = "transparent";
                      e.currentTarget.style.color = "var(--text-dim)";
                    }}
                  >
                    <RefreshCw className="h-3 w-3" />
                    重新生成
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export function DateDivider({ date }: { date: string }) {
  try {
    const d = new Date(date);
    const today = new Date();
    const yesterday = new Date(today.getTime() - 86400000);

    const isToday =
      d.getDate() === today.getDate() &&
      d.getMonth() === today.getMonth() &&
      d.getFullYear() === today.getFullYear();

    const isYesterday =
      d.getDate() === yesterday.getDate() &&
      d.getMonth() === yesterday.getMonth() &&
      d.getFullYear() === yesterday.getFullYear();

    let label = "";
    if (isToday) label = "今天";
    else if (isYesterday) label = "昨天";
    else {
      label = d.toLocaleDateString("zh-CN", {
        year: "numeric",
        month: "long",
        day: "numeric",
      });
    }

    return (
      <div className="chat-date-divider flex items-center justify-center py-2">
        <div className="flex-1 h-px" style={{ backgroundColor: "var(--chat-divider)" }}></div>
        <span className="px-3 text-[10px]" style={{ color: "var(--text-faint)" }}>{label}</span>
        <div className="flex-1 h-px" style={{ backgroundColor: "var(--chat-divider)" }}></div>
      </div>
    );
  } catch {
    return null;
  }
}
