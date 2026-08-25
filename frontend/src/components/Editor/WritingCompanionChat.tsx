import { useCallback, useEffect, useRef, useState } from "react";
import {
  Feather,
  Loader2,
  PanelLeft,
  PanelLeftClose,
  Search,
  Send,
  StopCircle,
} from "lucide-react";

import * as api from "@/api/client";
import ChatErrorBoundary from "@/components/Chat/ChatErrorBoundary";
import ChatMessageBubble, { DateDivider } from "@/components/Chat/ChatMessageBubble";
import ChatSearchBar, { highlightText, useChatSearch } from "@/components/Chat/ChatSearchBar";
import ChatSidebar from "@/components/Chat/ChatSidebar";
import ChatSkeleton from "@/components/Chat/ChatSkeleton";
import { useChatSession } from "@/hooks/useChatSession";


interface WritingCompanionChatProps {
  projectId: string;
  chapterNumber: number;
  draftInput?: { id: string; text: string } | null;
  onDraftInputConsumed?: (id: string) => void;
}


function shouldShowDateDivider(currentDate?: string, previousDate?: string) {
  if (!currentDate || !previousDate) return false;
  const current = new Date(currentDate);
  const previous = new Date(previousDate);
  return current.toDateString() !== previous.toDateString();
}


export default function WritingCompanionChat({
  projectId,
  chapterNumber,
  draftInput,
  onDraftInputConsumed,
}: WritingCompanionChatProps) {
  const [input, setInput] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const chat = useChatSession({
    projectId,
    agentType: "writing_companion",
    chapterNumber,
    welcomeMessage:
      "我是墨伴。可以和我讨论这一章的场景、人物反应和文字表达；需要修改时，我会先给出明确范围和候选文本，由你决定是否采用。",
  });
  const chatSearch = useChatSearch(chat.messages);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.messages]);

  useEffect(() => {
    if (!draftInput) return;
    setInput((current) => current.trim()
      ? `${current.trim()}\n\n${draftInput.text}`
      : draftInput.text);
    onDraftInputConsumed?.(draftInput.id);
  }, [draftInput, onDraftInputConsumed]);

  const stopResponse = useCallback(() => {
    abortControllerRef.current?.abort();
  }, []);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || chat.sending) return;

    const userMessage = {
      role: "user" as const,
      content: text,
      created_at: new Date().toISOString(),
    };
    const requestMessages = [...chat.messages, userMessage];
    chat.setMessages(requestMessages);
    setInput("");
    chat.setSending(true);
    abortControllerRef.current = new AbortController();
    let assistantContent = "";
    let finalized = false;

    const finalize = (content: string) => {
      if (finalized) return;
      finalized = true;
      const response = {
        role: "assistant" as const,
        content: content || "（墨伴没有返回内容）",
        created_at: new Date().toISOString(),
      };
      const messages = [...requestMessages, response];
      chat.setMessages(messages);
      chat.persistMessages(messages);
      chat.setSending(false);
    };

    try {
      await api.writingCompanionChatStream(
        projectId,
        requestMessages.map((message) => ({ role: message.role, content: message.content })),
        chapterNumber,
        (delta) => {
          assistantContent += delta;
          chat.setMessages((current) => {
            const next = [...current];
            const last = next.length - 1;
            if (last >= 0 && next[last].role === "assistant") {
              next[last] = { ...next[last], content: assistantContent };
            } else {
              next.push({
                role: "assistant",
                content: assistantContent,
                created_at: new Date().toISOString(),
              });
            }
            return next;
          });
        },
        () => finalize(assistantContent),
        (error) => finalize(`抱歉，墨伴暂时无法回应：${error}`),
        abortControllerRef.current.signal,
      );
    } catch (error) {
      finalize(`抱歉，墨伴暂时无法回应：${(error as Error).message}`);
    } finally {
      if (!finalized) chat.setSending(false);
      abortControllerRef.current = null;
    }
  };

  return (
    <ChatErrorBoundary agentName="墨伴">
      <div className="flex h-full">
        {sidebarOpen && (
          <ChatSidebar
            projectId={projectId}
            agentType="writing_companion"
            sessions={chat.sessions}
            currentSessionId={chat.currentSessionId}
            currentMessages={chat.messages}
            onSelectSession={chat.switchSession}
            onNewSession={chat.createNewSession}
            onRename={chat.renameSession}
            onDelete={chat.deleteSession}
            onPin={chat.pinSession}
            onUnpin={chat.unpinSession}
            onSearch={chat.searchSessions}
          />
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center gap-3 border-b border-pine-200/40 px-4 py-3">
            <button
              type="button"
              onClick={() => setSidebarOpen((open) => !open)}
              className="rounded-lg p-1 text-pine-700 transition-colors hover:bg-white/50"
              aria-label="切换墨伴会话列表"
            >
              {sidebarOpen
                ? <PanelLeftClose className="h-4 w-4" />
                : <PanelLeft className="h-4 w-4" />}
            </button>
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-magic-500/15">
              <Feather className="h-3.5 w-3.5 text-magic-600" />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-semibold text-pine-950">墨伴</h3>
              <p className="truncate text-xs text-pine-600">第{chapterNumber}章 · 灵感与修改方案</p>
            </div>
            <button
              type="button"
              onClick={() => setSearchOpen((open) => !open)}
              className="rounded-lg p-1 text-pine-700 transition-colors hover:bg-white/50"
              aria-label="搜索墨伴对话"
            >
              <Search className="h-4 w-4" />
            </button>
          </div>

          {searchOpen && (
            <div className="flex justify-end border-b border-pine-200/20 px-4 py-1.5">
              <ChatSearchBar
                onSearch={chatSearch.setSearchQuery}
                onClose={() => {
                  setSearchOpen(false);
                  chatSearch.setSearchQuery("");
                }}
                matchCount={chatSearch.matchCount}
                currentMatch={chatSearch.currentMatch}
                onPrev={chatSearch.goToPrev}
                onNext={chatSearch.goToNext}
              />
            </div>
          )}

          <div className="flex-1 space-y-3 overflow-auto px-4 py-3">
            {chat.loading ? <ChatSkeleton /> : chat.messages.map((message, index) => {
              const previous = index > 0 ? chat.messages[index - 1] : undefined;
              return (
                <div
                  key={`${message.created_at || "message"}-${index}`}
                  className={chatSearch.isCurrentMatch(index)
                    ? "rounded-lg ring-1 ring-magic-500/40"
                    : ""}
                >
                  {shouldShowDateDivider(message.created_at, previous?.created_at) && (
                    <DateDivider date={message.created_at || new Date().toISOString()} />
                  )}
                  <ChatMessageBubble
                    role={message.role}
                    content={chatSearch.searchQuery
                      ? highlightText(message.content, chatSearch.searchQuery)
                      : message.content}
                    copyText={message.content}
                    timestamp={message.created_at}
                  />
                </div>
              );
            })}
            {chat.sending && (
              <div className="flex items-center gap-2 text-sm text-pine-600">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                墨伴正在斟酌……
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          <div className="space-y-2 border-t border-pine-200/40 px-4 py-3">
            {!chat.sending && (
              <div className="flex flex-wrap gap-1.5">
                {["帮我推演下一步", "给这段增加张力", "检查人物反应", "提出局部改写方案"].map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => setInput(prompt)}
                    className="rounded-full border border-pine-200/70 bg-white/55 px-2.5 py-1 text-[11px] text-pine-700 hover:bg-white"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void handleSend();
                  }
                }}
                placeholder="和墨伴讨论本章，或请求一个明确范围的修改方案……"
                disabled={chat.sending}
                rows={1}
                className="max-h-28 min-h-9 flex-1 resize-none rounded-lg border border-pine-200 bg-white/60 px-3 py-2 text-sm text-pine-900 outline-none focus:border-magic-500/50 disabled:opacity-50"
              />
              <button
                type="button"
                onClick={chat.sending ? stopResponse : () => void handleSend()}
                disabled={!chat.sending && !input.trim()}
                className="flex h-9 w-9 items-center justify-center rounded-lg bg-pine-800 text-white transition-colors hover:bg-pine-900 disabled:opacity-40"
                aria-label={chat.sending ? "停止墨伴回应" : "发送给墨伴"}
              >
                {chat.sending
                  ? <StopCircle className="h-4 w-4" />
                  : <Send className="h-4 w-4" />}
              </button>
            </div>
            <p className="text-[10px] leading-4 text-pine-500">
              墨伴只提出候选方案；应用正文或蓝图修改前仍需你的确认。
            </p>
          </div>
        </div>
      </div>
    </ChatErrorBoundary>
  );
}
