import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { useLocation, useParams } from "react-router-dom";
import {
  Globe,
  Users,
  MapPin,
  Plus,
  Trash2,
  Edit3,
  Lock,
  Unlock,
  ChevronDown,
  ChevronRight,
  Shield,
  Sparkles,
  ScrollText,
  Swords,
  FlaskConical,
  X,
  AlertTriangle,
  Eye,
  ArrowUpFromLine,
  Check,
  Loader2,
  BookOpen,
  Search,
  Send,
  Database,
  RefreshCw,
} from "lucide-react";
import clsx from "clsx";
import * as api from "@/api/client";
import type {
  WorldRule,
  Character,
  Location,
  ForeshadowingLine,
  WorldbuildingOverview,
  PromotionProposal,
  ChatMessageItem,
  ForeshadowingDashboard,
  RevealReadiness,
  WorldviewObservation,
  WorldviewObservationList,
  WorldviewObservationListParams,
  WorldviewObservationReviewCounts,
} from "@/types";
import ChatMessageBubble, { DateDivider } from "@/components/Chat/ChatMessageBubble";
import ChatSessionDropdown from "@/components/Chat/ChatSessionDropdown";
import ChatErrorBoundary from "@/components/Chat/ChatErrorBoundary";
import ChatSkeleton from "@/components/Chat/ChatSkeleton";
import ChatSearchBar, { useChatSearch, highlightText } from "@/components/Chat/ChatSearchBar";
import WorldRuleFormModal from "@/components/Worldbuilding/WorldRuleFormModal";
import { useChatSession } from "@/hooks/useChatSession";
import {
  DEFAULT_OBSERVATION_PAGE_SIZE,
  useWorldbuildingStore,
} from "@/stores/worldbuildingStore";
import { motion, AnimatePresence } from "framer-motion";

/* Hallmark · pre-emit critique: P5 H5 E4 S5 R5 V4 · observation review queue */

type TabKey = "overview" | "rules" | "characters" | "locations" | "foreshadowing" | "promotions";

// 正文发现与晋升共用同一个“设定变更”工作台。保留旧 discoveries 链接的
// 兼容映射，避免历史书签或后端导航契约失效。
const normalizeWorldbuildingTab = (tab: string | null): TabKey => {
  if (tab === "discoveries") return "promotions";
  if (tab && ["overview", "rules", "characters", "locations", "foreshadowing", "promotions"].includes(tab)) {
    return tab as TabKey;
  }
  return "overview";
};

const ASSISTANT_ROLE_LABELS: Record<string, string> = {
  overview: "世界观导师",
  rules: "规则架构师",
  characters: "人物铸造师",
  locations: "地理编织师",
  foreshadowing: "伏笔织网师",
  promotions: "设定变更审阅师",
};

const CATEGORY_ICONS: Record<string, typeof Shield> = {
  magic: Sparkles,
  technology: FlaskConical,
  society: Shield,
  combat: Swords,
  history: ScrollText,
};

const CATEGORY_LABELS: Record<string, string> = {
  magic: "魔法体系",
  technology: "科技水平",
  society: "社会结构",
  combat: "战斗规则",
  history: "历史法则",
  general: "通用规则",
  inferred: "正文推断",
};

const PRIORITY_CONFIG: Record<string, { label: string; color: string }> = {
  critical: { label: "宪法级", color: "text-crimson-400 bg-crimson-500/15 border-crimson-500/30" },
  high: { label: "高优先", color: "text-magic-400 bg-magic-500/15 border-magic-500/30" },
  normal: { label: "普通", color: "text-pine-700 bg-white/50 border-pine-200/30" },
  low: { label: "低优先", color: "text-pine-700 bg-white/50 border-pine-200/30" },
};

function useDelayedBusy(active: boolean, delay = 150) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!active) {
      setVisible(false);
      return;
    }
    const timer = window.setTimeout(() => setVisible(true), delay);
    return () => window.clearTimeout(timer);
  }, [active, delay]);

  return visible;
}

function WorldbuildingInitialSkeleton() {
  return (
    <div
      className="mx-auto max-w-4xl space-y-6"
      role="status"
      aria-live="polite"
      aria-label="正在整理世界规则、人物、地点和伏笔"
    >
      <span className="sr-only">正在整理世界规则、人物、地点和伏笔</span>
      <div
        className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5"
        aria-hidden="true"
      >
        {Array.from({ length: 5 }, (_, index) => (
          <div
            key={index}
            className="min-h-36 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-5"
          >
            <div className="h-10 w-10 animate-pulse rounded-lg bg-[var(--surface-tool)] motion-reduce:animate-none" />
            <div className="mt-4 h-7 w-12 animate-pulse rounded bg-[var(--border-subtle)] motion-reduce:animate-none" />
            <div className="mt-2 h-3 w-20 animate-pulse rounded bg-[var(--border-subtle)] motion-reduce:animate-none" />
          </div>
        ))}
      </div>
      <div
        className="space-y-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-paper)] p-5"
        aria-hidden="true"
      >
        <div className="h-4 w-28 animate-pulse rounded bg-[var(--border-subtle)] motion-reduce:animate-none" />
        {Array.from({ length: 4 }, (_, index) => (
          <div key={index} className="flex items-center gap-3">
            <div className="h-4 w-4 animate-pulse rounded bg-[var(--border-subtle)] motion-reduce:animate-none" />
            <div className="h-3 w-20 animate-pulse rounded bg-[var(--border-subtle)] motion-reduce:animate-none" />
            <div className="h-2 min-w-0 flex-1 animate-pulse rounded bg-[var(--surface-tool)] motion-reduce:animate-none" />
          </div>
        ))}
      </div>
    </div>
  );
}

function WorldbuildingLoadError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="mx-auto flex min-h-72 max-w-xl flex-col items-center justify-center text-center" role="alert">
      <AlertTriangle className="h-8 w-8 text-[var(--color-error)]" />
      <h2 className="mt-4 text-base font-semibold text-[var(--color-ink-strong)]">世界观数据暂时无法载入</h2>
      <p className="mt-2 text-sm text-[var(--color-ink-muted)]">页面框架已经就绪，可以重试数据连接。</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-5 min-h-11 whitespace-nowrap rounded-md border border-[var(--border-emphasis)] bg-[var(--surface-raised)] px-4 text-sm font-medium text-[var(--color-ink)] transition-colors hover:bg-[var(--color-accent-soft)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--focus-ring)] active:translate-y-px motion-reduce:transform-none"
      >
        重新加载
      </button>
    </div>
  );
}

export default function WorldbuildingPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const location = useLocation();
  const initialTab = new URLSearchParams(location.search).get("tab");
  const [activeTab, setActiveTab] = useState<TabKey>(() => normalizeWorldbuildingTab(initialTab));
  const {
    projectId: loadedProjectId,
    overview,
    rules,
    characters,
    locations,
    promotions,
    observations,
    observationCounts,
    observationTotal,
    observationPage,
    observationPageSize,
    observationTotalPages,
    observationHasMore,
    loading,
    promotionsLoading,
    observationsLoading,
    promotionsLoaded,
    observationsLoaded,
    error,
    promotionsError,
    observationsError,
    fetchData,
    fetchPromotions,
    fetchObservations,
  } = useWorldbuildingStore();

  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>({});
  const [fillData, setFillData] = useState<Record<string, unknown> | null>(null);

  const handleFillForm = (data: Record<string, unknown>) => {
    setFillData(data);
    setTimeout(() => setFillData(null), 100);
  };

  useEffect(() => {
    if (projectId) {
      fetchData(projectId, true).then(() => {
        // Init expanded categories if rules were loaded
        const currentRules = useWorldbuildingStore.getState().rules;
        if (currentRules.length > 0) {
          const cats: Record<string, boolean> = {};
          currentRules.forEach((r) => {
            if (!(r.category in cats)) cats[r.category] = true;
          });
          setExpandedCategories((prev) => Object.keys(prev).length === 0 ? cats : prev);
        }
      });
    }
  }, [projectId, fetchData]);

  useEffect(() => {
    const tab = new URLSearchParams(location.search).get("tab");
    setActiveTab(normalizeWorldbuildingTab(tab));
  }, [location.search]);

  useEffect(() => {
    if (activeTab !== "promotions" || !projectId) return;
    void Promise.all([
      fetchPromotions(projectId, true),
      fetchObservations(projectId, {
        reviewState: "pending",
        page: 1,
        pageSize: DEFAULT_OBSERVATION_PAGE_SIZE,
      }, true),
    ]);
  }, [activeTab, projectId, fetchObservations, fetchPromotions]);

  const toggleCategory = (cat: string) => {
    setExpandedCategories((prev) => ({ ...prev, [cat]: !prev[cat] }));
  };

  const rulesByCategory = rules.reduce<Record<string, WorldRule[]>>((acc, rule) => {
    const cat = rule.category || "general";
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(rule);
    return acc;
  }, {});

  const tabs: { key: TabKey; label: string; icon: typeof Globe; beta?: boolean }[] = [
    { key: "overview", label: "概览", icon: Globe },
    { key: "rules", label: "世界规则", icon: Shield },
    { key: "characters", label: "人物", icon: Users },
    { key: "locations", label: "地点", icon: MapPin },
    { key: "foreshadowing", label: "伏笔", icon: Eye },
    { key: "promotions", label: "晋升与发现", icon: ArrowUpFromLine },
  ];

  const initialLoading = loadedProjectId !== projectId || (!overview && !error);
  const promotionDataLoading = !promotionsLoaded || !observationsLoaded;
  const promotionDataRefreshing = promotionsLoading || observationsLoading;
  const showSyncStatus = useDelayedBusy(
    loading || (activeTab === "promotions" && promotionDataRefreshing),
  );

  return (
    <div className="worldbuilding-layout flex h-full gap-4 p-4 lg:p-6 bg-white/50">
      <div
        className="worldbuilding-main flex flex-1 flex-col rounded-2xl glass-panel shadow-2xl overflow-hidden relative z-10"
        aria-busy={initialLoading || loading || (activeTab === "promotions" && promotionDataRefreshing)}
      >
        <div className="shrink-0 border-b border-pine-200/40 bg-white/50 px-8 py-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex items-center gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-magic-500/20 to-magic-600/5 border border-magic-500/20 shadow-glass">
                <Globe className="h-6 w-6 text-magic-400" />
              </div>
              <div>
                <h1 className="font-display text-2xl font-semibold tracking-tight text-[var(--color-ink-strong)]">世界观法则</h1>
                <p className="text-sm text-pine-700 mt-1">管理规则、人物、地点与伏笔等世界设定</p>
              </div>
            </div>
            {showSyncStatus && (
              <div
                className="flex min-h-8 shrink-0 items-center gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-tool)] px-3 text-xs text-[var(--color-ink-muted)]"
                role="status"
                aria-live="polite"
              >
                <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-accent)] motion-reduce:animate-none" />
                {initialLoading ? "正在整理世界设定" : "正在同步最新数据"}
              </div>
            )}
          </div>
          <div className="mt-6 flex gap-2 overflow-x-auto scrollbar-none pb-1">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.key;
              return (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className="relative flex shrink-0 items-center gap-2 whitespace-nowrap rounded-lg px-4 py-2.5 text-sm transition-colors group outline-none"
                >
                  {isActive && (
                    <motion.div 
                      layoutId="worldbuilding-tab"
                      className="absolute inset-0 bg-magic-500/15 border border-magic-500/30 rounded-lg"
                    />
                  )}
                  <Icon className={clsx("h-4 w-4 relative z-10 transition-colors", isActive ? "text-magic-400" : "text-pine-700 group-hover:text-pine-700")} />
                  <span className={clsx("relative z-10 font-medium transition-colors", isActive ? "text-magic-400" : "text-pine-700 group-hover:text-pine-700")}>
                    {tab.label}
                  </span>
                  {tab.beta && (
                    <span className="relative z-10 ml-1.5 rounded bg-violet-500/20 px-1.5 py-0.5 text-[9px] font-bold text-violet-400 border border-violet-500/20">
                      BETA
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex-1 overflow-auto p-8 relative">
          {initialLoading ? (
            <WorldbuildingInitialSkeleton />
          ) : error && !overview ? (
            <WorldbuildingLoadError onRetry={() => projectId && void fetchData(projectId, true)} />
          ) : (
            <AnimatePresence mode="wait">
              <motion.div
                key={activeTab}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="h-full"
              >
          {activeTab === "overview" && overview && (
            <OverviewSection
              overview={overview}
              onNavigate={setActiveTab}
            />
          )}
          {activeTab === "rules" && projectId && (
            <RulesSection
              projectId={projectId}
              rules={rules}
              rulesByCategory={rulesByCategory}
              expandedCategories={expandedCategories}
              onToggleCategory={toggleCategory}
              onRefresh={() => fetchData(projectId, true)}
              fillData={fillData}
            />
          )}
          {activeTab === "characters" && projectId && (
            <CharactersSection
              projectId={projectId}
              characters={characters}
              onRefresh={() => fetchData(projectId, true)}
              fillData={fillData}
            />
          )}
          {activeTab === "locations" && projectId && (
            <EnhancedLocationsSection
              projectId={projectId}
              locations={locations}
              onRefresh={() => fetchData(projectId, true)}
              fillData={fillData}
            />
          )}
          {activeTab === "foreshadowing" && projectId && (
            <ForeshadowingSection
              projectId={projectId}
              onRefresh={() => fetchData(projectId, true)}
              fillData={fillData}
            />
          )}
          {activeTab === "promotions" && projectId && (
            <PromotionDiscoverySection
              projectId={projectId}
              promotions={promotions}
              observations={observations}
              observationCounts={observationCounts}
              observationTotal={observationTotal}
              observationPage={observationPage}
              observationPageSize={observationPageSize}
              observationTotalPages={observationTotalPages}
              observationHasMore={observationHasMore}
              initialLoading={promotionDataLoading}
              refreshing={promotionDataRefreshing}
              loadError={promotionsError || observationsError}
              onRefresh={() => void Promise.all([
                fetchData(projectId, true),
                fetchPromotions(projectId, true),
              ])}
              onRefreshObservations={async (options) => {
                const [, result] = await Promise.all([
                  fetchData(projectId, true),
                  fetchObservations(projectId, options, true),
                ]);
                return result;
              }}
              onRetry={() => void Promise.all([
                fetchPromotions(projectId, true),
                fetchObservations(projectId, {
                  reviewState: "pending",
                  page: 1,
                  pageSize: DEFAULT_OBSERVATION_PAGE_SIZE,
                }, true),
              ])}
            />
          )}
              </motion.div>
            </AnimatePresence>
          )}
        </div>
      </div>

      <WorldbuildingChatPanel
        projectId={projectId || ""}
        activeTab={activeTab}
        onFillForm={(data) => handleFillForm(data)}
      />
    </div>
  );
}

function WorldbuildingChatPanel({
  projectId,
  activeTab,
  onFillForm,
}: {
  projectId: string;
  activeTab: TabKey;
  onFillForm: (data: Record<string, unknown>) => void;
}) {
  type ChatMsg = ChatMessageItem & { suggestions?: Record<string, unknown>[]; mentions?: { type: string; name: string }[]; navigates?: { tab: string; reason: string }[] };
  const [input, setInput] = useState("");
  const [savingIdx, setSavingIdx] = useState<number | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const chat = useChatSession({
    projectId,
    agentType: "worldbuilding",
  });
  const messages = chat.messages as ChatMsg[];
  const sending = chat.sending;
  const chatSearch = useChatSearch(chat.messages);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.messages]);

  const TAB_LABELS: Record<string, string> = {
    overview: "概览",
    rules: "世界规则",
    characters: "人物",
    locations: "地点",
    foreshadowing: "伏笔",
    promotions: "设定变更",
    style: "文笔",
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending) return;

    const userMsg: ChatMsg = { role: "user", content: text, created_at: new Date().toISOString() };
    const newMessages = [...messages, userMsg];
    chat.setMessages(newMessages);
    setInput("");
    chat.setSending(true);

    try {
      const result = await api.worldbuildingChat(projectId, newMessages, activeTab);
      const suggestionItems = Array.isArray(result.suggestions) ? result.suggestions : undefined;
      const assistantMsg: ChatMsg = {
        role: "assistant",
        content: result.response,
        created_at: new Date().toISOString(),
        suggestions: suggestionItems && suggestionItems.length > 0 ? suggestionItems : undefined,
        mentions: result.mentions || [],
        navigates: result.navigates || [],
      };
      chat.setMessages((prev) => [...prev, assistantMsg]);

      if (result.suggestions && !Array.isArray(result.suggestions)) {
        onFillForm(result.suggestions as Record<string, unknown>);
      }

      chat.persistMessages([...newMessages, assistantMsg]);
    } catch (e) {
      chat.setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `抱歉，出了点问题：${(e as Error).message}`, created_at: new Date().toISOString() },
      ]);
    } finally {
      chat.setSending(false);
    }
  };

  const handleQuickSave = async (idx: number, suggestions: Record<string, unknown>[]) => {
    setSavingIdx(idx);
    const savedNames: string[] = [];
    const failedItems: string[] = [];
    try {
      for (const item of suggestions) {
        const saveType = guessSuggestionType(item);
        const name = (item.name as string) || "未命名";
        try {
          if (saveType === "rules") {
            await api.createWorldRule(projectId, item);
          } else if (saveType === "characters") {
            await api.createCharacter(projectId, item);
          } else if (saveType === "locations") {
            await api.createLocation(projectId, item);
          } else if (saveType === "foreshadowing") {
            await api.createForeshadowing(projectId, item);
          } else {
            failedItems.push(name);
            continue;
          }
          savedNames.push(name);
        } catch {
          failedItems.push(name);
        }
      }
      chat.setMessages((prev) =>
        prev.map((m, i) => (i === idx ? { ...m, suggestions: undefined } : m))
      );
      if (savedNames.length > 0) {
        chat.setMessages((prev) => [
          ...prev,
          { role: "system", content: `✅ 已录入 ${savedNames.length} 项设定：${savedNames.map(n => `「${n}」`).join("、")}`, created_at: new Date().toISOString() },
        ]);
      }
      if (failedItems.length > 0) {
        chat.setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `以下设定未能录入：${failedItems.map(n => `「${n}」`).join("、")}。请尝试手动添加。`, created_at: new Date().toISOString() },
        ]);
      }
    } catch {
      //
    } finally {
      setSavingIdx(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const shouldShowDateDivider = (currentDate: string | undefined, prevDate: string | undefined) => {
    if (!prevDate || !currentDate) return false;
    try {
      const curr = new Date(currentDate);
      const prev = new Date(prevDate);
      return (
        curr.getDate() !== prev.getDate() ||
        curr.getMonth() !== prev.getMonth() ||
        curr.getFullYear() !== prev.getFullYear()
      );
    } catch {
      return false;
    }
  };

  const chatSupportedTabs = ["rules", "characters", "locations", "foreshadowing", "overview", "promotions"];
  if (!chatSupportedTabs.includes(activeTab)) return null;

  return (
    <ChatErrorBoundary agentName="世界观构建师">
    <div className="worldbuilding-assistant-shell">
    <aside className="worldbuilding-assistant-panel flex h-full w-full shrink-0 flex-col rounded-xl glass-panel shadow-2xl overflow-hidden relative z-20" aria-label="世界观构建助手">
      <div className="flex items-center justify-between border-b border-pine-200/40 bg-white/50 px-6 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-magic-500/20 to-magic-600/5 border border-magic-500/20">
            <Globe className="h-4 w-4 text-magic-400" />
          </div>
          <div>
            <div className="text-sm font-semibold text-[var(--color-ink-strong)]">构建师助手</div>
            <div className="mt-0.5 text-[10px] tracking-widest text-pine-700">
              {ASSISTANT_ROLE_LABELS[activeTab] || TAB_LABELS[activeTab] || "世界观"}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setSearchOpen(!searchOpen)}
            className="rounded-lg p-1 text-pine-700 hover:text-pine-700 hover:bg-white/50"
          >
            <Search className="h-4 w-4" />
          </button>
          <ChatSessionDropdown
            projectId={projectId}
            agentType="worldbuilding"
            sessions={chat.sessions}
            currentSessionId={chat.currentSessionId}
            currentMessages={chat.messages}
            onSelectSession={chat.switchSession}
            onNewSession={chat.createNewSession}
            onRename={chat.renameSession}
            onDelete={chat.deleteSession}
            onPin={chat.pinSession}
            onUnpin={chat.unpinSession}
          />
        </div>
      </div>

      {searchOpen && (
        <div className="flex justify-end px-4 py-1.5 border-b border-pine-200/20">
          <ChatSearchBar
            onSearch={chatSearch.setSearchQuery}
            onClose={() => { setSearchOpen(false); chatSearch.setSearchQuery(""); }}
            matchCount={chatSearch.matchCount}
            currentMatch={chatSearch.currentMatch}
            onPrev={chatSearch.goToPrev}
            onNext={chatSearch.goToNext}
          />
        </div>
      )}

      <div className="flex-1 overflow-auto p-4 space-y-3">
        {chat.loading ? (
          <ChatSkeleton />
        ) : (
        <>
        {messages.length === 0 && (
          <div className="flex flex-col items-center py-8 text-center">
            <Globe className="mb-3 h-10 w-10 text-pine-700" />
            <p className="text-sm text-pine-700">{ASSISTANT_ROLE_LABELS[activeTab] || "世界观构建师"}</p>
            <p className="mt-1 text-xs text-pine-700">
              告诉我你的想法，我来帮你构建{TAB_LABELS[activeTab] || "世界观"}
            </p>
            <div className="mt-4 space-y-2">
              {activeTab === "rules" && (
                <>
                  <button onClick={() => { setInput("帮我设计一套魔法体系"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">帮我设计一套魔法体系</button>
                  <button onClick={() => { setInput("这个世界的社会阶层如何划分？"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">这个世界的社会阶层如何划分？</button>
                </>
              )}
              {activeTab === "characters" && (
                <>
                  <button onClick={() => { setInput("帮我设计一个亦正亦邪的反派"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">帮我设计一个亦正亦邪的反派</button>
                  <button onClick={() => { setInput("设计一个有成长弧光的女主角"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">设计一个有成长弧光的女主角</button>
                </>
              )}
              {activeTab === "locations" && (
                <>
                  <button onClick={() => { setInput("设计一个神秘的修炼圣地"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">设计一个神秘的修炼圣地</button>
                  <button onClick={() => { setInput("帮我构建一个繁华的修仙城池"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">帮我构建一个繁华的修仙城池</button>
                </>
              )}
              {activeTab === "foreshadowing" && (
                <>
                  <button onClick={() => { setInput("帮我设计一条贯穿全书的伏笔"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">帮我设计一条贯穿全书的伏笔</button>
                  <button onClick={() => { setInput("设计一个反转伏笔"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">设计一个反转伏笔</button>
                </>
              )}
              {activeTab === "overview" && (
                <>
                  <button onClick={() => { setInput("检查一下我的世界观覆盖度"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">检查世界观覆盖度</button>
                  <button onClick={() => { setInput("我接下来该做什么？"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">我接下来该做什么？</button>
                </>
              )}
              {activeTab === "promotions" && (
                <>
                  <button onClick={() => { setInput("帮我核对当前待确认的正文发现，并判断哪些内容值得晋升为正式设定"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">核对发现与晋升</button>
                  <button onClick={() => { setInput("帮我审查待晋升的设定种子"); }} className="block w-full rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400">审查待晋升种子</button>
                </>
              )}
            </div>
          </div>
        )}
        {messages.map((msg, i) => {
          const prevMsg = i > 0 ? messages[i - 1] : undefined;
          const showDateDivider = shouldShowDateDivider(msg.created_at, prevMsg?.created_at);
          const isSearchCurrent = chatSearch.isCurrentMatch(i);
          const strippedContent = msg.role !== "user"
            ? msg.content.replace(/```json\s*\n?\{[\s\S]*?"fill"[\s\S]*?\}\s*\n?```/g, "").trim()
            : msg.content;
          const displayContent = chatSearch.searchQuery
            ? highlightText(strippedContent, chatSearch.searchQuery)
            : strippedContent;
          return (
            <div
              key={i}
              data-msg-index={i}
              className={`transition-all duration-300 ${isSearchCurrent ? "ring-1 ring-magic-500/40 rounded-lg" : ""}`}
            >
              {showDateDivider && <DateDivider date={msg.created_at || new Date().toISOString()} />}
              {msg.role === "system" ? (
                <div className="flex items-center justify-center py-1">
                  <span className="rounded-full bg-white/50 px-3 py-0.5 text-[10px] text-pine-700">
                    {msg.content}
                  </span>
                </div>
              ) : (
                <>
                  <ChatMessageBubble
                    role={msg.role}
                    content={displayContent}
                    copyText={msg.content}
                    timestamp={msg.created_at}
                  />
                  {msg.suggestions && msg.suggestions.length > 0 && (
                    <div className="mt-3 ml-4 rounded-lg border border-pine-200 bg-white/50 p-3 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-medium text-pine-700">快速创建建议</span>
                        <button
                          onClick={() => handleQuickSave(i, msg.suggestions!)}
                          disabled={savingIdx === i}
                          className="flex items-center gap-1 rounded bg-magic-500/20 px-2 py-1 text-xs text-magic-400 hover:bg-magic-500/30 transition-colors disabled:opacity-50"
                        >
                          {savingIdx === i ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Plus className="h-3 w-3" />
                          )}
                          全部创建
                        </button>
                      </div>
                      {msg.suggestions.map((s, j) => (
                        <div key={j} className="flex items-center justify-between rounded bg-white/50 px-2 py-1.5">
                          <span className="text-xs text-pine-700">
                            {(s.name as string) || "未命名"}
                          </span>
                          <span className="text-[10px] text-pine-700">
                            {guessSuggestionType(s) === "rules" ? "世界规则"
                              : guessSuggestionType(s) === "characters" ? "人物"
                              : guessSuggestionType(s) === "locations" ? "地点"
                              : guessSuggestionType(s) === "foreshadowing" ? "伏笔"
                              : "设定"}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
        {sending && (
          <div className="flex justify-start">
            <div className="flex items-center gap-2 rounded-xl bg-white/50 px-3.5 py-2.5 text-sm text-pine-700">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {ASSISTANT_ROLE_LABELS[activeTab] || "世界观构建师"}正在思考...
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
        </>
        )}
      </div>

      <div className="border-t border-pine-200/40 px-4 py-3">
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`描述你想要的${TAB_LABELS[activeTab] || "世界观"}...`}
            disabled={sending}
            rows={1}
            className="flex-1 rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none transition-colors focus:border-magic-500/50 disabled:opacity-50 resize-none max-h-24"
            style={{ minHeight: "36px" }}
          />
          <button
            onClick={handleSend}
            disabled={sending || !input.trim()}
            className="flex items-center justify-center rounded-lg bg-white/50 px-3 py-2 text-pine-700 transition-colors hover:bg-white/50 hover:text-pine-700 disabled:opacity-50"
          >
            {sending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>
    </aside>
    </div>
    </ChatErrorBoundary>
  );
}

function guessSuggestionType(item: Record<string, unknown>): string {
  if ("title" in item && item.title && !item.name) {
    return "rules";
  }
  if ("category" in item || "constraints" in item || "priority" in item) {
    return "rules";
  }
  if ("appearance" in item || "personality" in item || "desire" in item || "arc" in item || "deep_need" in item) {
    return "characters";
  }
  if ("geography" in item || "atmosphere" in item || "parent_location" in item) {
    return "locations";
  }
  if ("bury_window_start" in item || "reveal_window_start" in item || "related_characters" in item) {
    return "foreshadowing";
  }
  return "unknown";
}

function OverviewSection({
  overview,
  onNavigate,
}: {
  overview: WorldbuildingOverview;
  onNavigate: (tab: TabKey) => void;
}) {
  const stats: Array<{
    label: string;
    value: number;
    secondary?: string;
    icon: typeof Shield;
    color: string;
    bg: string;
    tab: TabKey;
  }> = [
    {
      label: "世界规则",
      value: overview.total_rules,
      icon: Shield,
      color: "text-magic-400",
      bg: "bg-magic-500/10",
      tab: "rules" as TabKey,
    },
    {
      label: "人物",
      value: overview.total_characters,
      icon: Users,
      color: "text-sky-400",
      bg: "bg-sky-500/10",
      tab: "characters" as TabKey,
    },
    {
      label: "地点",
      value: overview.total_locations,
      icon: MapPin,
      color: "text-emerald-400",
      bg: "bg-emerald-500/10",
      tab: "locations" as TabKey,
    },
    {
      label: "伏笔",
      value: overview.total_foreshadowing,
      icon: Eye,
      color: "text-violet-400",
      bg: "bg-violet-500/10",
      tab: "foreshadowing" as TabKey,
    },
    {
      label: "晋升与发现",
      value: overview.total_promotions + (overview.observations?.total || 0),
      secondary: `晋升 ${overview.total_promotions} · 待确认发现 ${overview.observations?.pending_review || 0}`,
      icon: ArrowUpFromLine,
      color: "text-rose-400",
      bg: "bg-rose-500/10",
      tab: "promotions" as TabKey,
    },
  ];

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <button
              key={stat.label}
              onClick={() => onNavigate(stat.tab)}
              className="group rounded-xl border border-pine-200/60 bg-white/50 p-5 text-left transition-colors hover:border-pine-200/60 hover:bg-white/50"
            >
              <div className={clsx("mb-3 flex h-10 w-10 items-center justify-center rounded-lg", stat.bg)}>
                <Icon className={clsx("h-5 w-5", stat.color)} />
              </div>
              <div className="text-2xl font-bold text-pine-700">{stat.value}</div>
              <div className="text-xs text-pine-700">{stat.label}</div>
              {stat.secondary && <div className="mt-1 text-[10px] text-pine-700">{stat.secondary}</div>}
            </button>
          );
        })}
      </div>

      {Object.keys(overview.rule_categories).length > 0 && (
        <div className="rounded-xl border border-pine-200/60 bg-white/50 p-5">
          <h3 className="mb-4 text-sm font-medium text-pine-700">规则分类分布</h3>
          <div className="space-y-2">
            {Object.entries(overview.rule_categories).map(([cat, count]) => {
              const Icon = CATEGORY_ICONS[cat] || Shield;
              const label = CATEGORY_LABELS[cat] || cat;
              const pct = overview.total_rules > 0 ? (count / overview.total_rules) * 100 : 0;
              return (
                <div key={cat} className="flex items-center gap-3">
                  <Icon className="h-4 w-4 shrink-0 text-pine-700" />
                  <span className="w-24 shrink-0 text-xs text-pine-700">{label}</span>
                  <div className="flex-1">
                    <div className="h-2 overflow-hidden rounded-full bg-white/50">
                      <div
                        className="h-full rounded-full bg-magic-500/60 transition-all"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                  <span className="w-8 text-right text-xs text-pine-700">{count}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {overview.seed_stats && (
        <div className="rounded-xl border border-pine-200/60 bg-white/50 p-5">
          <h3 className="mb-4 text-sm font-medium text-pine-700">细节种子分层</h3>
          <div className="grid grid-cols-3 gap-4">
            <div className="rounded-lg border border-crimson-500/20 bg-crimson-500/5 p-3 text-center">
              <div className="flex items-center justify-center gap-1.5 text-crimson-400">
                <Database className="h-3.5 w-3.5" />
                <span className="text-xs font-medium">T1 关键</span>
              </div>
              <div className="mt-1 text-lg font-bold text-pine-700">{overview.seed_stats.T1 || 0}</div>
            </div>
            <div className="rounded-lg border border-magic-500/20 bg-magic-500/5 p-3 text-center">
              <div className="flex items-center justify-center gap-1.5 text-magic-400">
                <Database className="h-3.5 w-3.5" />
                <span className="text-xs font-medium">T2 重要</span>
              </div>
              <div className="mt-1 text-lg font-bold text-pine-700">{overview.seed_stats.T2 || 0}</div>
            </div>
            <div className="rounded-lg border border-pine-200/40 bg-white/50 p-3 text-center">
              <div className="flex items-center justify-center gap-1.5 text-pine-700">
                <Database className="h-3.5 w-3.5" />
                <span className="text-xs font-medium">T3 补充</span>
              </div>
              <div className="mt-1 text-lg font-bold text-pine-700">{overview.seed_stats.T3 || 0}</div>
            </div>
          </div>
        </div>
      )}

      {overview.total_rules === 0 && overview.total_characters === 0 && overview.total_locations === 0 && overview.total_foreshadowing === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-pine-700">
          <Globe className="mb-4 h-16 w-16" />
          <p className="text-lg font-medium">世界尚在混沌之中</p>
          <p className="mt-1 text-sm">开始定义你的世界规则、人物和地点，构建故事世界的基石</p>
          <button
            onClick={() => onNavigate("rules")}
            className="mt-4 rounded-lg bg-magic-500 px-4 py-2 text-sm font-medium text-pine-700 transition-colors hover:bg-magic-400"
          >
            创建第一条规则
          </button>
        </div>
      )}
    </div>
  );
}

function RulesSection({
  projectId,
  rules,
  rulesByCategory,
  expandedCategories,
  onToggleCategory,
  onRefresh,
  fillData,
}: {
  projectId: string;
  rules: WorldRule[];
  rulesByCategory: Record<string, WorldRule[]>;
  expandedCategories: Record<string, boolean>;
  onToggleCategory: (cat: string) => void;
  onRefresh: () => void;
  fillData?: Record<string, unknown> | null;
}) {
  const [showForm, setShowForm] = useState(false);
  const [editingRule, setEditingRule] = useState<WorldRule | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (fillData && Object.keys(fillData).length > 0) {
      setEditingRule(null);
      setShowForm(true);
    }
  }, [fillData]);

  const handleSave = async (data: Partial<WorldRule>) => {
    setSaving(true);
    try {
      if (editingRule) {
        await api.updateWorldRule(projectId, editingRule.id, data);
      } else {
        await api.createWorldRule(projectId, data);
      }
      onRefresh();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deletingId) return;
    try {
      await api.deleteWorldRule(projectId, deletingId);
      setDeletingId(null);
      onRefresh();
    } catch {}
  };

  const handleToggleLock = async (rule: WorldRule) => {
    await api.updateWorldRule(projectId, rule.id, { locked: !rule.locked });
    onRefresh();
  };

  const criticalCount = rules.filter((r) => r.priority === "critical").length;

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-pine-700">世界规则</h2>
          <span className="text-xs text-pine-700">{rules.length} 条规则</span>
          {criticalCount > 0 && (
            <span className="flex items-center gap-1 rounded-full bg-crimson-500/15 px-2 py-0.5 text-xs text-crimson-400">
              <AlertTriangle className="h-3 w-3" />
              {criticalCount} 条宪法级
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => {
            setEditingRule(null);
            setShowForm(true);
          }}
          aria-haspopup="dialog"
          aria-expanded={showForm}
          className="inline-flex min-h-11 items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-md)] border border-[var(--color-accent)] bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-[var(--color-on-accent)] transition-[background-color,border-color,transform] duration-150 ease-[var(--ease-ui)] hover:border-[var(--color-accent-hover)] hover:bg-[var(--color-accent-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-ink-strong)] active:translate-y-px active:border-[var(--color-accent-hover)] active:bg-[var(--color-accent-hover)] motion-reduce:active:transform-none disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Plus className="h-4 w-4" strokeWidth={2.25} aria-hidden="true" />
          新增规则
        </button>
      </div>

      {Object.entries(rulesByCategory).length === 0 && (
        <div className="flex flex-col items-center py-12 text-pine-700">
          <Shield className="mb-3 h-12 w-12" />
          <p className="text-sm">尚未定义任何世界规则</p>
          <p className="text-xs">世界规则是故事世界的绝对真理，不可被生成内容违背</p>
        </div>
      )}

      {Object.entries(rulesByCategory).map(([cat, catRules]) => {
        const Icon = CATEGORY_ICONS[cat] || Shield;
        const label = CATEGORY_LABELS[cat] || cat;
        const isExpanded = expandedCategories[cat] !== false;

        return (
          <div key={cat} className="rounded-xl border border-pine-200/60 bg-white/50">
            <button
              onClick={() => onToggleCategory(cat)}
              className="flex w-full items-center gap-3 p-4 text-left"
            >
              {isExpanded ? (
                <ChevronDown className="h-4 w-4 text-pine-700" />
              ) : (
                <ChevronRight className="h-4 w-4 text-pine-700" />
              )}
              <Icon className="h-4 w-4 text-magic-400/70" />
              <span className="text-sm font-medium text-pine-700">{label}</span>
              <span className="rounded-full bg-white/50 px-2 py-0.5 text-xs text-pine-700">
                {catRules.length}
              </span>
            </button>

            {isExpanded && (
              <div className="border-t border-pine-200/40 px-4 pb-3">
                {catRules.map((rule) => {
                  const pCfg = PRIORITY_CONFIG[rule.priority] || PRIORITY_CONFIG.normal;
                  return (
                    <div
                      key={rule.id}
                      className="group flex items-start gap-3 border-b border-pine-200/20 py-3 last:border-0"
                    >
                      <div className="mt-1">
                        <button
                          onClick={() => handleToggleLock(rule)}
                          className={clsx(
                            "transition-colors",
                            rule.locked ? "text-magic-400" : "text-pine-700 hover:text-pine-700"
                          )}
                          title={rule.locked ? "已锁定（不可被AI修改）" : "未锁定"}
                        >
                          {rule.locked ? <Lock className="h-3.5 w-3.5" /> : <Unlock className="h-3.5 w-3.5" />}
                        </button>
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-pine-700">{rule.name}</span>
                          <span className={clsx("rounded-full border px-1.5 py-0.5 text-[10px]", pCfg.color)}>
                            {pCfg.label}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-pine-700 line-clamp-2">{rule.description}</p>
                        {rule.constraints.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {rule.constraints.map((c, i) => (
                              <span
                                key={i}
                                className="rounded bg-white/50 px-1.5 py-0.5 text-[10px] text-pine-700"
                              >
                                {c}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                        <button
                          onClick={() => {
                            setEditingRule(rule);
                            setShowForm(true);
                          }}
                          className="rounded p-1 text-pine-700 transition-colors hover:bg-white/50 hover:text-pine-700"
                        >
                          <Edit3 className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => setDeletingId(rule.id)}
                          className="rounded p-1 text-pine-700 transition-colors hover:bg-crimson-500/10 hover:text-crimson-400"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}

      {showForm && (
        <WorldRuleFormModal
          rule={editingRule}
          saving={saving}
          categoryLabels={CATEGORY_LABELS}
          onSave={handleSave}
          onClose={() => {
            setShowForm(false);
            setEditingRule(null);
          }}
          fillData={fillData}
        />
      )}

      {deletingId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-80 rounded-xl border border-pine-200/60 bg-white/50 p-5">
            <h3 className="text-sm font-semibold text-pine-700">确认删除</h3>
            <p className="mt-2 text-xs text-pine-700">删除后该规则将从世界观中移除，AI 生成将不再受此规则约束。</p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setDeletingId(null)}
                className="rounded-lg px-3 py-1.5 text-sm text-pine-700 hover:text-pine-700"
              >
                取消
              </button>
              <button
                onClick={handleDelete}
                className="rounded-lg bg-crimson-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-crimson-400"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function PromotionDiscoverySection({
  projectId,
  promotions,
  observations,
  observationCounts,
  observationTotal,
  observationPage,
  observationPageSize,
  observationTotalPages,
  observationHasMore,
  initialLoading,
  refreshing,
  loadError,
  onRefresh,
  onRefreshObservations,
  onRetry,
}: {
  projectId: string;
  promotions: PromotionProposal[];
  observations: WorldviewObservation[];
  observationCounts: WorldviewObservationReviewCounts;
  observationTotal: number;
  observationPage: number;
  observationPageSize: number;
  observationTotalPages: number;
  observationHasMore: boolean;
  initialLoading: boolean;
  refreshing: boolean;
  loadError: Error | null;
  onRefresh: () => void;
  onRefreshObservations: (options: WorldviewObservationListParams) => Promise<WorldviewObservationList | null>;
  onRetry: () => void;
}) {
  if (initialLoading && loadError) {
    return (
      <div className="mx-auto flex min-h-64 max-w-xl flex-col items-center justify-center text-center" role="alert">
        <AlertTriangle className="h-7 w-7 text-[var(--color-error)]" />
        <h2 className="mt-3 text-sm font-semibold text-[var(--color-ink-strong)]">设定变更数据载入失败</h2>
        <p className="mt-2 text-xs text-[var(--color-ink-muted)]">概览和其他世界观内容不受影响，可以单独重试这一部分。</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 min-h-11 whitespace-nowrap rounded-md border border-[var(--border-emphasis)] bg-[var(--surface-raised)] px-4 text-sm font-medium text-[var(--color-ink)] transition-colors hover:bg-[var(--color-accent-soft)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--focus-ring)] active:translate-y-px motion-reduce:transform-none"
        >
          重试载入
        </button>
      </div>
    );
  }

  if (initialLoading) {
    return (
      <div className="mx-auto max-w-4xl space-y-4" role="status" aria-live="polite">
        <span className="sr-only">正在载入晋升提案和正文发现</span>
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-paper)] p-5" aria-hidden="true">
          <div className="h-5 w-36 animate-pulse rounded bg-[var(--border-subtle)] motion-reduce:animate-none" />
          <div className="mt-3 h-3 max-w-xl animate-pulse rounded bg-[var(--surface-tool)] motion-reduce:animate-none" />
        </div>
        {Array.from({ length: 3 }, (_, index) => (
          <div
            key={index}
            className="flex min-h-16 items-center gap-4 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-4"
            aria-hidden="true"
          >
            <div className="h-7 w-16 animate-pulse rounded-full bg-[var(--surface-tool)] motion-reduce:animate-none" />
            <div className="min-w-0 flex-1 space-y-2">
              <div className="h-3 w-2/5 animate-pulse rounded bg-[var(--border-subtle)] motion-reduce:animate-none" />
              <div className="h-3 w-4/5 animate-pulse rounded bg-[var(--surface-tool)] motion-reduce:animate-none" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  const pendingPromotions = promotions.filter((item) => item.status === "pending").length;
  const pendingDiscoveries = observationCounts.pending;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="rounded-2xl border border-pine-200/70 bg-white/60 px-5 py-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <ArrowUpFromLine className="h-4 w-4 text-magic-400" />
              <h2 className="text-base font-semibold text-pine-700">设定晋升工作台</h2>
            </div>
            <p className="mt-1 text-xs leading-5 text-pine-700">
              正文发现记录“正文里发生了什么”，晋升提案决定“哪些内容进入正式世界观”。两者在同一处核对，避免重复确认或状态漂移。
            </p>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-pine-700">
            {refreshing && (
              <span className="flex items-center gap-1.5 text-[var(--color-ink-muted)]" role="status">
                <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />
                同步中
              </span>
            )}
            <span className="rounded-full border border-magic-500/30 bg-magic-500/10 px-2.5 py-1">{pendingDiscoveries} 条待确认发现</span>
            <span className="rounded-full border border-rose-500/30 bg-rose-500/10 px-2.5 py-1">{pendingPromotions} 条待处理提案</span>
          </div>
        </div>
      </div>

      <ObservationsSection
        projectId={projectId}
        observations={observations}
        counts={observationCounts}
        total={observationTotal}
        page={observationPage}
        pageSize={observationPageSize}
        totalPages={observationTotalPages}
        hasMore={observationHasMore}
        refreshing={refreshing}
        onRefresh={onRefreshObservations}
      />
      <PromotionSection
        projectId={projectId}
        promotions={promotions}
        onRefresh={onRefresh}
      />
    </div>
  );
}

const OBSERVATION_STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  active: { label: "待审核", color: "text-magic-400 bg-magic-500/15 border-magic-500/30" },
  retracted: { label: "已撤回", color: "text-pine-700 bg-white/50 border-pine-200/30" },
  merged: { label: "已合并", color: "text-sky-400 bg-sky-500/15 border-sky-500/30" },
  rejected: { label: "已拒绝", color: "text-crimson-400 bg-crimson-500/15 border-crimson-500/30" },
  promoted: { label: "已确认", color: "text-emerald-400 bg-emerald-500/15 border-emerald-500/30" },
};

const ENTITY_TYPE_LABELS: Record<string, string> = {
  character: "人物",
  location: "地点",
  world_rule: "世界规则",
  foreshadowing: "伏笔",
  fact: "事实",
};

const ENTITY_TYPE_COLORS: Record<string, string> = {
  character: "text-sky-400 bg-sky-500/15",
  location: "text-emerald-400 bg-emerald-500/15",
  world_rule: "text-magic-400 bg-magic-500/15",
  foreshadowing: "text-violet-400 bg-violet-500/15",
  fact: "text-pine-700 bg-white/50",
};

function ObservationsSection({
  projectId,
  observations,
  counts,
  total,
  page,
  pageSize,
  totalPages,
  hasMore,
  refreshing,
  onRefresh,
}: {
  projectId: string;
  observations: WorldviewObservation[];
  counts: WorldviewObservationReviewCounts;
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
  hasMore: boolean;
  refreshing: boolean;
  onRefresh: (options: WorldviewObservationListParams) => Promise<WorldviewObservationList | null>;
}) {
  const [promotingId, setPromotingId] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [filter, setFilter] = useState<"pending" | "auto" | "manual" | "all">("pending");
  const [actionError, setActionError] = useState<string | null>(null);

  const queryFor = useCallback((reviewState = filter, targetPage = page): WorldviewObservationListParams => ({
    reviewState,
    page: targetPage,
    pageSize,
  }), [filter, page, pageSize]);

  const refreshAfterAction = useCallback(async (removeFromCurrentPage: boolean) => {
    const targetPage = removeFromCurrentPage && observations.length <= 1 && page > 1
      ? page - 1
      : page;
    return onRefresh(queryFor(filter, targetPage));
  }, [filter, observations.length, onRefresh, page, queryFor]);

  const handlePromote = async (observationId: string) => {
    setPromotingId(observationId);
    setActionError(null);
    try {
      await api.promoteObservation(projectId, observationId);
      const refreshed = await refreshAfterAction(filter === "pending");
      if (!refreshed) setActionError("发现已确认，但列表刷新失败，请重试刷新");
    } catch (error) {
      setActionError((error as Error).message || "确认发现失败，请重试");
    } finally {
      setPromotingId(null);
    }
  };

  const handleReject = async (observationId: string) => {
    setRejectingId(observationId);
    setActionError(null);
    try {
      await api.rejectObservation(projectId, observationId);
      const refreshed = await refreshAfterAction(filter === "pending");
      if (!refreshed) setActionError("发现已拒绝，但列表刷新失败，请重试刷新");
    } catch (error) {
      setActionError((error as Error).message || "拒绝发现失败，请重试");
    } finally {
      setRejectingId(null);
    }
  };

  const handleRetrySync = async () => {
    setSyncing(true);
    setActionError(null);
    try {
      await api.retryWorldviewSync(projectId);
      const refreshed = await onRefresh(queryFor(filter, 1));
      if (!refreshed) setActionError("扫描已提交，但列表刷新失败，请稍后重试");
    } catch (error) {
      setActionError((error as Error).message || "重新扫描失败，请重试");
    } finally {
      setSyncing(false);
    }
  };

  const groupedByType = observations.reduce<Record<string, WorldviewObservation[]>>((acc, obs) => {
    const t = obs.entity_type;
    if (!acc[t]) acc[t] = [];
    acc[t].push(obs);
    return acc;
  }, {});

  const filterOptions = [
    { key: "pending" as const, label: "待确认", count: counts.pending },
    { key: "auto" as const, label: "自动确认", count: counts.auto },
    { key: "manual" as const, label: "手动确认", count: counts.manual },
    { key: "all" as const, label: "全部", count: counts.all },
  ];

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-semibold text-[var(--color-ink-strong)]">正文发现</h2>
            <span className="text-xs text-[var(--color-ink-muted)]">当前显示 {observations.length} / {total} 条</span>
          </div>
          <p className="mt-1 text-xs leading-5 text-[var(--color-ink-muted)]">
            待确认是需要你处理的队列；自动确认和手动确认可分别回看，不再混在同一列表。
          </p>
        </div>
        <button
          type="button"
          onClick={handleRetrySync}
          disabled={syncing}
          className="flex min-h-9 items-center gap-1.5 whitespace-nowrap rounded-md border border-[var(--border-emphasis)] bg-[var(--surface-raised)] px-3 text-sm text-[var(--color-ink)] transition-colors hover:bg-[var(--color-accent-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-[var(--surface-tool)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw className={clsx("h-3.5 w-3.5", syncing && "animate-spin motion-reduce:animate-none")} />
          {syncing ? "同步中" : "重新扫描"}
        </button>
      </div>

      <div className="flex max-w-full gap-1 overflow-x-auto rounded-md border border-[var(--border-subtle)] bg-[var(--surface-tool)] p-1" role="group" aria-label="正文发现筛选">
        {filterOptions.map((option) => {
          const active = filter === option.key;
          return (
            <button
              key={option.key}
              type="button"
              aria-pressed={active}
              onClick={() => {
                setFilter(option.key);
                setActionError(null);
                void onRefresh(queryFor(option.key, 1)).then((result) => {
                  if (!result) setActionError("筛选结果加载失败，请重试");
                });
              }}
              disabled={refreshing && active}
              className={clsx(
                "inline-flex min-h-9 shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-[var(--color-accent-soft)]",
                active
                  ? "bg-[var(--surface-raised)] text-[var(--color-ink-strong)] shadow-[var(--shadow-raised)]"
                  : "text-[var(--color-ink-muted)] hover:bg-[var(--surface-raised)] hover:text-[var(--color-ink)]",
              )}
            >
              {option.label}
              <span className={clsx(
                "min-w-5 rounded-full px-1.5 py-0.5 text-center tabular-nums",
                active ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]" : "bg-[var(--surface-paper)] text-[var(--color-ink-muted)]",
              )}>
                {option.count}
              </span>
            </button>
          );
        })}
      </div>

      {actionError && (
        <div className="rounded-md border border-[var(--color-error)] bg-[var(--color-error-soft)] px-3 py-2 text-xs leading-5 text-[var(--color-error)]" role="alert">
          {actionError}
        </div>
      )}

      {!refreshing && counts.all === 0 && (
        <div className="flex flex-col items-center py-12 text-pine-700">
          <BookOpen className="mb-3 h-12 w-12" />
          <p className="text-sm">暂无正文发现</p>
          <p className="text-xs">生成章节后，系统将自动从正文中提取世界观观察</p>
        </div>
      )}

      {!refreshing && counts.all > 0 && total === 0 && (
        <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-4 py-10 text-center">
          <p className="text-sm font-medium text-[var(--color-ink)]">
            {filter === "pending" ? "当前没有待确认发现" : filter === "auto" ? "当前没有自动确认记录" : "当前没有手动确认记录"}
          </p>
          <p className="mt-1 text-xs text-[var(--color-ink-muted)]">可以切换到“全部”查看其他状态。</p>
        </div>
      )}

      {Object.entries(groupedByType).map(([entityType, items]) => {
        const typeLabel = ENTITY_TYPE_LABELS[entityType] || entityType;
        const typeColor = ENTITY_TYPE_COLORS[entityType] || "text-pine-700 bg-white/50";
        return (
          <div key={entityType} className="rounded-xl border border-pine-200/60 bg-white/50">
            <div className="flex items-center gap-3 p-4">
              <span className={clsx("rounded-full px-2.5 py-1 text-xs font-medium", typeColor)}>
                {typeLabel}
              </span>
              <span className="text-xs text-pine-700">{items.length} 条</span>
            </div>
            <div className="border-t border-pine-200/40 px-4 pb-3">
              {items.map((obs) => {
                const sCfg = OBSERVATION_STATUS_CONFIG[obs.status] || OBSERVATION_STATUS_CONFIG.active;
                const isActive = obs.status === "active";
                return (
                  <div
                    key={obs.id}
                    className="group flex items-start gap-3 border-b border-pine-200/20 py-3 last:border-0"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-pine-700">{obs.entity_name}</span>
                        <span className={clsx("rounded-full border px-1.5 py-0.5 text-[10px]", sCfg.color)}>
                          {sCfg.label}
                        </span>
                        {obs.auto_promoted && (
                          <span className="rounded-full border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 text-[10px] text-sky-400">
                            自动确认
                          </span>
                        )}
                        {obs.orphan_warning && (
                          <span className="flex items-center gap-1 text-[10px] text-magic-400">
                            <AlertTriangle className="h-3 w-3" />
                            孤立
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-pine-700 line-clamp-2">
                        {obs.evidence_text.length > 120
                          ? obs.evidence_text.slice(0, 120) + "..."
                          : obs.evidence_text}
                      </p>
                      <div className="mt-2 flex items-center gap-3 text-[10px] text-pine-700">
                        <span>第{obs.chapter_number}章</span>
                        <span>置信度 {Math.round(obs.confidence * 100)}%</span>
                        <span>{obs.operation === "new" ? "新增" : obs.operation === "supplement" ? "补充" : obs.operation === "mention" ? "提及" : obs.operation === "reveal" ? "揭示" : obs.operation === "conflict" ? "冲突" : obs.operation}</span>
                      </div>
                    </div>
                    {isActive && (
                      <div className="flex shrink-0 items-center gap-1">
                        <button
                          onClick={() => handlePromote(obs.id)}
                          disabled={promotingId === obs.id}
                          className="flex items-center gap-1 whitespace-nowrap rounded-md bg-emerald-500/15 px-2 py-1 text-xs text-emerald-400 transition-colors hover:bg-emerald-500/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-emerald-500/25 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {promotingId === obs.id ? (
                            <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />
                          ) : (
                            <Check className="h-3 w-3" />
                          )}
                          确认
                        </button>
                        <button
                          onClick={() => handleReject(obs.id)}
                          disabled={rejectingId === obs.id}
                          className="flex items-center gap-1 whitespace-nowrap rounded-md bg-crimson-500/15 px-2 py-1 text-xs text-crimson-400 transition-colors hover:bg-crimson-500/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-crimson-500/25 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {rejectingId === obs.id ? (
                            <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />
                          ) : (
                            <X className="h-3 w-3" />
                          )}
                          拒绝
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      {total > 0 && totalPages > 1 && (
        <div className="flex items-center justify-end gap-2" aria-label="正文发现分页">
          <button
            type="button"
            onClick={() => {
              setActionError(null);
              void onRefresh(queryFor(filter, Math.max(1, page - 1))).then((result) => {
                if (!result) setActionError("上一页加载失败，请重试");
              });
            }}
            disabled={refreshing || page <= 1}
            className="min-h-9 whitespace-nowrap rounded-md border border-[var(--border-emphasis)] bg-[var(--surface-raised)] px-3 text-xs text-[var(--color-ink)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            上一页
          </button>
          <span className="text-xs tabular-nums text-[var(--color-ink-muted)]">第 {page} / {totalPages} 页</span>
          <button
            type="button"
            onClick={() => {
              setActionError(null);
              void onRefresh(queryFor(filter, page + 1)).then((result) => {
                if (!result) setActionError("下一页加载失败，请重试");
              });
            }}
            disabled={refreshing || !hasMore}
            className="min-h-9 whitespace-nowrap rounded-md border border-[var(--border-emphasis)] bg-[var(--surface-raised)] px-3 text-xs text-[var(--color-ink)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            下一页
          </button>
        </div>
      )}

    </div>
  );
}

function CharactersSection({
  projectId,
  characters,
  onRefresh,
  fillData,
}: {
  projectId: string;
  characters: Character[];
  onRefresh: () => void;
  fillData?: Record<string, unknown> | null;
}) {
  const [showForm, setShowForm] = useState(false);
  const [editingChar, setEditingChar] = useState<Character | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (fillData && Object.keys(fillData).length > 0) {
      setEditingChar(null);
      setShowForm(true);
    }
  }, [fillData]);

  const handleSave = async (data: Partial<Character>) => {
    setSaving(true);
    try {
      if (editingChar) {
        await api.updateCharacter(projectId, editingChar.id, data);
      } else {
        await api.createCharacter(projectId, data);
      }
      setShowForm(false);
      setEditingChar(null);
      onRefresh();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deletingId) return;
    try {
      await api.deleteCharacter(projectId, deletingId);
      setDeletingId(null);
      onRefresh();
    } catch {}
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-pine-700">人物核心</h2>
          <span className="text-xs text-pine-700">{characters.length} 个人物</span>
        </div>
        <button
          onClick={() => {
            setEditingChar(null);
            setShowForm(true);
          }}
          className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-3 py-1.5 text-sm font-medium text-pine-700 transition-colors hover:bg-magic-400"
        >
          <Plus className="h-4 w-4" />
          新增人物
        </button>
      </div>

      {characters.length === 0 && (
        <div className="flex flex-col items-center py-12 text-pine-700">
          <Users className="mb-3 h-12 w-12" />
          <p className="text-sm">尚未创建任何人物</p>
          <p className="text-xs">人物核心定义了角色的外貌、性格、欲望与人物弧光</p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        {characters.map((char) => (
          <div
            key={char.id}
            className="group rounded-xl border border-pine-200/60 bg-white/50 p-4 transition-colors hover:border-pine-200/60"
          >
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-sky-500/15">
                  <span className="text-sm font-medium text-sky-400">
                    {char.name.charAt(0)}
                  </span>
                </div>
                <div>
                  <h3 className="text-sm font-medium text-pine-700">{char.name}</h3>
                  {char.aliases.length > 0 && (
                    <p className="text-[10px] text-pine-700">
                      别名：{char.aliases.join("、")}
                    </p>
                  )}
                  <div className="mt-1 flex flex-wrap gap-1">
                    <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-[10px] text-sky-600">
                      {char.role_importance || "supporting"}
                    </span>
                    <span className="rounded bg-white/60 px-1.5 py-0.5 text-[10px] text-pine-700">
                      {char.lifecycle_status || "unknown"} · {char.narrative_activity || "dormant"}
                    </span>
                  </div>
                </div>
              </div>
              <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                <button
                  onClick={() => {
                    setEditingChar(char);
                    setShowForm(true);
                  }}
                  className="rounded p-1 text-pine-700 hover:bg-white/50 hover:text-pine-700"
                >
                  <Edit3 className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => setDeletingId(char.id)}
                  className="rounded p-1 text-pine-700 hover:bg-crimson-500/10 hover:text-crimson-400"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>

            <div className="mt-3 space-y-2">
              {char.description && (
                <div>
                  <span className="text-[10px] text-pine-700">人物摘要</span>
                  <p className="text-xs text-pine-700 line-clamp-2">{char.description}</p>
                </div>
              )}
              {char.appearance && (
                <div>
                  <span className="text-[10px] text-pine-700">外貌</span>
                  <p className="text-xs text-pine-700 line-clamp-2">{char.appearance}</p>
                </div>
              )}
              {char.personality && (
                <div>
                  <span className="text-[10px] text-pine-700">性格</span>
                  <p className="text-xs text-pine-700 line-clamp-2">{char.personality}</p>
                </div>
              )}
              {char.desire && (
                <div>
                  <span className="text-[10px] text-pine-700">欲望</span>
                  <p className="text-xs text-pine-700 line-clamp-1">{char.desire}</p>
                </div>
              )}
              {char.arc && (
                <div>
                  <span className="text-[10px] text-pine-700">人物弧光</span>
                  <p className="text-xs text-pine-700 line-clamp-1">{char.arc}</p>
                </div>
              )}
            </div>

            {Object.keys(char.relationships).length > 0 && (
              <div className="mt-3 border-t border-pine-200/30 pt-2">
                <span className="text-[10px] text-pine-700">关系</span>
                <div className="mt-1 flex flex-wrap gap-1">
                  {Object.entries(char.relationships).map(([name, rel]) => (
                    <span
                      key={name}
                      className="rounded bg-white/50 px-1.5 py-0.5 text-[10px] text-pine-700"
                    >
                      {name}：{rel}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {showForm && (
        <CharacterFormModal
          character={editingChar}
          saving={saving}
          onSave={handleSave}
          onClose={() => {
            setShowForm(false);
            setEditingChar(null);
          }}
          fillData={fillData}
        />
      )}

      {deletingId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-80 rounded-xl border border-pine-200/60 bg-white/50 p-5">
            <h3 className="text-sm font-semibold text-pine-700">确认删除</h3>
            <p className="mt-2 text-xs text-pine-700">删除后该人物将从世界观中移除，相关引用可能失效。</p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setDeletingId(null)}
                className="rounded-lg px-3 py-1.5 text-sm text-pine-700 hover:text-pine-700"
              >
                取消
              </button>
              <button
                onClick={handleDelete}
                className="rounded-lg bg-crimson-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-crimson-400"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function CharacterFormModal({
  character,
  saving,
  onSave,
  onClose,
  fillData,
}: {
  character: Character | null;
  saving: boolean;
  onSave: (data: Partial<Character>) => void;
  onClose: () => void;
  fillData?: Record<string, unknown> | null;
}) {
  const [name, setName] = useState(character?.name || "");
  const [aliases, setAliases] = useState(character?.aliases?.join("、") || "");
  const [description, setDescription] = useState(character?.description || "");
  const [appearance, setAppearance] = useState(character?.appearance || "");
  const [personality, setPersonality] = useState(character?.personality || "");
  const [desire, setDesire] = useState(character?.desire || "");
  const [deepNeed, setDeepNeed] = useState(character?.deep_need || "");
  const [arc, setArc] = useState(character?.arc || "");
  const [role, setRole] = useState(character?.role || "");
  const [faction, setFaction] = useState(character?.faction || "");
  const [roleImportance, setRoleImportance] = useState<Character["role_importance"]>(character?.role_importance || "supporting");
  const [lifecycleStatus, setLifecycleStatus] = useState<Character["lifecycle_status"]>(character?.lifecycle_status || "unknown");
  const [narrativeActivity, setNarrativeActivity] = useState<Character["narrative_activity"]>(character?.narrative_activity || "dormant");
  const [firstSeenChapter, setFirstSeenChapter] = useState(character?.first_seen_chapter?.toString() || "");
  const [lastSeenChapter, setLastSeenChapter] = useState(character?.last_seen_chapter?.toString() || "");
  const [lastMentionedChapter, setLastMentionedChapter] = useState(character?.last_mentioned_chapter?.toString() || "");
  const [nextPlannedChapter, setNextPlannedChapter] = useState(character?.next_planned_chapter?.toString() || "");
  const [activeArcIds, setActiveArcIds] = useState(character?.active_arc_ids?.join("、") || "");
  const [relationshipsText, setRelationshipsText] = useState(
    character?.relationships
      ? Object.entries(character.relationships)
          .map(([k, v]) => `${k}:${v}`)
          .join("\n")
      : ""
  );

  useEffect(() => {
    if (fillData && Object.keys(fillData).length > 0) {
      setName((fillData.name as string) || "");
      setDescription((fillData.description as string) || "");
      setAppearance((fillData.appearance as string) || "");
      setPersonality((fillData.personality as string) || "");
      setDesire((fillData.desire as string) || "");
      setDeepNeed((fillData.deep_need as string) || "");
      setArc((fillData.arc as string) || "");
      setRole((fillData.role as string) || "");
      setFaction((fillData.faction as string) || "");
      setRoleImportance((fillData.role_importance as Character["role_importance"]) || "supporting");
      setLifecycleStatus((fillData.lifecycle_status as Character["lifecycle_status"]) || "unknown");
      setNarrativeActivity((fillData.narrative_activity as Character["narrative_activity"]) || "dormant");
    }
  }, [fillData]);

  const handleSubmit = () => {
    if (!name.trim()) return;
    const relationships: Record<string, string> = {};
    relationshipsText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean)
      .forEach((line) => {
        const idx = line.indexOf(":");
        if (idx > 0) {
          relationships[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
        }
      });

    onSave({
      name: name.trim(),
      aliases: aliases
        .split(/[、,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
      description: description.trim(),
      appearance: appearance.trim(),
      personality: personality.trim(),
      desire: desire.trim(),
      deep_need: deepNeed.trim(),
      arc: arc.trim(),
      role: role.trim(),
      faction: faction.trim(),
      status: lifecycleStatus,
      lifecycle_status: lifecycleStatus,
      narrative_activity: narrativeActivity,
      role_importance: roleImportance,
      first_seen_chapter: firstSeenChapter ? parseInt(firstSeenChapter, 10) : null,
      last_seen_chapter: lastSeenChapter ? parseInt(lastSeenChapter, 10) : null,
      last_mentioned_chapter: lastMentionedChapter ? parseInt(lastMentionedChapter, 10) : null,
      next_planned_chapter: nextPlannedChapter ? parseInt(nextPlannedChapter, 10) : null,
      active_arc_ids: activeArcIds.split(/[、,，]/).map((s) => s.trim()).filter(Boolean),
      relationships,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="max-h-[85vh] w-[560px] overflow-auto rounded-xl border border-pine-200/60 bg-white/50 p-6">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-pine-700">
            {character ? "编辑人物" : "新增人物"}
          </h3>
          <button onClick={onClose} className="text-pine-700 hover:text-pine-700">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4">
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs text-pine-700">姓名</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="角色名称"
                className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
              />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-xs text-pine-700">别名（顿号分隔）</label>
              <input
                type="text"
                value={aliases}
                onChange={(e) => setAliases(e.target.value)}
                placeholder="外号、曾用名"
                className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">人物摘要</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="身份、经历、当前处境的简洁摘要；不要把整段资料塞进外貌字段"
              rows={3}
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="mb-1 block text-xs text-pine-700">重要性</label>
              <select value={roleImportance} onChange={(e) => setRoleImportance(e.target.value as Character["role_importance"])} className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700">
                <option value="protagonist">主角</option><option value="main">主要角色</option><option value="supporting">配角</option><option value="episodic">单章角色</option><option value="background">背景角色</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-pine-700">生命状态</label>
              <select value={lifecycleStatus} onChange={(e) => setLifecycleStatus(e.target.value as Character["lifecycle_status"])} className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700">
                <option value="unknown">未知</option><option value="alive">存活</option><option value="deceased">死亡</option><option value="missing">失踪</option><option value="transformed">已转化</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-pine-700">叙事活跃度</label>
              <select value={narrativeActivity} onChange={(e) => setNarrativeActivity(e.target.value as Character["narrative_activity"])} className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700">
                <option value="core_active">核心活跃</option><option value="scene_active">本场活跃</option><option value="dormant">休眠</option><option value="archived">归档</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div><label className="mb-1 block text-xs text-pine-700">角色定位</label><input value={role} onChange={(e) => setRole(e.target.value)} placeholder="反派、导师、调查者……" className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700" /></div>
            <div><label className="mb-1 block text-xs text-pine-700">所属阵营</label><input value={faction} onChange={(e) => setFaction(e.target.value)} className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700" /></div>
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">外貌</label>
            <textarea
              value={appearance}
              onChange={(e) => setAppearance(e.target.value)}
              placeholder="角色的外貌特征描述..."
              rows={2}
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">性格</label>
            <textarea
              value={personality}
              onChange={(e) => setPersonality(e.target.value)}
              placeholder="角色的性格特点..."
              rows={2}
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>

          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs text-pine-700">表层欲望</label>
              <input
                type="text"
                value={desire}
                onChange={(e) => setDesire(e.target.value)}
                placeholder="角色想要什么"
                className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
              />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-xs text-pine-700">深层需求</label>
              <input
                type="text"
                value={deepNeed}
                onChange={(e) => setDeepNeed(e.target.value)}
                placeholder="角色真正需要什么"
                className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">人物弧光</label>
            <textarea
              value={arc}
              onChange={(e) => setArc(e.target.value)}
              placeholder="角色在故事中的成长与转变轨迹..."
              rows={2}
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">人物关系（每行一条，格式：姓名:关系）</label>
            <textarea
              value={relationshipsText}
              onChange={(e) => setRelationshipsText(e.target.value)}
              placeholder={"林鹤:宿敌\n苏晚:挚爱"}
              rows={3}
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 font-mono text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>

          <div className="grid grid-cols-4 gap-2">
            {[
              ["初次出现章", firstSeenChapter, setFirstSeenChapter],
              ["最近出场章", lastSeenChapter, setLastSeenChapter],
              ["最近提及章", lastMentionedChapter, setLastMentionedChapter],
              ["下次计划章", nextPlannedChapter, setNextPlannedChapter],
            ].map(([label, value, setter]) => (
              <div key={label as string}><label className="mb-1 block text-xs text-pine-700">{label as string}</label><input type="number" min={1} value={value as string} onChange={(e) => (setter as (v: string) => void)(e.target.value)} className="w-full rounded-lg border border-pine-200 bg-white/50 px-2 py-2 text-sm text-pine-700" /></div>
            ))}
          </div>

          <div><label className="mb-1 block text-xs text-pine-700">活跃人物弧 ID（顿号分隔）</label><input value={activeArcIds} onChange={(e) => setActiveArcIds(e.target.value)} className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700" /></div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-pine-700 hover:text-pine-700"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !name.trim()}
            className="rounded-lg bg-magic-500 px-4 py-2 text-sm font-medium text-pine-700 transition-colors hover:bg-magic-400 disabled:opacity-50"
          >
            {saving ? "保存中..." : character ? "更新" : "创建"}
          </button>
        </div>
      </div>
    </div>
  );
}

function EnhancedLocationsSection({
  projectId,
  locations,
  onRefresh,
  fillData,
}: {
  projectId: string;
  locations: Location[];
  onRefresh: () => void;
  fillData?: Record<string, unknown> | null;
}) {
  const [showForm, setShowForm] = useState(false);
  const [editingLoc, setEditingLoc] = useState<Location | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (fillData && Object.keys(fillData).length > 0) {
      setEditingLoc(null);
      setShowForm(true);
    }
  }, [fillData]);

  const handleSave = async (data: Partial<Location>) => {
    setSaving(true);
    try {
      if (editingLoc) {
        await api.updateLocation(projectId, editingLoc.id, data);
      } else {
        await api.createLocation(projectId, data);
      }
      setShowForm(false);
      setEditingLoc(null);
      onRefresh();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deletingId) return;
    try {
      await api.deleteLocation(projectId, deletingId);
      setDeletingId(null);
      onRefresh();
    } catch {}
  };

  const locationGroups = useMemo(() => {
    const byName = new Map(locations.map((loc) => [loc.name.trim(), loc]));
    const childrenByParent = new Map<string, Location[]>();
    const roots: Location[] = [];
    const virtualParents = new Set<string>();

    locations.forEach((loc) => {
      const parentName = loc.parent_location?.trim();
      if (parentName) {
        const children = childrenByParent.get(parentName) ?? [];
        children.push(loc);
        childrenByParent.set(parentName, children);
        if (!byName.has(parentName)) virtualParents.add(parentName);
      } else {
        roots.push(loc);
      }
    });

    return [
      ...roots.map((loc) => ({
        id: loc.id,
        name: loc.name,
        parent: loc,
        virtual: false,
        children: childrenByParent.get(loc.name) ?? [],
      })),
      ...Array.from(virtualParents).map((name) => ({
        id: `virtual-${name}`,
        name,
        parent: null,
        virtual: true,
        children: childrenByParent.get(name) ?? [],
      })),
    ].sort((a, b) => Number(b.children.length > 0) - Number(a.children.length > 0) || a.name.localeCompare(b.name));
  }, [locations]);

  const renderLocationActions = (loc: Location, child = false) => (
    <div className={`flex gap-1 opacity-0 transition-opacity ${child ? "group-hover/child:opacity-100" : "group-hover:opacity-100"}`}>
      <button
        onClick={() => {
          setEditingLoc(loc);
          setShowForm(true);
        }}
        className="rounded p-1 text-pine-700 hover:bg-white/50 hover:text-pine-700"
        title="编辑"
      >
        <Edit3 className="h-3.5 w-3.5" />
      </button>
      <button
        onClick={() => setDeletingId(loc.id)}
        className="rounded p-1 text-pine-700 hover:bg-crimson-500/10 hover:text-crimson-400"
        title="删除"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-pine-700">地点网络</h2>
          <span className="text-xs text-pine-700">{locations.length} 个地点</span>
        </div>
        <button
          onClick={() => {
            setEditingLoc(null);
            setShowForm(true);
          }}
          className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-3 py-1.5 text-sm font-medium text-pine-700 transition-colors hover:bg-magic-400"
        >
          <Plus className="h-4 w-4" />
          新增地点
        </button>
      </div>

      {locations.length === 0 && (
        <div className="flex flex-col items-center py-12 text-pine-700">
          <MapPin className="mb-3 h-12 w-12" />
          <p className="text-sm">尚未定义任何地点</p>
          <p className="text-xs">章节提交后会自动补充新地点与隶属关系</p>
        </div>
      )}

      <div className="space-y-3">
        {locationGroups.map((group) => {
          const loc = group.parent;
          return (
            <div
              key={group.id}
              className="group rounded-xl border border-pine-200/60 bg-white/50 p-4 transition-colors hover:border-pine-200/60"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-500/15">
                      <MapPin className="h-4 w-4 text-emerald-400" />
                    </div>
                    <h3 className="text-sm font-medium text-pine-700">{group.name}</h3>
                    {loc?.location_type && (
                      <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-300">
                        {loc.location_type}
                      </span>
                    )}
                    {group.virtual && (
                      <span className="rounded bg-white/50 px-1.5 py-0.5 text-[10px] text-pine-700">虚拟父级</span>
                    )}
                    {group.children.length > 0 && (
                      <span className="rounded bg-white/50 px-1.5 py-0.5 text-[10px] text-pine-700">
                        {group.children.length} 个子地点
                      </span>
                    )}
                  </div>
                  {loc?.description && <p className="mt-2 text-xs text-pine-700 line-clamp-2">{loc.description}</p>}
                  {loc?.function && <p className="mt-2 text-xs text-pine-700">功能：{loc.function}</p>}
                  {loc?.atmosphere && <p className="mt-2 text-xs text-pine-700">氛围：{loc.atmosphere}</p>}
                </div>
                {loc && renderLocationActions(loc)}
              </div>

              {group.children.length > 0 && (
                <div className="mt-4 space-y-2 border-l border-pine-200/80 pl-3">
                  {group.children.map((child) => (
                    <div key={child.id} className="group/child rounded-lg bg-white/50 px-3 py-2">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-xs font-medium text-pine-700">{child.name}</span>
                            {child.location_type && (
                              <span className="rounded bg-white/50 px-1.5 py-0.5 text-[10px] text-pine-700">
                                {child.location_type}
                              </span>
                            )}
                          </div>
                          {child.description && <p className="mt-1 text-xs text-pine-700 line-clamp-2">{child.description}</p>}
                          {child.function && <p className="mt-1 text-[11px] text-pine-700">功能：{child.function}</p>}
                          {child.atmosphere && <p className="mt-1 text-[11px] text-pine-700">氛围：{child.atmosphere}</p>}
                        </div>
                        {renderLocationActions(child, true)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {showForm && (
        <LocationFormModal
          location={editingLoc}
          saving={saving}
          onSave={handleSave}
          onClose={() => {
            setShowForm(false);
            setEditingLoc(null);
          }}
          fillData={fillData}
        />
      )}

      {deletingId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-80 rounded-xl border border-pine-200/60 bg-white/50 p-5">
            <h3 className="text-sm font-semibold text-pine-700">确认删除</h3>
            <p className="mt-2 text-xs text-pine-700">删除后该地点会从世界观中移除，相关引用可能失效。</p>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setDeletingId(null)} className="rounded-lg px-3 py-1.5 text-sm text-pine-700 hover:text-pine-700">
                取消
              </button>
              <button onClick={handleDelete} className="rounded-lg bg-crimson-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-crimson-400">
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function LocationFormModal({
  location,
  saving,
  onSave,
  onClose,
  fillData,
}: {
  location: Location | null;
  saving: boolean;
  onSave: (data: Partial<Location>) => void;
  onClose: () => void;
  fillData?: Record<string, unknown> | null;
}) {
  const [name, setName] = useState(location?.name || "");
  const [description, setDescription] = useState(location?.description || "");
  const [atmosphere, setAtmosphere] = useState(location?.atmosphere || "");
  const [parentLocation, setParentLocation] = useState(location?.parent_location || "");

  useEffect(() => {
    if (fillData && Object.keys(fillData).length > 0) {
      setName((fillData.name as string) || "");
      setDescription((fillData.description as string) || "");
      setAtmosphere((fillData.atmosphere as string) || "");
      setParentLocation((fillData.parent_location as string) || "");
    }
  }, [fillData]);

  const handleSubmit = () => {
    if (!name.trim()) return;
    onSave({
      name: name.trim(),
      description: description.trim(),
      atmosphere: atmosphere.trim(),
      parent_location: parentLocation.trim() || undefined,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[520px] rounded-xl border border-pine-200/60 bg-white/50 p-6">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-pine-700">
            {location ? "编辑地点" : "新增地点"}
          </h3>
          <button onClick={onClose} className="text-pine-700 hover:text-pine-700">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs text-pine-700">地点名称</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例：天机阁"
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">地点描述</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="描述这个地点的核心特征..."
              rows={3}
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">氛围</label>
            <input
              type="text"
              value={atmosphere}
              onChange={(e) => setAtmosphere(e.target.value)}
              placeholder="例：肃穆、神秘、繁华"
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">上级地点</label>
            <input
              type="text"
              value={parentLocation}
              onChange={(e) => setParentLocation(e.target.value)}
              placeholder="如果此地点属于某个更大的区域..."
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-pine-700 hover:text-pine-700"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !name.trim()}
            className="rounded-lg bg-magic-500 px-4 py-2 text-sm font-medium text-pine-700 transition-colors hover:bg-magic-400 disabled:opacity-50"
          >
            {saving ? "保存中..." : location ? "更新" : "创建"}
          </button>
        </div>
      </div>
    </div>
  );
}

const FORESHADOWING_STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  planned: { label: "规划中", color: "text-pine-700 bg-white/50 border-pine-200/80" },
  active: { label: "激活中", color: "text-sky-400 bg-sky-500/10 border-sky-500/30" },
  dormant: { label: "休眠中", color: "text-emerald-400 bg-emerald-500/10 border-emerald-500/30" },
  revealing: { label: "揭示中", color: "text-magic-400 bg-magic-500/10 border-magic-500/30" },
  resolved: { label: "已回收", color: "text-violet-300 bg-violet-500/10 border-violet-500/30" },
  revised: { label: "已修订", color: "text-cyan-300 bg-cyan-500/10 border-cyan-500/30" },
  aborted: { label: "已废弃", color: "text-crimson-300 bg-crimson-500/10 border-crimson-500/30" },
};

function ForeshadowingSection({
  projectId,
  onRefresh,
  fillData,
}: {
  projectId: string;
  onRefresh: () => void;
  fillData?: Record<string, unknown> | null;
}) {
  const [showForm, setShowForm] = useState(false);
  const [editingItem, setEditingItem] = useState<ForeshadowingLine | null>(null);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [lines, setLines] = useState<ForeshadowingLine[]>([]);
  const [dashboard, setDashboard] = useState<ForeshadowingDashboard | null>(null);
  const [selectedLineId, setSelectedLineId] = useState<string | null>(null);
  const [selectedReadiness, setSelectedReadiness] = useState<RevealReadiness | null>(null);
  const [selectedEvidencePool, setSelectedEvidencePool] = useState<Record<string, unknown> | null>(null);
  const [selectedCognitiveMap, setSelectedCognitiveMap] = useState<Record<string, unknown> | null>(null);
  const [loadingForeshadowing, setLoadingForeshadowing] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    if (fillData && Object.keys(fillData).length > 0) {
      setEditingItem(null);
      setShowForm(true);
    }
  }, [fillData]);

  const refreshAdvanced = useCallback(async () => {
    setLoadingForeshadowing(true);
    try {
      const [lineResult, dashResult] = await Promise.all([
        api.listForeshadowingLines(projectId),
        api.getForeshadowingDashboard(projectId),
      ]);
      setLines(lineResult.lines || []);
      setDashboard(dashResult);
      setSelectedLineId((prev) => prev || lineResult.lines?.[0]?.id || null);
    } finally {
      setLoadingForeshadowing(false);
    }
  }, [projectId]);

  useEffect(() => {
    refreshAdvanced();
  }, [refreshAdvanced, refreshTick]);

  useEffect(() => {
    const loadLineDetails = async () => {
      if (!selectedLineId) {
        setSelectedReadiness(null);
        setSelectedEvidencePool(null);
        setSelectedCognitiveMap(null);
        return;
      }
      try {
        const [readiness, evidencePool, cognitiveMap] = await Promise.all([
          api.getRevealReadiness(projectId, selectedLineId),
          api.getEvidencePool(projectId, selectedLineId),
          api.getCognitiveMap(projectId, { line_id: selectedLineId }),
        ]);
        setSelectedReadiness(readiness);
        setSelectedEvidencePool(evidencePool);
        setSelectedCognitiveMap(cognitiveMap);
      } catch {
        setSelectedReadiness(null);
        setSelectedEvidencePool(null);
        setSelectedCognitiveMap(null);
      }
    };
    loadLineDetails();
  }, [projectId, selectedLineId, refreshTick]);

  const triggerRefresh = () => {
    onRefresh();
    setRefreshTick((tick) => tick + 1);
  };

  const handleSave = async (data: Partial<ForeshadowingLine>) => {
    setSaving(true);
    try {
      if (editingItem) {
        await api.updateForeshadowing(projectId, editingItem.id, data);
      } else {
        await api.createForeshadowingLine(projectId, {
          name: data.name || "未命名伏笔",
          status: data.status || "active",
          priority: data.priority || "moderate",
          secret: {
            canonical_statement: data.secret_canonical_statement || data.name || "未命名伏笔",
            truth_type: data.secret_truth_type || "past_event",
            impact_level: data.secret_impact_level || "moderate",
            spoiler_scope: data.secret_spoiler_scope || {},
          },
          timeline: {
            bury_window_start: data.bury_window_start ?? null,
            bury_window_end: data.bury_window_end ?? null,
            reveal_window_start: data.reveal_window_start ?? null,
            reveal_window_end: data.reveal_window_end ?? null,
            total_clues_planned: data.total_clues_planned || 3,
          },
          narrative_structure: {
            bury_rhythm: data.bury_rhythm || "gradual",
            reader_fairness_level: data.reader_fairness_level || "balanced",
            salience: data.salience || "medium",
          },
        });
      }
      setShowForm(false);
      setEditingItem(null);
      triggerRefresh();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deletingId) return;
    try {
      await api.deleteForeshadowing(projectId, deletingId);
      setDeletingId(null);
      triggerRefresh();
    } catch {}
  };

  const handleTransition = async (lineId: string, newStatus: ForeshadowingLine["status"]) => {
    await api.transitionForeshadowingStatus(projectId, lineId, newStatus);
    triggerRefresh();
  };

  const statusCounts = lines.reduce<Record<string, number>>((acc, line) => {
    acc[line.status] = (acc[line.status] || 0) + 1;
    return acc;
  }, {});

  const groupedLines = lines.reduce<Record<string, ForeshadowingLine[]>>((acc, line) => {
    if (!acc[line.status]) acc[line.status] = [];
    acc[line.status].push(line);
    return acc;
  }, {});

  const selectedLine = lines.find((line) => line.id === selectedLineId) || null;
  const evidenceCounts = {
    supportive: Number(selectedEvidencePool?.supportive || 0),
    distractive: Number(selectedEvidencePool?.distractive || 0),
    contradictory: Number(selectedEvidencePool?.contradictory || 0),
    missing: Number(selectedEvidencePool?.missing || 0),
  };
  const evidenceClues = Array.isArray(selectedEvidencePool?.clues)
    ? selectedEvidencePool.clues.filter(
        (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object",
      )
    : [];
  const cognitiveStates = Array.isArray(selectedCognitiveMap?.states)
    ? selectedCognitiveMap.states.filter(
        (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object",
      )
    : [];
  const lineNameById = new Map(lines.map((line) => [String(line.id), line.name]));
  const cognitiveLevelLabels: Record<string, string> = {
    fully_blind: "完全不知情",
    blind: "不知情",
    suspecting: "有所怀疑",
    partial_knowledge: "掌握部分信息",
    full_knowledge: "完全知情",
    knows_truth: "知晓真相",
    misled: "受到误导",
    unknown: "尚未记录",
  };
  const statusFlow: ForeshadowingLine["status"][] = ["planned", "active", "dormant", "revealing", "resolved"];
  const transitionTargets: Record<ForeshadowingLine["status"], ForeshadowingLine["status"][]> = {
    planned: ["active", "aborted"],
    active: ["dormant", "revised", "aborted"],
    dormant: ["revealing", "revised", "aborted"],
    revealing: ["resolved", "revised", "aborted"],
    resolved: ["revised"],
    revised: ["active", "dormant", "revealing", "aborted"],
    aborted: [],
  };

  const formatChapterScene = (chapter: number | null, scene: number | null) => {
    if (chapter == null) return "未设置";
    return scene != null ? `第${chapter}章·场景${scene}` : `第${chapter}章`;
  };

  const renderMetric = (label: string, value: string | number, hint?: string) => (
    <div className="rounded-xl border border-pine-200/60 bg-white/50 p-4">
      <div className="text-[11px] text-pine-700">{label}</div>
      <div className="mt-1 text-lg font-semibold text-pine-700">{value}</div>
      {hint && <div className="mt-1 text-[11px] text-pine-700">{hint}</div>}
    </div>
  );

  const renderLineCard = (line: ForeshadowingLine) => {
    const cfg = FORESHADOWING_STATUS_CONFIG[line.status] || FORESHADOWING_STATUS_CONFIG.planned;
    const isSelected = selectedLineId === line.id;
    return (
      <button
        key={line.id}
        onClick={() => setSelectedLineId(line.id)}
        className={clsx(
          "w-full rounded-xl border p-4 text-left transition-colors",
          isSelected
            ? "border-magic-500/40 bg-magic-500/10"
            : "border-pine-200/60 bg-white/50 hover:border-pine-200/70"
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-pine-700">{line.name}</span>
              <span className={clsx("rounded-full border px-2 py-0.5 text-[10px]", cfg.color)}>{cfg.label}</span>
            </div>
            <p className="mt-1 line-clamp-2 text-xs text-pine-700">{line.secret_canonical_statement || line.name}</p>
            <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-pine-700">
              <span>埋设 {formatChapterScene(line.bury_window_start, null)}</span>
              <span>揭示 {formatChapterScene(line.reveal_window_start, null)}</span>
              <span>线索 {line.clues_placed}/{line.total_clues_planned}</span>
              <span>准备度 {Math.round((line.reveal_readiness_score || 0) * 100)}%</span>
            </div>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <span className="text-[10px] text-pine-700">v{line.version}</span>
            <span className="text-[10px] text-pine-700">{line.priority}</span>
          </div>
        </div>
      </button>
    );
  };

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-pine-700">伏笔管理</h2>
          <p className="mt-1 text-xs text-pine-700">世界观二级菜单中的伏笔控制台。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => {
              setEditingItem(null);
              setShowForm(true);
            }}
            className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-3 py-2 text-sm font-medium text-pine-700 transition-colors hover:bg-magic-400"
          >
            <Plus className="h-4 w-4" />
            新增伏笔
          </button>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {renderMetric("伏笔总数", lines.length, "规划中 / 活跃 / 休眠 / 揭示中 / 已回收")}
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.3fr_0.7fr]">
        <div className="space-y-4">
          <div className="rounded-xl border border-pine-200/60 bg-white/50 p-4">
            <div className="flex flex-wrap gap-2">
              {statusFlow.map((status) => (
                <span key={status} className={clsx("rounded-full border px-2.5 py-1 text-xs", FORESHADOWING_STATUS_CONFIG[status].color)}>
                  {FORESHADOWING_STATUS_CONFIG[status].label}: {statusCounts[status] || 0}
                </span>
              ))}
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            {statusFlow.map((status) => (
              <div key={status} className="rounded-xl border border-pine-200/60 bg-white/50 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="text-sm font-medium text-pine-700">{FORESHADOWING_STATUS_CONFIG[status].label}</h3>
                  <span className="text-xs text-pine-700">{groupedLines[status]?.length || 0}</span>
                </div>
                <div className="space-y-3">
                  {(groupedLines[status] || []).slice(0, 3).map(renderLineCard)}
                  {!(groupedLines[status] || []).length && (
                    <div className="rounded-lg border border-dashed border-pine-200/80 px-3 py-6 text-center text-xs text-pine-700">
                      暂无条目
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>

        </div>

        <div className="space-y-4">
          <div className="rounded-xl border border-pine-200/60 bg-white/50 p-4">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-medium text-pine-700">选中伏笔</h3>
              {selectedLine && (
                <span className={clsx("rounded-full border px-2 py-0.5 text-[10px]", FORESHADOWING_STATUS_CONFIG[selectedLine.status].color)}>
                  {FORESHADOWING_STATUS_CONFIG[selectedLine.status].label}
                </span>
              )}
            </div>
            <div className="space-y-3">
              {selectedLine ? (
                <>
                  <div>
                    <div className="text-base font-semibold text-pine-700">{selectedLine.name}</div>
                    <p className="mt-1 text-xs text-pine-700">{selectedLine.secret_canonical_statement}</p>
                  </div>
                  {selectedReadiness && (
                    <div className="grid grid-cols-2 gap-2 text-xs text-pine-700">
                      <div>准备度: {Math.round(selectedReadiness.readiness * 100)}%</div>
                      <div>可揭示: {selectedReadiness.ready ? "是" : "否"}</div>
                      <div>证据覆盖: {Math.round(selectedReadiness.components.evidence_coverage * 100)}%</div>
                      <div>读者公平: {Math.round(selectedReadiness.components.reader_fairness * 100)}%</div>
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-2 text-xs text-pine-700">
                    <div>线索: {selectedLine.clues_placed}/{selectedLine.total_clues_planned}</div>
                    <div>复杂度: {selectedLine.complexity_score.toFixed(2)}</div>
                    <div>埋设窗口: {selectedLine.bury_window_start ?? "未设"} - {selectedLine.bury_window_end ?? "未设"}</div>
                    <div>揭示窗口: {selectedLine.reveal_window_start ?? "未设"} - {selectedLine.reveal_window_end ?? "未设"}</div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {transitionTargets[selectedLine.status]
                      .map((status) => (
                        <button
                          key={status}
                          onClick={() => handleTransition(selectedLine.id, status)}
                          className="rounded-lg border border-pine-200/80 px-2.5 py-1 text-xs text-pine-700 hover:border-magic-500/40 hover:text-magic-300"
                        >
                          迁移到 {FORESHADOWING_STATUS_CONFIG[status].label}
                        </button>
                      ))}
                  </div>
                </>
              ) : (
                <div className="rounded-lg border border-dashed border-pine-200/80 px-3 py-8 text-center text-xs text-pine-700">
                  请选择一条伏笔查看准备度、证据池与认知图。
                </div>
              )}
            </div>
          </div>

          <div className="rounded-xl border border-pine-200/60 bg-white/50 p-4">
            <h3 className="text-sm font-medium text-pine-700">线索与人物认知</h3>
            <p className="mt-1 text-xs leading-5 text-pine-700">
              查看这条伏笔已经留下的证据，以及各人物目前知道多少。
            </p>

            <div className="mt-4 grid grid-cols-2 border-y border-pine-200/60 text-xs sm:grid-cols-4">
              {([
                ["支持线索", evidenceCounts.supportive],
                ["干扰线索", evidenceCounts.distractive],
                ["矛盾线索", evidenceCounts.contradictory],
                ["待补线索", evidenceCounts.missing],
              ] as Array<[string, number]>).map(([label, value], index) => (
                <div
                  key={label}
                  className={`px-3 py-3 ${index > 0 ? "sm:border-l sm:border-pine-200/60" : ""} ${index % 2 === 1 ? "border-l border-pine-200/60" : ""}`}
                >
                  <div className="text-pine-700">{label}</div>
                  <div className="mt-1 font-mono text-base font-semibold text-pine-700">{value}</div>
                </div>
              ))}
            </div>

            <div className="mt-4">
              <div className="text-xs font-medium text-pine-700">已记录的线索</div>
              {evidenceClues.length > 0 ? (
                <ul className="mt-2 divide-y divide-pine-200/60 border-y border-pine-200/60">
                  {evidenceClues.slice(0, 8).map((clue, index) => (
                    <li key={String(clue.id || index)} className="flex items-start justify-between gap-3 py-2.5 text-xs text-pine-700">
                      <span className="min-w-0 leading-5">{String(clue.clue_text || clue.description || "未填写线索内容")}</span>
                      <span className="shrink-0 whitespace-nowrap text-pine-700">
                        {clue.chapter_number ? `第${String(clue.chapter_number)}章` : "章节未定"}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 border-y border-pine-200/60 py-3 text-xs text-pine-700">尚未记录具体线索。</p>
              )}
            </div>

            <div className="mt-4">
              <div className="text-xs font-medium text-pine-700">人物认知</div>
              {cognitiveStates.length > 0 ? (
                <ul className="mt-2 divide-y divide-pine-200/60 border-y border-pine-200/60">
                  {cognitiveStates.map((state, index) => {
                    const level = String(state.cognitive_level || state.cognitive_status || "unknown");
                    return (
                      <li key={String(state.id || state.character_name || index)} className="flex items-center justify-between gap-3 py-2.5 text-xs text-pine-700">
                        <span className="min-w-0 truncate font-medium">{String(state.character_name || "未命名人物")}</span>
                        <span className="shrink-0 text-right">
                          {cognitiveLevelLabels[level] || "已有认知记录"}
                          {state.is_intentionally_misled ? " · 受到误导" : ""}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="mt-2 border-y border-pine-200/60 py-3 text-xs text-pine-700">尚未记录人物认知。</p>
              )}
            </div>

            {dashboard?.conflicts?.length ? (
              <div className="mt-4">
                <div className="text-xs font-medium text-crimson-500">需要留意</div>
                <ul className="mt-2 divide-y divide-pine-200/60 border-y border-pine-200/60">
                  {dashboard.conflicts.slice(0, 5).map((conflict, index) => {
                    const type = String(conflict.type || "");
                    const sourceName = lineNameById.get(String(conflict.source_id || ""));
                    const targetName = lineNameById.get(String(conflict.target_id || ""));
                    const cycleNames = Array.isArray(conflict.cycle)
                      ? conflict.cycle.map((id) => lineNameById.get(String(id)) || "未命名伏笔")
                      : [];
                    const title = type === "dependency_cycle"
                      ? `发现循环依赖：${cycleNames.join(" → ")}`
                      : sourceName && targetName
                        ? `「${sourceName}」与「${targetName}」存在冲突`
                        : "两条伏笔之间存在冲突";
                    return (
                      <li key={index} className="py-2.5 text-xs leading-5 text-pine-700">
                        <p className="font-medium">{title}</p>
                        {conflict.narrative_justification ? (
                          <p className="mt-0.5 text-pine-700">{String(conflict.narrative_justification)}</p>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {loadingForeshadowing && <div className="text-xs text-pine-700">正在同步新伏笔数据...</div>}

      {showForm && (
        <ForeshadowingFormModal
          item={editingItem}
          saving={saving}
          onSave={handleSave}
          onClose={() => {
            setShowForm(false);
            setEditingItem(null);
          }}
          fillData={fillData}
        />
      )}

      {deletingId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-80 rounded-xl border border-pine-200/60 bg-white/50 p-5">
            <h3 className="text-sm font-semibold text-pine-700">确认删除</h3>
            <p className="mt-2 text-xs text-pine-700">删除后该伏笔会从世界观中移除，生成时将不再参考。</p>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setDeletingId(null)} className="rounded-lg px-3 py-1.5 text-sm text-pine-700 hover:text-pine-700">取消</button>
              <button onClick={handleDelete} className="rounded-lg bg-crimson-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-crimson-400">删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}function ForeshadowingFormModal({
  item,
  saving,
  onSave,
  onClose,
  fillData,
}: {
  item: ForeshadowingLine | null;
  saving: boolean;
  onSave: (data: Partial<ForeshadowingLine>) => void;
  onClose: () => void;
  fillData?: Record<string, unknown> | null;
}) {
  const [name, setName] = useState(item?.name || "");
  const [secretStatement, setSecretStatement] = useState(item?.secret_canonical_statement || "");
  const [buryStart, setBuryStart] = useState(item?.bury_window_start?.toString() || "");
  const [buryEnd, setBuryEnd] = useState(item?.bury_window_end?.toString() || "");
  const [revealStart, setRevealStart] = useState(item?.reveal_window_start?.toString() || "");
  const [revealEnd, setRevealEnd] = useState(item?.reveal_window_end?.toString() || "");
  const [status, setStatus] = useState<ForeshadowingLine["status"]>(item?.status || "active");
  const [priority, setPriority] = useState<ForeshadowingLine["priority"]>(item?.priority || "moderate");

  useEffect(() => {
    if (fillData && Object.keys(fillData).length > 0) {
      setName((fillData.name as string) || "");
      setSecretStatement((fillData.secret_canonical_statement as string) || "");
      setBuryStart(fillData.bury_window_start != null ? String(fillData.bury_window_start) : "");
      setRevealStart(fillData.reveal_window_start != null ? String(fillData.reveal_window_start) : "");
    }
  }, [fillData]);

  const handleSubmit = () => {
    if (!name.trim()) return;
    onSave({
      name: name.trim(),
      secret_canonical_statement: secretStatement.trim(),
      bury_window_start: buryStart ? parseInt(buryStart, 10) : null,
      bury_window_end: buryEnd ? parseInt(buryEnd, 10) : null,
      reveal_window_start: revealStart ? parseInt(revealStart, 10) : null,
      reveal_window_end: revealEnd ? parseInt(revealEnd, 10) : null,
      status,
      priority,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="max-h-[85vh] w-[560px] overflow-auto rounded-xl border border-pine-200/60 bg-white/50 p-6">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-pine-700">
            {item ? "编辑伏笔" : "新增伏笔"}
          </h3>
          <button onClick={onClose} className="text-pine-700 hover:text-pine-700">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs text-pine-700">伏笔名称</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例：神秘的玉佩"
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">核心秘密陈述</label>
            <textarea
              value={secretStatement}
              onChange={(e) => setSecretStatement(e.target.value)}
              placeholder="描述这条伏笔的核心秘密内容..."
              rows={3}
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs text-pine-700">埋设起始章节</label>
              <input
                type="number"
                value={buryStart}
                onChange={(e) => setBuryStart(e.target.value)}
                placeholder="章节数"
                className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-pine-700">埋设截止章节</label>
              <input
                type="number"
                value={buryEnd}
                onChange={(e) => setBuryEnd(e.target.value)}
                placeholder="章节数"
                className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-pine-700">揭示起始章节</label>
              <input
                type="number"
                value={revealStart}
                onChange={(e) => setRevealStart(e.target.value)}
                placeholder="章节数"
                className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-pine-700">揭示截止章节</label>
              <input
                type="number"
                value={revealEnd}
                onChange={(e) => setRevealEnd(e.target.value)}
                placeholder="章节数"
                className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">状态</label>
            <div className="flex flex-wrap gap-1">
              {(["planned", "active", "dormant", "revealing", "resolved", "revised", "aborted"] as const).map((s) => {
                const cfg = FORESHADOWING_STATUS_CONFIG[s];
                if (!cfg) return null;
                return (
                  <button
                    key={s}
                    onClick={() => setStatus(s)}
                    className={clsx(
                      "rounded-lg border px-2 py-1 text-xs transition-colors",
                      status === s ? cfg.color : "border-pine-200 text-pine-700 hover:text-pine-700"
                    )}
                  >
                    {cfg.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">优先级</label>
            <div className="flex gap-1">
              {(["minor", "moderate", "major", "structural"] as const).map((p) => (
                <button
                  key={p}
                  onClick={() => setPriority(p)}
                  className={clsx(
                    "rounded-lg border px-2 py-1 text-xs transition-colors",
                    priority === p ? "border-magic-500/30 bg-magic-500/15 text-magic-400" : "border-pine-200 text-pine-700 hover:text-pine-700"
                  )}
                >
                  {p === "minor" ? "次要" : p === "moderate" ? "中等" : p === "major" ? "重要" : "结构性"}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-pine-700 hover:text-pine-700"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !name.trim()}
            className="rounded-lg bg-magic-500 px-4 py-2 text-sm font-medium text-pine-700 transition-colors hover:bg-magic-400 disabled:opacity-50"
          >
            {saving ? "保存中..." : item ? "更新" : "创建"}
          </button>
        </div>
      </div>
    </div>
  );
}

const PROMOTION_STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  pending: { label: "待审核", color: "text-magic-400 bg-magic-500/15 border-magic-500/30" },
  approved: { label: "已批准", color: "text-emerald-400 bg-emerald-500/15 border-emerald-500/30" },
  rejected: { label: "已拒绝", color: "text-pine-700 bg-white/50 border-pine-200/30" },
  conflict: { label: "有冲突", color: "text-crimson-400 bg-crimson-500/15 border-crimson-500/30" },
};

const TARGET_TYPE_LABELS: Record<string, string> = {
  character: "人物",
  world_rule: "世界规则",
  location: "地点",
};

function PromotionSection({
  projectId,
  promotions,
  onRefresh,
}: {
  projectId: string;
  promotions: PromotionProposal[];
  onRefresh: () => void;
}) {
  const [candidates, setCandidates] = useState<Array<{ seed_id: string; content: string; reference_count: number }>>([]);
  const [showForm, setShowForm] = useState(false);
  const [formCandidate, setFormCandidate] = useState<{ seed_id: string; content: string; reference_count: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const loadCandidates = useCallback(async () => {
    try {
      const data = await api.getPromotionCandidates(projectId);
      setCandidates(data);
    } catch {}
  }, [projectId]);

  useEffect(() => {
    loadCandidates();
  }, [loadCandidates]);

  const handleCreateFromCandidate = (candidate: { seed_id: string; content: string; reference_count: number }) => {
    setFormCandidate(candidate);
    setShowForm(true);
  };

  const handleSave = async (data: Partial<PromotionProposal>) => {
    setSaving(true);
    try {
      await api.createPromotionProposal(projectId, data);
      setShowForm(false);
      setFormCandidate(null);
      onRefresh();
    } finally {
      setSaving(false);
    }
  };

  const handleApprove = async (proposalId: string) => {
    await api.approvePromotion(projectId, proposalId);
    onRefresh();
  };

  const handleReject = async () => {
    if (!rejectingId) return;
    await api.rejectPromotion(projectId, rejectingId, rejectReason);
    setRejectingId(null);
    setRejectReason("");
    onRefresh();
  };

  const handleDelete = async () => {
    if (!deletingId) return;
    try {
      await api.deletePromotionProposal(projectId, deletingId);
      setDeletingId(null);
      onRefresh();
    } catch {}
  };

  const pending = promotions.filter((p) => p.status === "pending");
  const approved = promotions.filter((p) => p.status === "approved");
  const rejected = promotions.filter((p) => p.status === "rejected");
  const conflict = promotions.filter((p) => p.status === "conflict");

  const renderGroup = (label: string, items: PromotionProposal[]) => {
    if (items.length === 0) return null;
    return (
      <div>
        <h3 className="mb-3 text-xs font-medium text-pine-700">{label}（{items.length}）</h3>
        <div className="space-y-3">
          {items.map((proposal) => {
            const sCfg = PROMOTION_STATUS_CONFIG[proposal.status] || PROMOTION_STATUS_CONFIG.pending;
            return (
              <div
                key={proposal.id}
                className="group rounded-xl border border-pine-200/60 bg-white/50 p-4 transition-colors hover:border-pine-200/60"
              >
                <div className="flex items-start justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-pine-700">{proposal.seed_content}</span>
                      <span className={clsx("rounded-full border px-1.5 py-0.5 text-[10px]", sCfg.color)}>
                        {sCfg.label}
                      </span>
                    </div>
                    <div className="mt-2 flex items-center gap-3 text-[10px] text-pine-700">
                      <span>引用次数：{proposal.reference_count}</span>
                      <span>目标类型：{TARGET_TYPE_LABELS[proposal.target_type] || proposal.target_type}</span>
                    </div>
                    {proposal.conflict_info && (
                      <p className="mt-1 text-xs text-crimson-400">{proposal.conflict_info}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                    {proposal.status === "pending" && (
                      <>
                        <button
                          onClick={() => handleApprove(proposal.id)}
                          className="rounded p-1 text-pine-700 transition-colors hover:bg-emerald-500/10 hover:text-emerald-400"
                          title="批准晋升"
                        >
                          <Check className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => {
                            setRejectingId(proposal.id);
                            setRejectReason("");
                          }}
                          className="rounded p-1 text-pine-700 transition-colors hover:bg-white/50 hover:text-pine-700"
                          title="拒绝晋升"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </>
                    )}
                    <button
                      onClick={() => setDeletingId(proposal.id)}
                      className="rounded p-1 text-pine-700 transition-colors hover:bg-crimson-500/10 hover:text-crimson-400"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-pine-700">细节晋升</h2>
          <span className="text-xs text-pine-700">{promotions.length} 个提案</span>
        </div>
      </div>

      {candidates.length > 0 && (
        <div className="rounded-xl border border-magic-500/20 bg-magic-500/5 p-4">
          <h3 className="mb-3 text-xs font-medium text-magic-400">晋升候选（引用次数≥3）</h3>
          <div className="space-y-2">
            {candidates.map((c) => (
              <div
                key={c.seed_id}
                className="flex items-center justify-between rounded-lg border border-pine-200/40 bg-white/50 px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-pine-700 line-clamp-1">{c.content}</p>
                  <span className="text-[10px] text-pine-700">引用 {c.reference_count} 次</span>
                </div>
                <button
                  onClick={() => handleCreateFromCandidate(c)}
                  className="ml-3 flex items-center gap-1 rounded-lg bg-magic-500/15 px-2 py-1 text-xs text-magic-400 transition-colors hover:bg-magic-500/25"
                >
                  <ArrowUpFromLine className="h-3 w-3" />
                  创建提案
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {promotions.length === 0 && candidates.length === 0 && (
        <div className="flex flex-col items-center py-12 text-pine-700">
          <ArrowUpFromLine className="mb-3 h-12 w-12" />
          <p className="text-sm">暂无晋升提案</p>
          <p className="text-xs">当细节种子被引用3次以上时，将自动成为晋升候选</p>
        </div>
      )}

      {renderGroup("待审核", pending)}
      {renderGroup("已批准", approved)}
      {renderGroup("已拒绝", rejected)}
      {renderGroup("有冲突", conflict)}

      {showForm && formCandidate && (
        <PromotionFormModal
          candidate={formCandidate}
          saving={saving}
          onSave={handleSave}
          onClose={() => {
            setShowForm(false);
            setFormCandidate(null);
          }}
        />
      )}

      {rejectingId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-96 rounded-xl border border-pine-200/60 bg-white/50 p-5">
            <h3 className="text-sm font-semibold text-pine-700">拒绝晋升</h3>
            <div className="mt-3">
              <label className="mb-1 block text-xs text-pine-700">拒绝原因</label>
              <textarea
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="说明拒绝原因..."
                rows={3}
                className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
              />
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => {
                  setRejectingId(null);
                  setRejectReason("");
                }}
                className="rounded-lg px-3 py-1.5 text-sm text-pine-700 hover:text-pine-700"
              >
                取消
              </button>
              <button
                onClick={handleReject}
                className="rounded-lg bg-crimson-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-crimson-400"
              >
                确认拒绝
              </button>
            </div>
          </div>
        </div>
      )}

      {deletingId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-80 rounded-xl border border-pine-200/60 bg-white/50 p-5">
            <h3 className="text-sm font-semibold text-pine-700">确认删除</h3>
            <p className="mt-2 text-xs text-pine-700">删除后该晋升提案将被移除。</p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setDeletingId(null)}
                className="rounded-lg px-3 py-1.5 text-sm text-pine-700 hover:text-pine-700"
              >
                取消
              </button>
              <button
                onClick={handleDelete}
                className="rounded-lg bg-crimson-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-crimson-400"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function PromotionFormModal({
  candidate,
  saving,
  onSave,
  onClose,
}: {
  candidate: { seed_id: string; content: string; reference_count: number };
  saving: boolean;
  onSave: (data: Partial<PromotionProposal>) => void;
  onClose: () => void;
}) {
  const [targetType, setTargetType] = useState<PromotionProposal["target_type"]>("character");
  const [targetDataText, setTargetDataText] = useState("{}");

  const handleSubmit = () => {
    let targetData: Record<string, unknown> = {};
    try {
      targetData = JSON.parse(targetDataText);
    } catch {
      return;
    }
    onSave({
      seed_id: candidate.seed_id,
      seed_content: candidate.content,
      reference_count: candidate.reference_count,
      target_type: targetType,
      target_data: targetData,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[520px] rounded-xl border border-pine-200/60 bg-white/50 p-6">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-pine-700">创建晋升提案</h3>
          <button onClick={onClose} className="text-pine-700 hover:text-pine-700">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4">
          <div className="rounded-lg border border-pine-200/40 bg-white/50 p-3">
            <p className="text-xs text-pine-700">种子内容</p>
            <p className="mt-1 text-sm text-pine-700">{candidate.content}</p>
            <p className="mt-1 text-[10px] text-pine-700">引用次数：{candidate.reference_count}</p>
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">晋升目标类型</label>
            <div className="flex gap-1">
              {(["character", "world_rule", "location"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTargetType(t)}
                  className={clsx(
                    "rounded-lg border px-3 py-1 text-xs transition-colors",
                    targetType === t
                      ? "border-magic-500/30 bg-magic-500/15 text-magic-400"
                      : "border-pine-200 text-pine-700 hover:text-pine-700"
                  )}
                >
                  {TARGET_TYPE_LABELS[t]}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">晋升数据</label>
            <textarea
              value={targetDataText}
              onChange={(e) => setTargetDataText(e.target.value)}
              placeholder={targetType === "character" ? '{"name": "角色名", "personality": "性格描述"}' : targetType === "world_rule" ? '{"name": "规则名", "description": "规则描述"}' : '{"name": "地点名", "description": "地点描述"}'}
              rows={5}
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 font-mono text-sm text-pine-700 outline-none focus:border-magic-500/50 placeholder:text-pine-700"
            />
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-pine-700 hover:text-pine-700"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving}
            className="rounded-lg bg-magic-500 px-4 py-2 text-sm font-medium text-pine-700 transition-colors hover:bg-magic-400 disabled:opacity-50"
          >
            {saving ? "创建中..." : "创建提案"}
          </button>
        </div>
      </div>
    </div>
  );
}
