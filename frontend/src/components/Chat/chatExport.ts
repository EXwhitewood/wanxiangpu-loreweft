import type { ChatMessageItem } from "@/types";

function escapeMarkdown(text: string): string {
  return text.replace(/([\\`*_{\}\[\]()#+\-.!|>~])/g, "\\$1");
}

export function exportAsMarkdown(
  messages: ChatMessageItem[],
  sessionTitle: string
): string {
  const lines: string[] = [];
  lines.push(`# ${sessionTitle || "聊天记录"}`);
  lines.push("");
  lines.push(`> 导出时间：${new Date().toLocaleString("zh-CN")}`);
  lines.push("");

  for (const msg of messages) {
    const ts = msg.created_at
      ? new Date(msg.created_at).toLocaleString("zh-CN")
      : "";

    if (msg.role === "system") {
      lines.push(`---`);
      lines.push(`*系统消息${ts ? ` · ${ts}` : ""}*`);
      lines.push("");
      lines.push(msg.content);
      lines.push("");
    } else if (msg.role === "user") {
      lines.push(`### 👤 用户${ts ? ` · ${ts}` : ""}`);
      lines.push("");
      lines.push(escapeMarkdown(msg.content));
      lines.push("");
    } else {
      lines.push(`### 🤖 助手${ts ? ` · ${ts}` : ""}`);
      lines.push("");
      lines.push(msg.content);
      lines.push("");
    }
  }

  return lines.join("\n");
}

export function exportAsJson(
  messages: ChatMessageItem[],
  sessionTitle: string
): string {
  return JSON.stringify(
    {
      title: sessionTitle || "聊天记录",
      exported_at: new Date().toISOString(),
      message_count: messages.length,
      messages: messages.map((m) => ({
        role: m.role,
        content: m.content,
        created_at: m.created_at || null,
      })),
    },
    null,
    2
  );
}

export function downloadFile(content: string, filename: string, mimeType: string) {
  const BOM = "\uFEFF";
  const blob = new Blob([BOM + content], { type: `${mimeType};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
