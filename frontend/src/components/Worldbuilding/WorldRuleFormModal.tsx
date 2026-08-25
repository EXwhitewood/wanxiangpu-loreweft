/*
 * Hallmark · pre-emit critique: P5 H5 E4 S5 R5 V5
 * Component: world-rule form modal · genre/theme: DESIGN.md locked editorial instrument
 * Responsive: 320 / 375 / 414 / 768 px · states: default / hover / focus / active / disabled / loading / error / success
 * Slop: static component gates pass · contrast/tokens pass · browser visual QA pending
 */
import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { Check, CircleAlert, ListChecks, Loader2, LockKeyhole } from "lucide-react";
import clsx from "clsx";

import Modal from "@/components/Common/Modal";
import type { WorldRule } from "@/types";

type RulePriority = WorldRule["priority"];

const PRIORITY_OPTIONS: Array<{
  value: RulePriority;
  label: string;
  description: string;
}> = [
  { value: "critical", label: "宪法级", description: "世界底层真理，任何内容都不得违背" },
  { value: "high", label: "高优先", description: "重要设定，仅在明确剧情需要时调整" },
  { value: "normal", label: "普通", description: "常规约束，作为默认写作依据" },
  { value: "low", label: "低优先", description: "倾向性设定，可在合理情节中让步" },
];

const fieldClassName =
  "w-full rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--surface-paper)] px-3 text-sm text-[var(--color-ink-strong)] outline outline-2 outline-offset-1 outline-transparent transition-[border-color,background-color] duration-150 placeholder:text-[var(--color-ink-muted)] hover:border-[var(--border-emphasis)] focus:border-[var(--color-accent)] focus-visible:outline-[var(--color-accent)] disabled:cursor-not-allowed disabled:opacity-60";

interface WorldRuleFormModalProps {
  rule: WorldRule | null;
  saving: boolean;
  categoryLabels: Record<string, string>;
  onSave: (data: Partial<WorldRule>) => void | Promise<void>;
  onClose: () => void;
  fillData?: Record<string, unknown> | null;
}

export default function WorldRuleFormModal({
  rule,
  saving,
  categoryLabels,
  onSave,
  onClose,
  fillData,
}: WorldRuleFormModalProps) {
  const formId = useId();
  const nameId = useId();
  const nameHelpId = useId();
  const categoryId = useId();
  const descriptionId = useId();
  const constraintsId = useId();
  const constraintsHelpId = useId();
  const priorityHelpId = useId();
  const nameInputRef = useRef<HTMLInputElement>(null);
  const closeTimerRef = useRef<number | null>(null);

  const [name, setName] = useState(rule?.name || "");
  const [category, setCategory] = useState(rule?.category || "general");
  const [description, setDescription] = useState(rule?.description || "");
  const [priority, setPriority] = useState<RulePriority>(rule?.priority || "normal");
  const [locked, setLocked] = useState(rule?.locked || false);
  const [constraintsText, setConstraintsText] = useState(rule?.constraints?.join("\n") || "");
  const [nameTouched, setNameTouched] = useState(false);
  const [submitState, setSubmitState] = useState<"idle" | "error" | "success">("idle");
  const [submitError, setSubmitError] = useState("");

  useEffect(() => {
    return () => {
      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!fillData || Object.keys(fillData).length === 0) return;

    setName((fillData.name as string) || "");
    setDescription((fillData.description as string) || "");
    setConstraintsText(
      Array.isArray(fillData.constraints)
        ? (fillData.constraints as string[]).join("\n")
        : "",
    );
    setCategory((fillData.category as string) || "magic");
    setPriority(
      ["critical", "high", "normal", "low"].includes(fillData.priority as string)
        ? (fillData.priority as RulePriority)
        : "normal",
    );
    setLocked(Boolean(fillData.locked));
  }, [fillData]);

  const constraints = useMemo(
    () =>
      constraintsText
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean),
    [constraintsText],
  );

  const trimmedName = name.trim();
  const nameInvalid = nameTouched && !trimmedName;
  const isComplete = Boolean(trimmedName);
  const interactionDisabled = saving || submitState === "success";
  const activePriority = PRIORITY_OPTIONS.find((item) => item.value === priority)!;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setNameTouched(true);
    setSubmitError("");
    setSubmitState("idle");
    if (!trimmedName || interactionDisabled) return;

    try {
      await onSave({
        name: trimmedName,
        category,
        description: description.trim(),
        priority,
        locked,
        constraints,
      });
      setSubmitState("success");
      closeTimerRef.current = window.setTimeout(onClose, 650);
    } catch {
      setSubmitState("error");
      setSubmitError("保存失败，请检查连接后重试。你填写的内容仍保留在这里。");
    }
  };

  const footerStatus = (() => {
    if (submitState === "success") {
      return (
        <span className="flex items-center gap-2 text-[var(--color-ink-strong)]">
          <Check className="h-4 w-4 text-[var(--color-accent)]" aria-hidden="true" />
          {rule ? "规则已更新" : "规则已创建"}
        </span>
      );
    }
    if (submitState === "error") {
      return (
        <span className="flex items-start gap-2 text-[var(--color-ink-strong)]">
          <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-error)]" aria-hidden="true" />
          {submitError}
        </span>
      );
    }
    if (!isComplete) {
      return <span>填写规则名称后即可{rule ? "保存" : "创建"}</span>;
    }
    return <span>将按“{activePriority.label}”执行，包含 {constraints.length} 条约束</span>;
  })();

  return (
    <Modal
      title={rule ? "编辑世界规则" : "新增世界规则"}
      description="把这条规则写成可判断的创作依据；后续生成与一致性检查都会参考它。"
      onClose={onClose}
      initialFocusRef={nameInputRef}
      closeDisabled={interactionDisabled}
      maxWidth="max-w-3xl"
      bodyMaxHeight="max-h-[calc(100dvh_-_16rem)] sm:max-h-[68dvh]"
      footer={
        <div className="flex w-full min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div
            className="min-w-0 text-xs leading-5 text-[var(--color-ink)]"
            role="status"
            aria-live="polite"
          >
            {footerStatus}
          </div>
          <div className="grid shrink-0 grid-cols-2 gap-2 sm:flex">
            <button
              type="button"
              onClick={onClose}
              disabled={interactionDisabled}
              className="h-11 whitespace-nowrap rounded-[var(--radius-md)] border border-transparent px-4 text-sm font-medium text-[var(--color-ink)] transition-[background-color,border-color,color] duration-150 hover:border-[var(--border-subtle)] hover:bg-[var(--surface-raised)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)] active:bg-[var(--color-accent-soft)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              取消
            </button>
            <button
              type="submit"
              form={formId}
              disabled={!isComplete || interactionDisabled}
              className="inline-flex h-11 items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-md)] bg-[var(--color-accent)] px-5 text-sm font-semibold text-[var(--color-on-accent)] transition-[background-color,opacity] duration-150 hover:bg-[var(--color-accent-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)] active:opacity-90 disabled:cursor-not-allowed disabled:opacity-45"
            >
              {saving ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                  保存中
                </>
              ) : submitState === "success" ? (
                <>
                  <Check className="h-4 w-4" aria-hidden="true" />
                  已保存
                </>
              ) : rule ? (
                "保存修改"
              ) : (
                "创建规则"
              )}
            </button>
          </div>
        </div>
      }
    >
      <form id={formId} onSubmit={handleSubmit} className="space-y-7" noValidate>
        <section aria-labelledby={`${formId}-definition`}>
          <div className="mb-4 min-w-0">
            <h3 id={`${formId}-definition`} className="text-sm font-semibold text-[var(--color-ink-strong)]">
              定义规则
            </h3>
            <p className="mt-0.5 text-xs leading-5 text-[var(--color-ink-muted)]">
              名称用于检索，描述用于说明它在世界中如何成立。
            </p>
          </div>

          <div className="grid min-w-0 gap-4 sm:grid-cols-[minmax(0,1fr)_12rem]">
            <div className="min-w-0">
              <label htmlFor={nameId} className="mb-1.5 block text-sm font-medium text-[var(--color-ink-strong)]">
                规则名称 <span className="text-[var(--color-error)]" aria-hidden="true">*</span>
              </label>
              <input
                ref={nameInputRef}
                id={nameId}
                type="text"
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                  if (submitState === "error") setSubmitState("idle");
                }}
                onBlur={() => setNameTouched(true)}
                placeholder="例如：魔力守恒定律"
                maxLength={80}
                required
                disabled={interactionDisabled}
                aria-invalid={nameInvalid}
                aria-describedby={nameHelpId}
                className={clsx("h-11", fieldClassName, nameInvalid && "border-[var(--color-error)]")}
              />
              <p
                id={nameHelpId}
                className={clsx(
                  "mt-1.5 text-xs leading-5",
                  nameInvalid ? "font-medium text-[var(--color-ink-strong)]" : "text-[var(--color-ink-muted)]",
                )}
              >
                {nameInvalid ? "请填写一个能准确识别这条规则的名称。" : "建议使用“对象 + 规律”，如“灵力衰减规律”。"}
              </p>
            </div>

            <div className="min-w-0">
              <label htmlFor={categoryId} className="mb-1.5 block text-sm font-medium text-[var(--color-ink-strong)]">
                归属分类
              </label>
              <select
                id={categoryId}
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                disabled={interactionDisabled}
                className={clsx("h-11", fieldClassName)}
              >
                {Object.entries(categoryLabels).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
              <p className="mt-1.5 text-xs leading-5 text-[var(--color-ink-muted)]">
                决定规则在世界观中的整理位置。
              </p>
            </div>
          </div>

          <div className="mt-4">
            <div className="mb-1.5 flex items-baseline justify-between gap-3">
              <label htmlFor={descriptionId} className="text-sm font-medium text-[var(--color-ink-strong)]">
                核心描述
              </label>
              <span className="whitespace-nowrap text-xs text-[var(--color-ink-muted)]">可选</span>
            </div>
            <textarea
              id={descriptionId}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="说明规则的适用范围、触发方式，以及它为什么成立。"
              rows={3}
              maxLength={600}
              disabled={interactionDisabled}
              className={clsx("min-h-24 resize-y py-2.5 leading-6", fieldClassName)}
            />
          </div>
        </section>

        <section className="border-t border-[var(--border-subtle)] pt-6" aria-labelledby={`${formId}-constraints`}>
          <div className="mb-4 min-w-0">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 id={`${formId}-constraints`} className="text-sm font-semibold text-[var(--color-ink-strong)]">
                拆成可检查的约束
              </h3>
              <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-[var(--color-ink-muted)]">
                <ListChecks className="h-3.5 w-3.5" aria-hidden="true" />
                已识别 {constraints.length} 条
              </span>
            </div>
            <p id={constraintsHelpId} className="mt-0.5 text-xs leading-5 text-[var(--color-ink-muted)]">
              每行写一条能判断“是否违反”的条件，空行会自动忽略。
            </p>
          </div>
          <label htmlFor={constraintsId} className="sr-only">
            约束条件，每行一条
          </label>
          <textarea
            id={constraintsId}
            value={constraintsText}
            onChange={(event) => setConstraintsText(event.target.value)}
            placeholder={"魔力不能凭空产生\n每次施法消耗等价生命力\n禁术必须付出不可逆代价"}
            rows={4}
            disabled={interactionDisabled}
            aria-describedby={constraintsHelpId}
            className={clsx("min-h-28 resize-y py-2.5 font-mono leading-6", fieldClassName)}
          />
        </section>

        <section className="border-t border-[var(--border-subtle)] pt-6" aria-labelledby={`${formId}-enforcement`}>
          <div className="mb-4 min-w-0">
            <h3 id={`${formId}-enforcement`} className="text-sm font-semibold text-[var(--color-ink-strong)]">
              设置执行力度
            </h3>
            <p className="mt-0.5 text-xs leading-5 text-[var(--color-ink-muted)]">
              优先级决定冲突时如何处理；锁定则禁止系统自动改写。
            </p>
          </div>

          <fieldset disabled={interactionDisabled} aria-describedby={priorityHelpId}>
            <legend className="mb-2 text-sm font-medium text-[var(--color-ink-strong)]">优先级</legend>
            <p id={priorityHelpId} className="sr-only">
              选择这条规则在内容冲突时的执行级别
            </p>
            <div className="grid min-w-0 gap-2 sm:grid-cols-2">
              {PRIORITY_OPTIONS.map((option) => {
                const selected = priority === option.value;
                return (
                  <label
                    key={option.value}
                    className={clsx(
                      "group relative flex min-h-[4.5rem] cursor-pointer items-start gap-3 rounded-[var(--radius-md)] border px-3 py-3 transition-[background-color,border-color] duration-150 active:bg-[var(--color-accent-soft)]",
                      selected
                        ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)]"
                        : "border-[var(--border-subtle)] bg-[var(--surface-paper)] hover:border-[var(--border-emphasis)] hover:bg-[var(--surface-raised)]",
                      interactionDisabled && "cursor-not-allowed opacity-60",
                    )}
                  >
                    <input
                      type="radio"
                      name={`${formId}-priority`}
                      value={option.value}
                      checked={selected}
                      onChange={() => setPriority(option.value)}
                      className="peer sr-only"
                    />
                    <span
                      aria-hidden="true"
                      className={clsx(
                        "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-[background-color,border-color] duration-150 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--color-accent)]",
                        selected
                          ? "border-[var(--color-accent)] bg-[var(--color-accent)] text-[var(--color-on-accent)]"
                          : "border-[var(--border-emphasis)] bg-[var(--surface-raised)]",
                      )}
                    >
                      {selected && <Check className="h-3 w-3" />}
                    </span>
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold text-[var(--color-ink-strong)]">{option.label}</span>
                      <span className="mt-0.5 block text-xs leading-5 text-[var(--color-ink-muted)]">
                        {option.description}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <label
            className={clsx(
              "mt-4 flex cursor-pointer items-center gap-4 rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--surface-paper)] px-3 py-3 transition-[background-color,border-color] duration-150 hover:border-[var(--border-emphasis)] hover:bg-[var(--surface-raised)] active:bg-[var(--color-accent-soft)]",
              interactionDisabled && "cursor-not-allowed opacity-60",
            )}
          >
            <input
              type="checkbox"
              checked={locked}
              onChange={(event) => setLocked(event.target.checked)}
              disabled={interactionDisabled}
              className="peer sr-only"
            />
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-md)] bg-[var(--color-accent-soft)] text-[var(--color-accent)] peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--color-accent)]">
              <LockKeyhole className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold text-[var(--color-ink-strong)]">锁定这条规则</span>
              <span className="mt-0.5 block text-xs leading-5 text-[var(--color-ink-muted)]">
                锁定后，系统只会提示冲突，不会自动修改规则内容。
              </span>
            </span>
            <span
              aria-hidden="true"
              className={clsx(
                "relative h-6 w-11 shrink-0 rounded-full border transition-[background-color,border-color] duration-150",
                locked
                  ? "border-[var(--color-accent)] bg-[var(--color-accent)]"
                  : "border-[var(--border-emphasis)] bg-[var(--surface-tool)]",
              )}
            >
              <span
                className={clsx(
                  "absolute top-0.5 h-[18px] w-[18px] rounded-full bg-[var(--surface-raised)] shadow-sm transition-transform duration-150",
                  locked ? "translate-x-[21px]" : "translate-x-0.5",
                )}
              />
            </span>
          </label>
        </section>
      </form>
    </Modal>
  );
}
