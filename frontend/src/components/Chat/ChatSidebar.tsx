import { useState, useRef, useEffect } from "react";
import { Plus, Search, Pin, MoreHorizontal, Pencil, Trash2, PinOff, Download } from "lucide-react";
import clsx from "clsx";
import type { ChatSessionItem, ChatMessageItem } from "@/types";
import { exportAsMarkdown, exportAsJson, downloadFile } from "@/components/Chat/chatExport";

interface ChatSidebarProps {
  projectId: string;
  agentType: string;
  sessions: ChatSessionItem[];
  currentSessionId: string | null;
  currentMessages?: ChatMessageItem[];
  onSelectSession: (sessionId: string) => void;
  onNewSession: () => void;
  onRename: (sessionId: string, name: string) => Promise<void>;
  onDelete: (sessionId: string) => Promise<void>;
  onPin: (sessionId: string) => Promise<void>;
  onUnpin: (sessionId: string) => Promise<void>;
  onSearch?: (query: string) => Promise<void>;
}

function formatTimeGroup(dateStr: string | null): string {
  if (!dateStr) return "更早";
  const d = new Date(dateStr);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const msgDate = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  if (msgDate.getTime() === today.getTime()) return "今天";
  if (msgDate.getTime() === yesterday.getTime()) return "昨天";
  return "更早";
}

export default function ChatSidebar({
  sessions,
  currentSessionId,
  currentMessages,
  onSelectSession,
  onNewSession,
  onRename,
  onDelete,
  onPin,
  onUnpin,
  onSearch,
}: ChatSidebarProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [menuSessionId, setMenuSessionId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuSessionId(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const pinnedSessions = sessions.filter((s) => s.is_pinned);
  const unpinnedSessions = sessions.filter((s) => !s.is_pinned);

  const grouped: Record<string, ChatSessionItem[]> = {};
  for (const s of unpinnedSessions) {
    const group = formatTimeGroup(s.updated_at);
    if (!grouped[group]) grouped[group] = [];
    grouped[group].push(s);
  }

  // If we have a server search, we just use the filtered sessions directly
  const filteredPinned = pinnedSessions;
  const filteredGrouped = grouped;
  
  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const q = e.target.value;
    setSearchQuery(q);
    if (onSearch) {
      onSearch(q);
    }
  };

  const handleStartRename = (sessionId: string, currentTitle: string) => {
    setEditingId(sessionId);
    setEditTitle(currentTitle);
    setMenuSessionId(null);
  };

  const handleFinishRename = async () => {
    if (editingId && editTitle.trim()) {
      await onRename(editingId, editTitle.trim());
    }
    setEditingId(null);
    setEditTitle("");
  };

  const handleDelete = async (sessionId: string) => {
    setMenuSessionId(null);
    await onDelete(sessionId);
  };

  const renderSessionItem = (s: ChatSessionItem) => {
    const isActive = s.session_id === currentSessionId;
    const isEditing = editingId === s.session_id;

    return (
      <div
        key={s.session_id}
        className={clsx(
          "group relative flex items-start gap-2 rounded-lg px-2.5 py-2 cursor-pointer transition-colors",
          isActive
            ? "bg-magic-500/10 border-l-2 border-magic-500"
            : "hover:bg-white/50"
        )}
        onClick={() => {
          if (!isEditing) onSelectSession(s.session_id);
        }}
      >
        <div className="min-w-0 flex-1">
          {isEditing ? (
            <input
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              onBlur={handleFinishRename}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleFinishRename();
                if (e.key === "Escape") { setEditingId(null); setEditTitle(""); }
              }}
              className="w-full rounded border border-magic-500/30 bg-white/50 px-1.5 py-0.5 text-xs text-pine-700 outline-none"
              autoFocus
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <p className="truncate text-xs text-pine-700 group-hover:text-pine-700">
              {s.title || "未命名对话"}
            </p>
          )}
          <p className="mt-0.5 text-[10px] text-pine-700">
            {s.message_count}条消息
          </p>
        </div>

        <button
          onClick={(e) => {
            e.stopPropagation();
            setMenuSessionId(menuSessionId === s.session_id ? null : s.session_id);
          }}
          className="shrink-0 rounded p-0.5 text-pine-700 opacity-0 transition-opacity group-hover:opacity-100 hover:text-pine-700"
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </button>

        {menuSessionId === s.session_id && (
          <div
            ref={menuRef}
            className="absolute right-0 top-8 z-20 w-36 rounded-lg border border-pine-200 bg-white/50 py-1 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => handleStartRename(s.session_id, s.title)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50"
            >
              <Pencil className="h-3 w-3" /> 重命名
            </button>
            {s.is_pinned ? (
              <button
                onClick={() => { onUnpin(s.session_id); setMenuSessionId(null); }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50"
              >
                <PinOff className="h-3 w-3" /> 取消置顶
              </button>
            ) : (
              <button
                onClick={() => { onPin(s.session_id); setMenuSessionId(null); }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50"
              >
                <Pin className="h-3 w-3" /> 置顶
              </button>
            )}
            {s.session_id === currentSessionId && currentMessages && currentMessages.length > 0 && (
              <button
                onClick={() => {
                  const md = exportAsMarkdown(currentMessages, s.title);
                  downloadFile(md, `${s.title || "聊天记录"}.md`, "text/markdown;charset=utf-8");
                  setMenuSessionId(null);
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50"
              >
                <Download className="h-3 w-3" /> 导出 Markdown
              </button>
            )}
            {s.session_id === currentSessionId && currentMessages && currentMessages.length > 0 && (
              <button
                onClick={() => {
                  const json = exportAsJson(currentMessages, s.title);
                  downloadFile(json, `${s.title || "聊天记录"}.json`, "application/json;charset=utf-8");
                  setMenuSessionId(null);
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50"
              >
                <Download className="h-3 w-3" /> 导出 JSON
              </button>
            )}
            <button
              onClick={() => handleDelete(s.session_id)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-crimson-400 hover:bg-white/50"
            >
              <Trash2 className="h-3 w-3" /> 删除
            </button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="flex h-full w-56 shrink-0 flex-col border-r border-pine-200/40 bg-white/50">
      <div className="shrink-0 p-2">
        <button
          onClick={onNewSession}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-pine-200 bg-white/50 px-3 py-1.5 text-xs text-pine-700 transition-colors hover:border-magic-500/30 hover:text-magic-400"
        >
          <Plus className="h-3.5 w-3.5" />
          新建对话
        </button>
      </div>

      <div className="shrink-0 px-2 pb-1">
        <div className="flex items-center gap-1.5 rounded-lg border border-pine-200/40 bg-white/50 px-2 py-1">
          <Search className="h-3 w-3 text-pine-700" />
          <input
            value={searchQuery}
            onChange={handleSearchChange}
            placeholder="搜索对话..."
            className="flex-1 bg-transparent text-xs text-pine-700 outline-none placeholder:text-pine-700"
          />
        </div>
      </div>

      <div className="flex-1 overflow-auto px-1.5 py-1">
        {filteredPinned.length > 0 && (
          <div className="mb-2">
            <div className="px-2 py-1 text-[10px] font-medium text-pine-700 uppercase tracking-wider">
              置顶
            </div>
            {filteredPinned.map(renderSessionItem)}
          </div>
        )}

        {Object.entries(filteredGrouped).map(([group, items]) => (
          <div key={group} className="mb-2">
            <div className="px-2 py-1 text-[10px] font-medium text-pine-700 uppercase tracking-wider">
              {group}
            </div>
            {items.map(renderSessionItem)}
          </div>
        ))}

        {filteredPinned.length === 0 && Object.keys(filteredGrouped).length === 0 && (
          <div className="py-8 text-center text-xs text-pine-700">
            {searchQuery ? "未找到匹配的对话" : "暂无对话记录"}
          </div>
        )}
      </div>
    </div>
  );
}
