import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import {
  Activity,
  Boxes,
  Clock3,
  Compass,
  Database,
  Eye,
  HeartPulse,
  Map as MapIcon,
  MapPin,
  Package,
  RefreshCw,
  Search,
  Sun,
  UserRound,
  Users,
} from "lucide-react";
import clsx from "clsx";
import * as api from "@/api/client";
import { useProjectStore } from "@/stores/projectStore";
import type {
  Character,
  EntityState,
  Location,
  LocationState,
  SubjectiveView,
  WorldbuildingOverview,
} from "@/types";

type EntityKind = "character" | "location";
type EntityFilter = "all" | EntityKind;

interface StateEntry {
  name: string;
  kind: EntityKind;
  state?: EntityState | LocationState;
  character?: Character;
  location?: Location;
}

const FIELD_LABELS: Record<string, string> = {
  location: "当前位置",
  emotional_state: "情绪状态",
  physical_state: "身体状态",
  inventory: "随身物品",
  alive: "生命状态",
  atmosphere: "环境氛围",
  light_source: "光源",
  objects_present: "场内物件",
  condition: "地点状况",
  believed_state: "认知状态",
  last_known: "最后所知",
  role: "角色定位",
  faction: "所属阵营",
  lifecycle_status: "生命状态",
  narrative_activity: "叙事活跃度",
  last_seen_chapter: "最近出场章节",
  status: "角色状态",
  description: "地点说明",
  parent_location: "上级地点",
  location_type: "地点类型",
  function: "叙事功能",
  rules: "地点规则",
  first_seen_chapter: "首次出现章节",
};

const VALUE_LABELS: Record<string, string> = {
  alive: "存活",
  deceased: "已死亡",
  missing: "失踪",
  transformed: "已转化",
  unknown: "未知",
  core_active: "核心活跃",
  scene_active: "场景活跃",
  dormant: "暂未活跃",
  archived: "已归档",
};

function isCharacterState(state: EntityState | LocationState): state is EntityState {
  return "alive" in state || "inventory" in state || "emotional_state" in state;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未记录";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.length ? value.map(String).join("、") : "未记录";
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) return "未记录";
    return entries.map(([key, item]) => `${FIELD_LABELS[key] || key}：${formatValue(item)}`).join("；");
  }
  const text = String(value);
  return VALUE_LABELS[text] || text;
}

function FactCell({ icon, label, value }: { icon: ReactNode; label: string; value: unknown }) {
  return (
    <div className="min-w-0 border-b border-pine-900/10 py-4 last:border-b-0 md:border-b-0 md:border-r md:px-5 md:first:pl-0 md:last:border-r-0">
      <div className="mb-1.5 flex items-center gap-2 text-xs font-medium text-pine-700">
        {icon}
        <span>{label}</span>
      </div>
      <div className="min-w-0 [overflow-wrap:anywhere] text-sm font-semibold text-pine-950">
        {formatValue(value)}
      </div>
    </div>
  );
}

function RecordRows({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value || {});
  if (!entries.length) return <div className="py-4 text-sm text-pine-700">暂无记录</div>;

  return (
    <div className="divide-y divide-pine-900/10">
      {entries.map(([key, item]) => (
        <div key={key} className="grid gap-1 py-3 sm:grid-cols-[140px_minmax(0,1fr)] sm:gap-5">
          <div className="text-xs font-medium text-pine-700">{FIELD_LABELS[key] || key}</div>
          <div className="min-w-0 [overflow-wrap:anywhere] text-sm leading-6 text-pine-950">{formatValue(item)}</div>
        </div>
      ))}
    </div>
  );
}

function SubjectiveRecord({ view }: { view: SubjectiveView }) {
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <div>
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-pine-700">
          <Eye className="h-4 w-4" />
          认知状态
        </div>
        <RecordRows value={view.believed_state || {}} />
      </div>
      <div>
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-pine-700">
          <Clock3 className="h-4 w-4" />
          最后所知
        </div>
        <RecordRows value={view.last_known || {}} />
      </div>
    </div>
  );
}

export default function StateEditorPage() {
  const { id } = useParams<{ id: string }>();
  const { storyState, fetchState, loading, error } = useProjectStore();
  const [overview, setOverview] = useState<WorldbuildingOverview | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<EntityFilter>("all");
  const [selectedEntity, setSelectedEntity] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    void fetchState(id);
    setCatalogLoading(true);
    setCatalogError(null);
    api.getWorldbuildingOverview(id)
      .then(setOverview)
      .catch((reason) => setCatalogError((reason as Error).message))
      .finally(() => setCatalogLoading(false));
  }, [id, fetchState]);

  const refresh = async () => {
    if (!id) return;
    setCatalogLoading(true);
    setCatalogError(null);
    const [, catalogResult] = await Promise.allSettled([
      fetchState(id),
      api.getWorldbuildingOverview(id),
    ]);
    if (catalogResult.status === "fulfilled") {
      setOverview(catalogResult.value);
    } else {
      setCatalogError(catalogResult.reason instanceof Error ? catalogResult.reason.message : "核心资料读取失败");
    }
    setCatalogLoading(false);
  };

  const entries = useMemo<StateEntry[]>(() => {
    const merged = new Map<string, StateEntry>();
    const aliasToName = new Map<string, string>();
    const normalize = (value: string) => value.trim().toLocaleLowerCase();

    for (const character of overview?.characters || []) {
      const key = normalize(character.name);
      merged.set(key, { name: character.name, kind: "character", character });
      aliasToName.set(key, key);
      for (const alias of character.aliases || []) aliasToName.set(normalize(alias), key);
    }
    for (const location of overview?.locations || []) {
      const key = normalize(location.name);
      merged.set(key, { name: location.name, kind: "location", location });
      aliasToName.set(key, key);
    }

    for (const [name, state] of Object.entries(storyState?.objective_state || {})) {
      const normalizedName = normalize(name);
      const key = aliasToName.get(normalizedName) || normalizedName;
      const existing = merged.get(key);
      merged.set(key, {
        ...existing,
        name: existing?.name || name,
        kind: existing?.kind || (isCharacterState(state) ? "character" : "location"),
        state,
      });
    }

    return Array.from(merged.values()).sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
  }, [overview, storyState]);

  const visibleEntries = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return entries.filter((entry) => {
      if (filter !== "all" && entry.kind !== filter) return false;
      if (!normalizedQuery) return true;
      return `${entry.name} ${formatValue(entry.state)} ${formatValue(entry.character)} ${formatValue(entry.location)}`
        .toLocaleLowerCase()
        .includes(normalizedQuery);
    });
  }, [entries, filter, query]);

  useEffect(() => {
    if (!visibleEntries.length) {
      setSelectedEntity(null);
      return;
    }
    if (!selectedEntity || !visibleEntries.some((entry) => entry.name === selectedEntity)) {
      setSelectedEntity(visibleEntries[0].name);
    }
  }, [selectedEntity, visibleEntries]);

  const selected = entries.find((entry) => entry.name === selectedEntity) || null;
  const characterCount = entries.filter((entry) => entry.kind === "character").length;
  const locationCount = entries.length - characterCount;
  const subjectiveView = selected ? storyState?.subjective_views?.[selected.name] : undefined;
  const selectedCharacterState = selected?.state && isCharacterState(selected.state) ? selected.state : undefined;
  const selectedLocationState = selected?.state && !isCharacterState(selected.state) ? selected.state : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--workspace-document-surface)] text-pine-950">
      <header className="shrink-0 border-b border-[var(--workspace-border)] bg-[var(--workspace-chrome)] px-5 py-4 lg:px-7">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs font-semibold text-pine-700">
              <Database className="h-4 w-4 text-[var(--workspace-accent)]" />
              当前事实快照
            </div>
            <h1 className="mt-1 [overflow-wrap:anywhere] text-xl font-bold text-pine-950">当前世界状态</h1>
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={!id || loading || catalogLoading}
            title="刷新状态"
            aria-label="刷新状态"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-pine-900/10 bg-white text-pine-700 transition-colors hover:bg-pine-50 hover:text-pine-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magic-500 disabled:opacity-50"
          >
            <RefreshCw className={clsx("h-4 w-4", (loading || catalogLoading) && "animate-spin")} />
          </button>
        </div>
      </header>

      <section className="shrink-0 border-b border-[var(--workspace-border)] bg-white/55 px-5 lg:px-7">
        <div className="grid md:grid-cols-5">
          <FactCell icon={<Activity className="h-4 w-4" />} label="状态截至" value={`第 ${storyState?.active_chapter || 1} 章 · 场景 ${(storyState?.active_scene || 0) + 1}`} />
          <FactCell icon={<Clock3 className="h-4 w-4" />} label="叙事时间" value={storyState?.narrative_time} />
          <FactCell icon={<Eye className="h-4 w-4" />} label="当前视角" value={storyState?.pov_character} />
          <FactCell icon={<Users className="h-4 w-4" />} label="角色" value={`${characterCount} 个`} />
          <FactCell icon={<MapIcon className="h-4 w-4" />} label="地点" value={`${locationCount} 个`} />
        </div>
      </section>

      {(error || catalogError) && (
        <div className="mx-5 mt-4 border-l-2 border-crimson-500 bg-crimson-50 px-4 py-3 text-sm text-crimson-700 lg:mx-7">
          {error || catalogError}
        </div>
      )}

      <main className="grid min-h-0 flex-1 gap-4 overflow-y-auto p-4 lg:p-6 xl:grid-cols-[300px_minmax(0,1fr)] xl:overflow-hidden">
        <aside className="flex min-h-[360px] flex-col overflow-hidden rounded-lg border border-[var(--workspace-border)] bg-white xl:min-h-0">
          <div className="border-b border-pine-900/10 p-3">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-pine-700" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索角色、地点或状态"
                className="h-9 w-full rounded-lg border border-pine-900/10 bg-pine-50/60 pl-9 pr-3 text-sm text-pine-950 outline-none placeholder:text-pine-700 focus:border-magic-500 focus:ring-2 focus:ring-magic-500/15"
              />
            </div>
            <div className="mt-3 grid grid-cols-3 rounded-lg bg-pine-900/[0.04] p-1" aria-label="实体类型筛选">
              {([
                ["all", "全部"],
                ["character", "角色"],
                ["location", "地点"],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setFilter(value)}
                  className={clsx(
                    "h-8 rounded-md text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magic-500",
                    filter === value ? "bg-white text-pine-950 shadow-sm" : "text-pine-700 hover:text-pine-950",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {visibleEntries.map((entry) => {
              const character = entry.state && isCharacterState(entry.state) ? entry.state : null;
              const location = entry.state && !isCharacterState(entry.state) ? entry.state : null;
              return (
                <button
                  key={entry.name}
                  type="button"
                  onClick={() => setSelectedEntity(entry.name)}
                  className={clsx(
                    "mb-1 flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magic-500",
                    selectedEntity === entry.name
                      ? "bg-[var(--workspace-sidebar-active)] text-pine-950"
                      : "text-pine-800 hover:bg-pine-900/[0.04]",
                  )}
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-pine-900/[0.05] text-[var(--workspace-accent)]">
                    {entry.kind === "character" ? <UserRound className="h-4 w-4" /> : <MapPin className="h-4 w-4" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold">{entry.name}</span>
                    <span className="mt-0.5 block truncate text-xs text-pine-700">
                      {entry.kind === "character"
                        ? formatValue(character?.location || entry.character?.narrative_activity || entry.character?.status)
                        : formatValue(location?.condition || entry.location?.atmosphere)}
                    </span>
                  </span>
                </button>
              );
            })}
            {!visibleEntries.length && (
              <div className="px-3 py-10 text-center text-sm text-pine-700">没有匹配的状态记录</div>
            )}
          </div>
        </aside>

        <section className="min-w-0 rounded-lg border border-[var(--workspace-border)] bg-white xl:flex xl:min-h-0 xl:flex-col xl:overflow-hidden">
          {selected ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-pine-900/10 px-5 py-4 lg:px-6">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-xs font-medium text-pine-700">
                    {selected.kind === "character" ? <UserRound className="h-4 w-4" /> : <MapPin className="h-4 w-4" />}
                    {selected.kind === "character" ? "角色状态" : "地点状态"}
                  </div>
                  <h2 className="mt-1 [overflow-wrap:anywhere] text-lg font-bold text-pine-950">{selected.name}</h2>
                </div>
                <div className="rounded-full border border-pine-900/10 bg-pine-50 px-2.5 py-1 text-xs text-pine-700">
                  {storyState?.timeline_id === "timeline_main" ? "主时间线" : storyState?.timeline_id || "当前时间线"}
                </div>
              </div>

              <div className="space-y-7 p-5 lg:p-6 xl:min-h-0 xl:flex-1 xl:overflow-y-auto">
                {selected.kind === "character" ? (
                  <>
                    <div className="grid gap-x-5 sm:grid-cols-2 xl:grid-cols-3">
                      <FactCell icon={<MapPin className="h-4 w-4" />} label="当前位置" value={selectedCharacterState?.location} />
                      <FactCell icon={<Activity className="h-4 w-4" />} label="情绪状态" value={selectedCharacterState?.emotional_state} />
                      <FactCell icon={<HeartPulse className="h-4 w-4" />} label="身体状态" value={selectedCharacterState?.physical_state} />
                      <FactCell icon={<Package className="h-4 w-4" />} label="随身物品" value={selectedCharacterState?.inventory} />
                      <FactCell
                        icon={<UserRound className="h-4 w-4" />}
                        label="生命状态"
                        value={selectedCharacterState ? (selectedCharacterState.alive ? "存活" : "已死亡") : selected.character?.lifecycle_status}
                      />
                    </div>
                    {selectedCharacterState?.extra && Object.keys(selectedCharacterState.extra).length > 0 && (
                      <div>
                        <div className="flex items-center gap-2 border-b border-pine-900/10 pb-2 text-sm font-semibold text-pine-950">
                          <Boxes className="h-4 w-4 text-[var(--workspace-accent)]" />
                          其他状态
                        </div>
                        <RecordRows value={selectedCharacterState.extra} />
                      </div>
                    )}
                    {selected.character && (
                      <div>
                        <div className="flex items-center gap-2 border-b border-pine-900/10 pb-2 text-sm font-semibold text-pine-950">
                          <Database className="h-4 w-4 text-[var(--workspace-accent)]" />
                          核心资料状态
                        </div>
                        <RecordRows value={{
                          role: selected.character.role,
                          faction: selected.character.faction,
                          status: selected.character.status,
                          lifecycle_status: selected.character.lifecycle_status,
                          narrative_activity: selected.character.narrative_activity,
                          last_seen_chapter: selected.character.last_seen_chapter,
                        }} />
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <div className="grid gap-x-5 sm:grid-cols-2 xl:grid-cols-3">
                      <FactCell icon={<Compass className="h-4 w-4" />} label="地点状况" value={selectedLocationState?.condition} />
                      <FactCell icon={<Activity className="h-4 w-4" />} label="环境氛围" value={selectedLocationState?.atmosphere || selected.location?.atmosphere} />
                      <FactCell icon={<Sun className="h-4 w-4" />} label="光源" value={selectedLocationState?.light_source} />
                      <FactCell icon={<Boxes className="h-4 w-4" />} label="场内物件" value={selectedLocationState?.objects_present} />
                    </div>
                    {selected.location && (
                      <div>
                        <div className="flex items-center gap-2 border-b border-pine-900/10 pb-2 text-sm font-semibold text-pine-950">
                          <Database className="h-4 w-4 text-[var(--workspace-accent)]" />
                          地点档案
                        </div>
                        <RecordRows value={{
                          description: selected.location.description,
                          parent_location: selected.location.parent_location,
                          location_type: selected.location.location_type,
                          function: selected.location.function,
                          rules: selected.location.rules,
                          first_seen_chapter: selected.location.first_seen_chapter,
                        }} />
                      </div>
                    )}
                  </>
                )}

                {subjectiveView && (
                  <div>
                    <div className="flex items-center gap-2 border-b border-pine-900/10 pb-2 text-sm font-semibold text-pine-950">
                      <Eye className="h-4 w-4 text-[var(--workspace-accent)]" />
                      认知记录
                    </div>
                    <div className="pt-4">
                      <SubjectiveRecord view={subjectiveView} />
                    </div>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex min-h-[360px] flex-col items-center justify-center px-6 text-center">
              <Database className="h-8 w-8 text-pine-400" />
              <div className="mt-3 text-sm font-semibold text-pine-950">暂无可展示的世界状态</div>
              <div className="mt-1 text-xs text-pine-700">完成章节生成并提交状态后，这里会显示当前事实快照。</div>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
