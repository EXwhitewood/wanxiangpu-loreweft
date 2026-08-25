import { useState, useRef, useEffect } from "react";
import {
  Plus,
  ChevronDown,
  Pencil,
  Trash2,
  Pin,
  PinOff,
  MoreHorizontal,
  Download,
} from "lucide-react";
import type { ChatSessionItem, ChatMessageItem } from "@/types";
import { exportAsMarkdown, exportAsJson, downloadFile } from "@/components/Chat/chatExport";

interface ChatSessionDropdownProps {
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

export default function ChatSessionDropdown({
  sessions,
  currentSessionId,
  currentMessages,
  onSelectSession,
  onNewSession,
  onRename,
  onDelete,
  onPin,
  onUnpin,
}: ChatSessionDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [menuSessionId, setMenuSessionId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setMenuSessionId(null);
      }
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

  const currentSession = sessions.find((s) => s.session_id === currentSessionId);

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 rounded-lg border border-pine-200 bg-white/50 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50 transition-colors"
      >
        <span className="truncate max-w-32">
          {currentSession?.title || "当前会话"}
        </span>
        <ChevronDown className="h-3.5 w-3.5 text-pine-700" />
      </button>

      {isOpen && (
        <div className="absolute right-0 top-10 z-50 w-64 max-h-80 overflow-auto rounded-lg border border-pine-200 bg-white/50 shadow-xl">
          <div className="sticky top-0 border-b border-pine-200 bg-white/50 p-2">
            <button
              onClick={() => {
                onNewSession();
                setIsOpen(false);
              }}
              className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-pine-200 bg-white/50 px-3 py-1.5 text-xs text-pine-700 transition-colors hover:border-magic-500/30 hover:text-magic-400"
            >
              <Plus className="h-3.5 w-3.5" />
              新建对话
            </button>
          </div>

          <div className="p-2 space-y-2">
            {pinnedSessions.length > 0 && (
              <div>
                <div className="px-2 py-1 text-[10px] font-medium text-pine-700 uppercase tracking-wider">
                  置顶
                </div>
                {pinnedSessions.map((s) => (
                  <SessionItem
                    key={s.session_id}
                    session={s}
                    isActive={s.session_id === currentSessionId}
                    currentMessages={s.session_id === currentSessionId ? currentMessages : undefined}
                    onSelect={() => {
                      onSelectSession(s.session_id);
                      setIsOpen(false);
                    }}
                    menuSessionId={menuSessionId}
                    onMenuOpen={(id) => setMenuSessionId(id === null ? null : id === menuSessionId ? null : id)}
                    onStartRename={handleStartRename}
                    onDelete={handleDelete}
                    onPin={onPin}
                    onUnpin={onUnpin}
                    isEditing={editingId === s.session_id}
                    editTitle={editTitle}
                    onEditTitleChange={setEditTitle}
                    onFinishRename={handleFinishRename}
                  />
                ))}
              </div>
            )}

            {Object.entries(grouped).map(([group, items]) => (
              <div key={group}>
                <div className="px-2 py-1 text-[10px] font-medium text-pine-700 uppercase tracking-wider">
                  {group}
                </div>
                {items.map((s) => (
                  <SessionItem
                    key={s.session_id}
                    session={s}
                    isActive={s.session_id === currentSessionId}
                    currentMessages={s.session_id === currentSessionId ? currentMessages : undefined}
                    onSelect={() => {
                      onSelectSession(s.session_id);
                      setIsOpen(false);
                    }}
                    menuSessionId={menuSessionId}
                    onMenuOpen={(id) => setMenuSessionId(id === null ? null : id === menuSessionId ? null : id)}
                    onStartRename={handleStartRename}
                    onDelete={handleDelete}
                    onPin={onPin}
                    onUnpin={onUnpin}
                    isEditing={editingId === s.session_id}
                    editTitle={editTitle}
                    onEditTitleChange={setEditTitle}
                    onFinishRename={handleFinishRename}
                  />
                ))}
              </div>
            ))}

            {sessions.length === 0 && (
              <div className="py-8 text-center text-xs text-pine-700">
                暂无对话记录
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

interface SessionItemProps {
  session: ChatSessionItem;
  isActive: boolean;
  currentMessages?: ChatMessageItem[];
  onSelect: () => void;
  menuSessionId: string | null;
  onMenuOpen: (sessionId: string | null) => void;
  onStartRename: (sessionId: string, title: string) => void;
  onDelete: (sessionId: string) => Promise<void>;
  onPin: (sessionId: string) => Promise<void>;
  onUnpin: (sessionId: string) => Promise<void>;
  isEditing: boolean;
  editTitle: string;
  onEditTitleChange: (v: string) => void;
  onFinishRename: () => void;
}

function SessionItem({
  session,
  isActive,
  currentMessages,
  onSelect,
  menuSessionId,
  onMenuOpen,
  onStartRename,
  onDelete,
  onPin,
  onUnpin,
  isEditing,
  editTitle,
  onEditTitleChange,
  onFinishRename,
}: SessionItemProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  return (
    <div
      className={`group relative flex items-start gap-2 rounded-lg px-2 py-1.5 cursor-pointer transition-colors ${
        isActive
          ? "bg-magic-500/10 border-l-2 border-magic-500"
          : "hover:bg-white/50"
      }`}
      onClick={onSelect}
    >
      <div className="min-w-0 flex-1">
        {isEditing ? (
          <input
            value={editTitle}
            onChange={(e) => onEditTitleChange(e.target.value)}
            onBlur={onFinishRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") onFinishRename();
              if (e.key === "Escape") {
                onEditTitleChange("");
                onFinishRename();
              }
            }}
            className="w-full rounded border border-magic-500/30 bg-white/50 px-1 py-0.5 text-xs text-pine-700 outline-none"
            autoFocus
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <p className="truncate text-xs text-pine-700 group-hover:text-pine-700">
            {session.title || "未命名对话"}
          </p>
        )}
        <p className="mt-0.5 text-[10px] text-pine-700">
          {session.message_count}条消息
        </p>
      </div>

      <button
        onClick={(e) => {
          e.stopPropagation();
          onMenuOpen(session.session_id);
        }}
        className="shrink-0 rounded p-0.5 text-pine-700 opacity-0 transition-opacity group-hover:opacity-100 hover:text-pine-700"
      >
        <MoreHorizontal className="h-3 w-3" />
      </button>

      {menuSessionId === session.session_id && (
        <div
          ref={menuRef}
          className="absolute right-0 top-6 z-20 w-32 rounded-lg border border-pine-200 bg-white/50 py-1 shadow-lg"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            onClick={() => onStartRename(session.session_id, session.title)}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50"
          >
            <Pencil className="h-3 w-3" /> 重命名
          </button>
          {session.is_pinned ? (
            <button
              onClick={async () => {
                await onUnpin(session.session_id);
                onMenuOpen(null);
              }}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50"
            >
              <PinOff className="h-3 w-3" /> 取消置顶
            </button>
          ) : (
            <button
              onClick={async () => {
                await onPin(session.session_id);
                onMenuOpen(null);
              }}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50"
            >
              <Pin className="h-3 w-3" /> 置顶
            </button>
          )}
          {isActive && currentMessages && currentMessages.length > 0 && (
            <>
              <button
                onClick={() => {
                  const md = exportAsMarkdown(currentMessages, session.title);
                  downloadFile(md, `${session.title || "聊天记录"}.md`, "text/markdown;charset=utf-8");
                  onMenuOpen(null);
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50"
              >
                <Download className="h-3 w-3" /> 导出 Markdown
              </button>
              <button
                onClick={() => {
                  const json = exportAsJson(currentMessages, session.title);
                  downloadFile(json, `${session.title || "聊天记录"}.json`, "application/json;charset=utf-8");
                  onMenuOpen(null);
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50"
              >
                <Download className="h-3 w-3" /> 导出 JSON
              </button>
            </>
          )}
          <button
            onClick={() => onDelete(session.session_id)}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-crimson-400 hover:bg-white/50"
          >
            <Trash2 className="h-3 w-3" /> 删除
          </button>
        </div>
      )}
    </div>
  );
}
