import { useState, useCallback, useRef, useEffect } from "react";
import * as api from "@/api/client";
import type { ChatSessionItem, ChatMessageItem } from "@/types";

export interface UseChatSessionOptions {
  projectId: string;
  agentType: "outline_architect" | "worldbuilding" | "writing_companion";
  chapterNumber?: number;
  welcomeMessage?: string;
}

export interface UseChatSessionReturn {
  sessions: ChatSessionItem[];
  currentSessionId: string | null;
  messages: ChatMessageItem[];
  loading: boolean;
  sending: boolean;
  historyLoaded: boolean;
  createNewSession: () => void;
  switchSession: (sessionId: string) => Promise<void>;
  renameSession: (sessionId: string, name: string) => Promise<void>;
  deleteSession: (sessionId: string) => Promise<void>;
  pinSession: (sessionId: string) => Promise<void>;
  unpinSession: (sessionId: string) => Promise<void>;
  searchSessions: (query: string) => Promise<void>;
  persistMessages: (msgs: ChatMessageItem[]) => void;
  setMessages: React.Dispatch<React.SetStateAction<ChatMessageItem[]>>;
  setSending: React.Dispatch<React.SetStateAction<boolean>>;
  refreshSessions: () => Promise<void>;
}

export function useChatSession({
  projectId,
  agentType,
  chapterNumber,
  welcomeMessage,
}: UseChatSessionOptions): UseChatSessionReturn {
  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const [filteredSessions, setFilteredSessions] = useState<ChatSessionItem[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [searchQuery, setSearchQuery] = useState<string>("");

  const messagesRef = useRef<ChatMessageItem[]>([]);
  const currentSessionIdRef = useRef<string | null>(null);
  useEffect(() => { messagesRef.current = messages; }, [messages]);
  useEffect(() => { currentSessionIdRef.current = currentSessionId; }, [currentSessionId]);

  useEffect(() => {
    currentSessionIdRef.current = null;
    setCurrentSessionId(null);
    setMessages([]);
    setHistoryLoaded(false);
  }, [projectId, agentType, chapterNumber]);

  const refreshSessions = useCallback(async () => {
    if (!projectId) return;
    try {
      const result = await api.listChatSessions(projectId, agentType);
      setSessions(result.sessions);
      setFilteredSessions(result.sessions);
    } catch {}
  }, [projectId, agentType]);

  const searchSessions = useCallback(async (query: string) => {
    setSearchQuery(query);
    if (!query.trim()) {
      setFilteredSessions(sessions);
      return;
    }
    if (!projectId) return;
    try {
      const result = await api.searchChatSessions(projectId, query, agentType);
      setFilteredSessions(result.sessions);
    } catch {}
  }, [projectId, agentType, sessions]);

  useEffect(() => {
    if (historyLoaded || !projectId) return;

    const init = async () => {
      setLoading(true);
      try {
        await refreshSessions();
        const result = await api.getLatestChatSession(projectId, agentType, chapterNumber);
        if (result.session_id && result.messages && result.messages.length > 0) {
          setCurrentSessionId(result.session_id);
          setMessages(result.messages.map((m) => ({ 
            role: m.role as ChatMessageItem["role"], 
            content: m.content,
            created_at: new Date().toISOString()
          })));
        } else if (welcomeMessage) {
          setMessages([{ role: "assistant", content: welcomeMessage }]);
        }
      } catch {
        if (welcomeMessage) {
          setMessages([{ role: "assistant", content: welcomeMessage }]);
        }
      } finally {
        setLoading(false);
        setHistoryLoaded(true);
      }
    };
    init();
  }, [projectId, agentType, chapterNumber, historyLoaded, welcomeMessage, refreshSessions]);

  const createNewSession = useCallback(() => {
    currentSessionIdRef.current = null;
    setCurrentSessionId(null);
    if (welcomeMessage) {
      setMessages([{ role: "assistant", content: welcomeMessage }]);
    } else {
      setMessages([]);
    }
  }, [welcomeMessage]);

  const switchSession = useCallback(async (sessionId: string) => {
    if (!projectId) return;
    setLoading(true);
    try {
      const result = await api.getChatSessionMessages(projectId, sessionId);
      currentSessionIdRef.current = sessionId;
      setCurrentSessionId(sessionId);
      setMessages(
        result.messages.map((m) => ({
          role: m.role as ChatMessageItem["role"],
          content: m.content,
          id: m.id,
          created_at: m.created_at,
        }))
      );
    } catch {} finally {
      setLoading(false);
    }
  }, [projectId]);

  const renameSession = useCallback(async (sessionId: string, name: string) => {
    if (!projectId) return;
    try {
      await api.renameChatSession(projectId, sessionId, name);
      await refreshSessions();
    } catch {}
  }, [projectId, refreshSessions]);

  const deleteSession = useCallback(async (sessionId: string) => {
    if (!projectId) return;
    try {
      await api.deleteChatSession(projectId, sessionId);
      if (currentSessionId === sessionId) {
        currentSessionIdRef.current = null;
        setCurrentSessionId(null);
        if (welcomeMessage) {
          setMessages([{ role: "assistant", content: welcomeMessage }]);
        } else {
          setMessages([]);
        }
      }
      await refreshSessions();
    } catch {}
  }, [projectId, currentSessionId, welcomeMessage, refreshSessions]);

  const pinSession = useCallback(async (sessionId: string) => {
    if (!projectId) return;
    try {
      await api.pinChatSession(projectId, sessionId);
      await refreshSessions();
    } catch {}
  }, [projectId, refreshSessions]);

  const unpinSession = useCallback(async (sessionId: string) => {
    if (!projectId) return;
    try {
      await api.unpinChatSession(projectId, sessionId);
      await refreshSessions();
    } catch {}
  }, [projectId, refreshSessions]);

  const persistMessages = useCallback((msgs: ChatMessageItem[]) => {
    if (!projectId || msgs.length === 0) return;
    
    const messagesWithTimestamps = msgs.map(m => {
      if (!m.created_at) {
        return { ...m, created_at: new Date().toISOString() };
      }
      return m;
    });
    
    api.saveChatMessages(projectId, {
      agent_type: agentType,
      session_id: currentSessionIdRef.current,
      chapter_number: chapterNumber ?? null,
      messages: messagesWithTimestamps.map((m) => ({ role: m.role, content: m.content })),
    }).then((result) => {
      if (!currentSessionIdRef.current) {
        currentSessionIdRef.current = result.session_id;
        setCurrentSessionId(result.session_id);
      }
      refreshSessions();
    }).catch(() => {});
  }, [projectId, agentType, chapterNumber, refreshSessions]);

  return {
    sessions: searchQuery ? filteredSessions : sessions,
    currentSessionId,
    messages,
    loading,
    sending,
    historyLoaded,
    createNewSession,
    switchSession,
    renameSession,
    deleteSession,
    pinSession,
    unpinSession,
    searchSessions,
    persistMessages,
    setMessages,
    setSending,
    refreshSessions,
  };
}
