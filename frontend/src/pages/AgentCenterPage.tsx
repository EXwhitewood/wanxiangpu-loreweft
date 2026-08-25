/* Hallmark - pre-emit critique: P5 H5 E5 S5 R5 V5 - macrostructure: catalogue -> detail - tone: quiet studio - contrast: semantic-token pass */
import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  Eye,
  EyeOff,
  Feather,
  GitBranch,
  Globe2,
  KeyRound,
  Loader2,
  PenTool,
  RotateCcw,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  ScrollText,
  TestTube2,
  Wrench,
  XCircle,
} from "lucide-react";
import * as api from "@/api/client";
import AgentSkillManager from "@/components/Agent/AgentSkillManager";
import Toast from "@/components/Common/Toast";
import { FadeInWrapper } from "@/components/UI/FadeInWrapper";
import { useSettingsStore } from "@/stores/settingsStore";
import type { AgentDetail, AgentOverride, APIFormat, AppSettings, SkillInfo } from "@/types";
import { cn } from "@/utils/cn";

const AGENT_GROUPS = [
  { id: "outline", label: "大纲设计", description: "全书结构、章节脊柱与场景节拍", icon: ScrollText },
  { id: "worldbuilding", label: "世界观", description: "规则、人物、地点与伏笔体系", icon: Globe2 },
  { id: "creation", label: "正文创作", description: "生成调度、正文写作与作者协作", icon: PenTool },
  { id: "plot", label: "剧情推演", description: "分支探索、细节追踪与阅读分析", icon: GitBranch },
  { id: "style", label: "文笔系统", description: "目标文风的学习与复用", icon: Feather },
  { id: "quality", label: "质量审校", description: "场景校验与提交后的状态提取", icon: ShieldCheck },
  { id: "repair", label: "智能修订", description: "受控修复、补丁执行与风险仲裁", icon: Wrench },
] as const;

type AgentGroupId = typeof AGENT_GROUPS[number]["id"];

const AGENT_GROUP_BY_NAME: Record<string, AgentGroupId> = {
  outline_architect: "outline",
  worldbuilder: "worldbuilding",
  editor_in_chief: "creation",
  core_generation: "creation",
  writing_companion: "creation",
  branch_explorer: "plot",
  detail_detective: "plot",
  plot_commentator: "plot",
  style_learner: "style",
  scene_validator: "quality",
  scene_post_processor: "quality",
  scene_repairer: "repair",
  content_repair: "repair",
  style_repair: "repair",
  budget_arbiter: "repair",
};

function agentGroup(name: string) {
  const groupId = AGENT_GROUP_BY_NAME[name] || "creation";
  return AGENT_GROUPS.find((group) => group.id === groupId) || AGENT_GROUPS[2];
}

function emptyOverride(): AgentOverride {
  return {
    api_format: null,
    api_key: null,
    base_url: null,
    model: null,
    skills: null,
    agent_skills: null,
    persona: null,
  };
}

function normalizeOverride(override?: Partial<AgentOverride>): AgentOverride {
  return { ...emptyOverride(), ...override };
}

function hasApiOverride(override?: Partial<AgentOverride>): boolean {
  return Boolean(override?.api_format || override?.api_key || override?.base_url || override?.model);
}

function hasAgentCustomization(override?: Partial<AgentOverride>): boolean {
  return Boolean(
    hasApiOverride(override)
    || override?.skills !== null && override?.skills !== undefined
    || override?.agent_skills !== null && override?.agent_skills !== undefined
    || override?.persona,
  );
}

function apiFormatLabel(format?: APIFormat | null): string {
  if (format === "anthropic_compatible") return "Anthropic 兼容";
  if (format === "openai_compatible") return "OpenAI 兼容";
  return "继承全局";
}

type AgentDirectoryProps = {
  agents: AgentDetail[];
  settings: AppSettings;
  query: string;
  setQuery: (value: string) => void;
};

function AgentDirectory({ agents, settings, query, setQuery }: AgentDirectoryProps) {
  const groups = AGENT_GROUPS.map((group) => ({
    group,
    agents: agents.filter((agent) => agentGroup(agent.name).id === group.id),
  })).filter(({ agents: groupAgents }) => groupAgents.length > 0);

  return (
    <section className="agent-directory" aria-label="智能体目录">
      <div className="agent-directory__toolbar">
        <div>
          <p className="agent-directory__eyebrow">工作单元目录</p>
          <h2 className="agent-directory__title">选择一个智能体，先了解它负责什么</h2>
        </div>
        <label className="agent-directory__search">
          <Search className="h-4 w-4 shrink-0" aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索智能体"
            aria-label="搜索智能体"
          />
        </label>
      </div>

      <div className="agent-directory__groups">
        {groups.map(({ group, agents: groupAgents }) => {
          const GroupIcon = group.icon;
          return (
            <section key={group.id} className="agent-directory__group">
              <div className="agent-directory__group-heading">
                <div className="agent-directory__group-mark"><GroupIcon className="h-4 w-4" aria-hidden="true" /></div>
                <div>
                  <div className="agent-directory__group-title-row">
                    <h3>{group.label}</h3>
                    <span>{groupAgents.length}</span>
                  </div>
                  <p>{group.description}</p>
                </div>
              </div>
              <div className="agent-directory__grid">
                {groupAgents.map((agent) => {
                  const override = settings.agent_overrides[agent.name];
                  const apiOverridden = hasApiOverride(override);
                  const customized = hasAgentCustomization(override);
                  const installedCount = agent.enabled_skills.length + agent.enabled_agent_skills.length;
                  return (
                    <Link key={agent.name} to={`/agents/${agent.name}`} className="agent-card group">
                      <div className="agent-card__topline">
                        <div className="agent-card__icon"><GroupIcon className="h-5 w-5" aria-hidden="true" /></div>
                        <span className={cn("agent-card__status", customized && "agent-card__status--custom")}>
                          <span className="agent-card__status-dot" />
                          {apiOverridden ? "独立 API" : customized ? "自定义装配" : "系统默认"}
                        </span>
                      </div>
                      <div className="agent-card__body">
                        <p className="agent-card__id">{agent.name}</p>
                        <h4>{agent.display_name}</h4>
                        <p className="agent-card__description">{agent.description}</p>
                      </div>
                      <div className="agent-card__footer">
                        <span>{installedCount} 个 Skill</span>
                        <span className="agent-card__link-label">打开设置 <ArrowUpRight className="h-4 w-4" aria-hidden="true" /></span>
                      </div>
                    </Link>
                  );
                })}
              </div>
            </section>
          );
        })}
        {!groups.length && <p className="agent-directory__empty">没有匹配的智能体</p>}
      </div>
    </section>
  );
}

type AgentSettingsViewProps = {
  selectedAgent: AgentDetail;
  skills: SkillInfo[];
  initialOverride: AgentOverride;
  draft: AgentOverride;
  setDraft: Dispatch<SetStateAction<AgentOverride>>;
  showKey: boolean;
  setShowKey: Dispatch<SetStateAction<boolean>>;
  saving: boolean;
  testing: boolean;
  isDirty: boolean;
  testResult: { success: boolean; message: string } | null;
  onSave: () => void;
  onReset: () => void;
  onTest: () => void;
  onSkillsReloaded: (skills: SkillInfo[]) => void;
};

function AgentSettingsView({
  selectedAgent,
  skills,
  initialOverride,
  draft,
  setDraft,
  showKey,
  setShowKey,
  saving,
  testing,
  isDirty,
  testResult,
  onSave,
  onReset,
  onTest,
  onSkillsReloaded,
}: AgentSettingsViewProps) {
  const group = agentGroup(selectedAgent.name);
  const customized = hasAgentCustomization(initialOverride);
  const enabledSkills = draft.skills ?? selectedAgent.enabled_skills;
  const enabledAgentSkills = draft.agent_skills ?? selectedAgent.enabled_agent_skills;

  return (
    <section className="agent-settings-page" aria-label={`${selectedAgent.display_name}设置`}>
      <div className="agent-settings__topbar">
        <Link to="/agents" className="agent-settings__back">
          <ArrowLeft className="h-4 w-4" />
          智能体目录
        </Link>
        <span className="agent-settings__path">智能体中心 / 单独设置</span>
      </div>

      <article className="agent-settings__card">
        <header className="agent-settings__hero">
          <div className="agent-settings__hero-icon"><Bot className="h-6 w-6" /></div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2>{selectedAgent.display_name}</h2>
              <span className={cn("agent-settings__badge", customized && "agent-settings__badge--custom")}>
                {hasApiOverride(initialOverride) ? "独立 API" : customized ? "自定义装配" : "系统默认"}
              </span>
            </div>
            <p className="agent-settings__name">{selectedAgent.name}</p>
            <p className="agent-settings__description">{selectedAgent.description}</p>
          </div>
          <span className="agent-settings__group">{group.label}</span>
        </header>

        <section className="agent-settings__effective">
          <div className="agent-settings__section-heading">
            <span>当前生效配置</span>
            <span className="agent-settings__section-note">只读摘要</span>
          </div>
          <dl className="agent-settings__effective-grid">
            <div><dt>接口格式</dt><dd>{apiFormatLabel(selectedAgent.config.api_format)}</dd></div>
            <div><dt>模型</dt><dd title={selectedAgent.config.model || "未配置"}>{selectedAgent.config.model || "未配置"}</dd></div>
            <div><dt>连接地址</dt><dd title={selectedAgent.config.base_url || "未配置"}>{selectedAgent.config.base_url || "未配置"}</dd></div>
          </dl>
        </section>

        <AgentSkillManager
          agent={selectedAgent}
          skills={skills}
          enabledSkills={enabledSkills}
          enabledAgentSkills={enabledAgentSkills}
          disabled={saving || testing}
          onChange={(nextSkills, nextAgentSkills) => {
            setDraft((current) => ({
              ...current,
              skills: nextSkills,
              agent_skills: nextAgentSkills,
            }));
          }}
          onSkillsReloaded={onSkillsReloaded}
        />

        <section className="agent-settings__form-section">
          <div className="agent-settings__section-heading agent-settings__section-heading--form">
            <div className="flex items-center gap-2"><KeyRound className="h-4 w-4" /><span>独立 API 覆盖</span></div>
            <span className="agent-settings__section-note">留空即继承全局</span>
          </div>

          <div className="agent-settings__field">
            <label>接口格式</label>
            <div className="agent-settings__segmented" role="group" aria-label="接口格式">
              {([
                [null, "继承全局"],
                ["openai_compatible", "OpenAI"],
                ["anthropic_compatible", "Anthropic"],
              ] as Array<[APIFormat | null, string]>).map(([value, label]) => (
                <button
                  key={value || "global"}
                  type="button"
                  disabled={saving || testing}
                  onClick={() => setDraft((current) => ({ ...current, api_format: value }))}
                  aria-pressed={draft.api_format === value}
                  className={cn("agent-settings__segment", draft.api_format === value && "agent-settings__segment--active")}
                >{label}</button>
              ))}
            </div>
          </div>

          <div className="agent-settings__fields-grid">
            <label className="agent-settings__field">
              <span>API 密钥</span>
              <span className="relative block">
                <input
                  type={showKey ? "text" : "password"}
                  disabled={saving || testing}
                  value={draft.api_key || ""}
                  onChange={(event) => setDraft((current) => ({ ...current, api_key: event.target.value || null }))}
                  placeholder={selectedAgent.config.api_key ? `继承 ${selectedAgent.config.api_key}` : "未配置"}
                />
                <button type="button" disabled={saving || testing} onClick={() => setShowKey((value) => !value)} title={showKey ? "隐藏密钥" : "显示密钥"} aria-label={showKey ? "隐藏密钥" : "显示密钥"} className="agent-settings__password-toggle">
                  {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </span>
            </label>
            <label className="agent-settings__field">
              <span>默认模型</span>
              <input type="text" disabled={saving || testing} value={draft.model || ""} onChange={(event) => setDraft((current) => ({ ...current, model: event.target.value || null }))} placeholder={selectedAgent.config.model || "继承全局配置"} />
            </label>
            <label className="agent-settings__field sm:col-span-2">
              <span>接口地址</span>
              <input type="url" disabled={saving || testing} value={draft.base_url || ""} onChange={(event) => setDraft((current) => ({ ...current, base_url: event.target.value || null }))} placeholder={selectedAgent.config.base_url || "继承全局配置"} />
            </label>
          </div>
        </section>

        <footer className="agent-settings__footer">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <button type="button" onClick={onTest} disabled={testing || saving} className="agent-settings__secondary-action">
              {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <TestTube2 className="h-4 w-4" />}
              测试连接
            </button>
            {hasApiOverride(initialOverride) && <button type="button" onClick={onReset} disabled={saving || testing} className="agent-settings__reset-action"><RotateCcw className="h-4 w-4" />恢复继承</button>}
            {testResult && (
              <span className={cn("agent-settings__test-result", testResult.success ? "agent-settings__test-result--success" : "agent-settings__test-result--error")}>
                {testResult.success ? <CheckCircle2 className="h-4 w-4 shrink-0" /> : <XCircle className="h-4 w-4 shrink-0" />}
                <span>{testResult.message}</span>
              </span>
            )}
          </div>
          <button type="button" onClick={onSave} disabled={!isDirty || saving || testing} className="agent-settings__save">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            保存智能体
          </button>
        </footer>
      </article>
    </section>
  );
}

export default function AgentCenterPage() {
  const { agentName } = useParams<{ agentName?: string }>();
  const {
    settings,
    error: settingsError,
    fetchSettings,
    updateAgentConfig,
    testConnection,
  } = useSettingsStore();
  const [agents, setAgents] = useState<AgentDetail[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<AgentOverride>(emptyOverride);
  const [initializing, setInitializing] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const load = async () => {
    setInitializing(true);
    setLoadError(null);
    try {
      const [, nextAgents, nextSkills] = await Promise.all([
        fetchSettings(),
        api.getAgents(),
        api.getSkills(),
      ]);
      setAgents(nextAgents);
      setSkills(nextSkills);
    } catch (loadFailure) {
      setLoadError(loadFailure instanceof Error ? loadFailure.message : "无法读取智能体与 Skill 注册表。 ");
    } finally {
      setInitializing(false);
    }
  };

  useEffect(() => { void load(); }, [fetchSettings]);

  const selectedName = agentName || "";
  const selectedAgent = useMemo(() => agents.find((agent) => agent.name === selectedName) || null, [agents, selectedName]);
  const initialOverride = useMemo(() => normalizeOverride(settings.agent_overrides[selectedName]), [selectedName, settings.agent_overrides]);
  const isDirty = JSON.stringify(draft) !== JSON.stringify(initialOverride);
  const overriddenCount = agents.filter((agent) => hasAgentCustomization(settings.agent_overrides[agent.name])).length;
  const installedSkillCount = agents.reduce(
    (total, agent) => total + agent.enabled_skills.length + agent.enabled_agent_skills.length,
    0,
  );

  const visibleAgents = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    if (!normalizedQuery) return agents;
    return agents.filter((agent) => {
      const group = agentGroup(agent.name);
      return [agent.name, agent.display_name, group.label, group.description, agent.description]
        .join(" ")
        .toLocaleLowerCase()
        .includes(normalizedQuery);
    });
  }, [agents, query]);

  useEffect(() => {
    if (!selectedName) {
      setDraft(emptyOverride());
      return;
    }
    setDraft(normalizeOverride(settings.agent_overrides[selectedName]));
    setTestResult(null);
    setShowKey(false);
  }, [selectedName, settings.agent_overrides]);

  const handleSave = async () => {
    if (!selectedAgent) return;
    setSaving(true);
    try {
      await updateAgentConfig(selectedAgent.name, draft);
      const [, nextAgents] = await Promise.all([fetchSettings(), api.getAgents()]);
      setAgents(nextAgents);
    } catch {
      setToast({ type: "error", message: "智能体配置保存失败" });
    } finally { setSaving(false); }
  };

  const handleReset = async () => {
    if (!selectedAgent) return;
    const inheritedDraft: AgentOverride = { ...draft, api_format: null, api_key: null, base_url: null, model: null };
    setSaving(true);
    try {
      await updateAgentConfig(selectedAgent.name, inheritedDraft);
      const [, nextAgents] = await Promise.all([fetchSettings(), api.getAgents()]);
      setAgents(nextAgents);
      setDraft(inheritedDraft);
      setTestResult(null);
    } catch {
      setToast({ type: "error", message: "恢复全局配置失败" });
    } finally { setSaving(false); }
  };

  const handleTest = async () => {
    if (!selectedAgent) return;
    const effective = selectedAgent.config;
    setTesting(true);
    setTestResult(null);
    const result = await testConnection({
      api_format: draft.api_format || effective.api_format || "openai_compatible",
      api_key: draft.api_key || effective.api_key || "",
      base_url: draft.base_url || effective.base_url || "",
      model: draft.model || effective.model || "",
    });
    setTestResult(result);
    setTesting(false);
  };

  const isDetail = Boolean(agentName);
  const title = isDetail && selectedAgent ? selectedAgent.display_name : "智能体中心";
  const subtitle = isDetail
    ? "管理当前智能体的 Skill 装配、专属通道与模型"
    : "按小说工作阶段查找智能体，再进入单独设置";

  return (
    <FadeInWrapper className="agent-center-page relative z-10 h-full min-w-0 overflow-x-clip overflow-y-auto px-4 py-8 sm:px-6 lg:px-10">
      <div className="mx-auto w-full max-w-7xl pb-10">
        <header className="agent-center-header">
          <div className="agent-center-header__top">
            <div className="min-w-0">
              <div className="agent-center-header__kicker"><Bot className="h-4 w-4" aria-hidden="true" /><span>{isDetail ? "智能体设置" : "智能体工作台"}</span></div>
              <h1>{title}</h1>
              <p>{subtitle}</p>
            </div>
            <Link to="/settings" className="agent-center-header__global"><Settings2 className="h-4 w-4" />全局 API</Link>
          </div>
          <dl className="agent-center-header__stats">
            <div><dt>全部智能体</dt><dd>{agents.length}</dd></div>
            <div><dt>自定义配置</dt><dd>{overriddenCount}</dd></div>
            <div><dt>Skill 装配</dt><dd>{installedSkillCount}</dd></div>
          </dl>
        </header>

        {initializing ? (
          <div className="agent-center-state"><Loader2 className="h-5 w-5 animate-spin" />正在读取智能体配置</div>
        ) : (loadError || settingsError) && !agents.length ? (
          <div className="agent-center-state agent-center-state--error"><XCircle className="h-6 w-6" /><p>{loadError || settingsError}</p><button type="button" onClick={() => void load()}>重新读取</button></div>
        ) : isDetail && !selectedAgent ? (
          <div className="agent-center-state"><p>没有找到这个智能体。</p><Link to="/agents">返回智能体目录</Link></div>
        ) : isDetail && selectedAgent ? (
          <AgentSettingsView
            selectedAgent={selectedAgent}
            skills={skills}
            initialOverride={initialOverride}
            draft={draft}
            setDraft={setDraft}
            showKey={showKey}
            setShowKey={setShowKey}
            saving={saving}
            testing={testing}
            isDirty={isDirty}
            testResult={testResult}
            onSave={() => void handleSave()}
            onReset={() => void handleReset()}
            onTest={() => void handleTest()}
            onSkillsReloaded={setSkills}
          />
        ) : (
          <AgentDirectory agents={visibleAgents} settings={settings} query={query} setQuery={setQuery} />
        )}
      </div>
      {toast && <Toast type={toast.type} message={toast.message} onClose={() => setToast(null)} />}
    </FadeInWrapper>
  );
}
