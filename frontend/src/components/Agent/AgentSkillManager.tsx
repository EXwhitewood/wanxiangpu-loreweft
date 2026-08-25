/* Hallmark · component: agent-skill-assembly · genre: editorial · theme: Loreweft locked system
 * states: default · hover · focus · active · disabled · loading · error · success
 * pre-emit critique: P5 H5 E5 S5 R5 V4
 */
import { useMemo, useRef, useState } from "react";
import {
  Check,
  FileUp,
  Loader2,
  LockKeyhole,
  PackagePlus,
  Puzzle,
  X,
} from "lucide-react";

import * as api from "@/api/client";
import Modal from "@/components/Common/Modal";
import { GlassButton } from "@/components/UI/GlassButton";
import type { AgentDetail, SkillInfo } from "@/types";

type AgentSkillManagerProps = {
  agent: AgentDetail;
  skills: SkillInfo[];
  enabledSkills: string[];
  enabledAgentSkills: string[];
  disabled?: boolean;
  onChange: (enabledSkills: string[], enabledAgentSkills: string[]) => void;
  onSkillsReloaded: (skills: SkillInfo[]) => void;
};

function unique(values: string[]): string[] {
  return Array.from(new Set(values));
}

function skillBucket(skill: SkillInfo): "utility" | "agent" {
  return skill.category === "utility" ? "utility" : "agent";
}

function categoryLabel(skill: SkillInfo): string {
  if (skill.category === "utility") return "通用";
  if (skill.category === "governance") return "治理";
  return "专属";
}

function sourceLabel(skill: SkillInfo): string {
  if (skill.source === "custom") return "自定义";
  if (skill.source === "worldbuilder_sub_agent") return "子智能体";
  return "内置";
}

function placeholderSkill(name: string): SkillInfo {
  return {
    name,
    display_name: name,
    description: "该 Skill 已装配，但当前注册表没有返回详细说明。",
    category: "agent",
  };
}

export default function AgentSkillManager({
  agent,
  skills,
  enabledSkills,
  enabledAgentSkills,
  disabled = false,
  onChange,
  onSkillsReloaded,
}: AgentSkillManagerProps) {
  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const skillMap = useMemo(
    () => new Map(skills.map((skill) => [skill.name, skill])),
    [skills],
  );
  const installedNames = useMemo(
    () => unique([...enabledSkills, ...enabledAgentSkills]),
    [enabledAgentSkills, enabledSkills],
  );
  const installedSet = useMemo(() => new Set(installedNames), [installedNames]);
  const declaredSet = useMemo(
    () => new Set([
      ...agent.default_skills,
      ...agent.agent_skills,
      ...agent.enabled_skills,
      ...agent.enabled_agent_skills,
    ]),
    [agent],
  );

  const compatibleSkills = useMemo(
    () => skills
      .filter((skill) => (
        declaredSet.has(skill.name)
        || skill.agent === agent.name
        || skill.agents?.includes(agent.name)
      ))
      .sort((a, b) => a.display_name.localeCompare(b.display_name, "zh-CN")),
    [agent.name, declaredSet, skills],
  );
  const installedSkills = installedNames.map((name) => skillMap.get(name) || placeholderSkill(name));
  const availableSkills = compatibleSkills.filter((skill) => !installedSet.has(skill.name));

  const setInstalled = (skill: SkillInfo, installed: boolean) => {
    const bucket = skillBucket(skill);
    if (bucket === "utility") {
      const next = installed
        ? unique([...enabledSkills, skill.name])
        : enabledSkills.filter((name) => name !== skill.name);
      onChange(next, enabledAgentSkills);
      return;
    }
    const next = installed
      ? unique([...enabledAgentSkills, skill.name])
      : enabledAgentSkills.filter((name) => name !== skill.name);
    onChange(enabledSkills, next);
  };

  const closeImport = () => {
    if (importing) return;
    setImportOpen(false);
    setImportFile(null);
    setImportError(null);
  };

  const handleImport = async () => {
    if (!importFile || importing) return;
    setImporting(true);
    setImportError(null);
    try {
      const results = await api.importSkills([importFile], "agent", agent.name);
      const failures = results.filter((result) => result.error);
      const importedNames = results
        .filter((result) => !result.error && result.name)
        .map((result) => result.name as string);
      if (!importedNames.length) {
        throw new Error(failures.map((result) => result.error).filter(Boolean).join("；") || "文件中没有可导入的 Skill。请检查 frontmatter。 ");
      }

      const refreshedSkills = await api.getSkills();
      onSkillsReloaded(refreshedSkills);
      onChange(enabledSkills, unique([...enabledAgentSkills, ...importedNames]));

      const warnings = results.flatMap((result) => result.import_warnings || []);
      setImportNotice(
        warnings.length
          ? `已加入装配草稿；出于安全原因：${warnings.join("；")}`
          : "已加入装配草稿。保存智能体后生效。",
      );
      setImportOpen(false);
      setImportFile(null);
    } catch (error) {
      setImportError(error instanceof Error ? error.message : "Skill 导入失败。请检查文件后重试。");
    } finally {
      setImporting(false);
    }
  };

  return (
    <section className="agent-skill-manager" aria-labelledby="agent-skill-manager-title">
      <div className="agent-skill-manager__heading">
        <div>
          <div className="agent-skill-manager__title-row">
            <Puzzle className="h-4 w-4" aria-hidden="true" />
            <h3 id="agent-skill-manager-title">Skill 装配</h3>
          </div>
          <p>只列出注册表中明确适配当前智能体的能力；更改将在保存智能体后生效。</p>
        </div>
        <div className="agent-skill-manager__heading-actions">
          <span className="agent-skill-manager__count">已装配 {installedSkills.length}</span>
          <button
            type="button"
            className="agent-skill-manager__import"
            onClick={() => {
              setImportError(null);
              setImportOpen(true);
            }}
            disabled={disabled}
          >
            <PackagePlus className="h-4 w-4" aria-hidden="true" />
            导入 Skill
          </button>
        </div>
      </div>

      {importNotice && (
        <div className="agent-skill-manager__notice" role="status">
          <Check className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{importNotice}</span>
          <button type="button" onClick={() => setImportNotice(null)} aria-label="关闭导入提示">
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      )}

      <div className="agent-skill-manager__columns">
        <section className="agent-skill-list" aria-labelledby="installed-skills-title">
          <div className="agent-skill-list__heading">
            <h4 id="installed-skills-title">已装配</h4>
            <span>{installedSkills.length}</span>
          </div>
          {installedSkills.length ? (
            <div className="agent-skill-list__rows">
              {installedSkills.map((skill) => {
                const systemMounted = agent.default_skills.includes(skill.name) || Boolean(skill.enabled_by_default);
                return (
                  <div key={skill.name} className="agent-skill-row agent-skill-row--installed">
                    <div className="agent-skill-row__body">
                      <div className="agent-skill-row__name">
                        <span>{skill.display_name}</span>
                        <span className="agent-skill-row__tag">{categoryLabel(skill)}</span>
                        <span className="agent-skill-row__tag">{sourceLabel(skill)}</span>
                      </div>
                      <code>{skill.name}</code>
                      <p>{skill.description}</p>
                    </div>
                    {systemMounted ? (
                      <span className="agent-skill-row__locked" title="系统默认装配不可从此处移除">
                        <LockKeyhole className="h-3.5 w-3.5" aria-hidden="true" />
                        系统装配
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="agent-skill-row__remove"
                        onClick={() => setInstalled(skill, false)}
                        disabled={disabled}
                      >
                        移除
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="agent-skill-list__empty">当前没有装配 Skill。</p>
          )}
        </section>

        <section className="agent-skill-list" aria-labelledby="available-skills-title">
          <div className="agent-skill-list__heading">
            <h4 id="available-skills-title">可安装</h4>
            <span>{availableSkills.length}</span>
          </div>
          {availableSkills.length ? (
            <div className="agent-skill-list__rows">
              {availableSkills.map((skill) => (
                <div key={skill.name} className="agent-skill-row">
                  <div className="agent-skill-row__body">
                    <div className="agent-skill-row__name">
                      <span>{skill.display_name}</span>
                      <span className="agent-skill-row__tag">{categoryLabel(skill)}</span>
                    </div>
                    <code>{skill.name}</code>
                    <p>{skill.description}</p>
                  </div>
                  <button
                    type="button"
                    className="agent-skill-row__install"
                    onClick={() => setInstalled(skill, true)}
                    disabled={disabled}
                  >
                    安装
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="agent-skill-list__empty">
              当前没有其他可安装 Skill。你可以导入一个绑定到此智能体的技能包。
            </p>
          )}
        </section>
      </div>

      <Modal
        title={`为“${agent.display_name}”导入 Skill`}
        description="支持万象谱技能说明文件或技能包。为保护本机安全，技能包中的外部程序不会直接运行。"
        isOpen={importOpen}
        onClose={closeImport}
        closeDisabled={importing}
        initialFocusRef={fileRef}
        maxWidth="max-w-xl"
        footer={
          <>
            <GlassButton type="button" variant="ghost" onClick={closeImport} disabled={importing} className="h-11 px-5">
              取消
            </GlassButton>
            <GlassButton
              type="button"
              variant="primary"
              onClick={() => void handleImport()}
              disabled={!importFile || importing}
              className="h-11 min-w-[8rem] px-5"
            >
              {importing ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <FileUp className="h-4 w-4" aria-hidden="true" />}
              {importing ? "正在导入…" : "导入并装配"}
            </GlassButton>
          </>
        }
      >
        <div className="agent-skill-import">
          <label htmlFor="agent-skill-file">Skill 文件</label>
          <input
            ref={fileRef}
            id="agent-skill-file"
            type="file"
            accept=".md,.json,text/markdown,application/json"
            disabled={importing}
            aria-describedby="agent-skill-file-help agent-skill-file-error"
            onChange={(event) => {
              setImportFile(event.target.files?.[0] || null);
              setImportError(null);
            }}
          />
          <p id="agent-skill-file-help">
            文件会进入全局 Skill 注册表，并仅绑定到当前智能体。导入后仍需保存智能体设置。
          </p>
          <div id="agent-skill-file-error" className="agent-skill-import__error" role={importError ? "alert" : undefined}>
            {importError || "\u00a0"}
          </div>
        </div>
      </Modal>
    </section>
  );
}
