/* Hallmark · component: project-create-dialog · genre: editorial · design-system: DESIGN.md
 * states: default · hover · focus · active · disabled · loading · error · success
 * pre-emit critique: P5 H5 E4 S5 R5 V5
 */
import { useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from "react";
import { AlertCircle, ArrowRight, Loader2 } from "lucide-react";
import clsx from "clsx";

import Modal from "@/components/Common/Modal";
import { GlassButton } from "@/components/UI/GlassButton";
import type { GenreProfileId, Project, ProjectCreate } from "@/types";

const FORM_ID = "project-create-form";
const NAME_LIMIT = 80;
const DESCRIPTION_LIMIT = 1000;
const GENRE_LIMIT = 100;
const MIN_TARGET_WORDS = 10_000;
const MAX_TARGET_WORDS = 5_000_000;

const GENRE_PROFILES: Array<{ value: GenreProfileId; label: string }> = [
  { value: "general", label: "通用小说" },
  { value: "xianxia", label: "修仙 / 奇幻" },
  { value: "mystery", label: "悬疑 / 推理" },
  { value: "romance", label: "言情 / 情感" },
  { value: "scifi", label: "科幻" },
  { value: "historical", label: "历史 / 古风" },
];

const WORD_TARGET_PRESETS = [100_000, 300_000, 500_000, 1_000_000];

type FormState = {
  name: string;
  description: string;
  genreProfileId: GenreProfileId;
  genre: string;
  wordCountTarget: string;
};

type FieldName = "name" | "description" | "genre" | "wordCountTarget";
type FieldErrors = Partial<Record<FieldName, string>>;

const INITIAL_FORM: FormState = {
  name: "",
  description: "",
  genreProfileId: "general",
  genre: "",
  wordCountTarget: "100000",
};

const PROFILE_LABELS = Object.fromEntries(
  GENRE_PROFILES.map((profile) => [profile.value, profile.label]),
) as Record<GenreProfileId, string>;

function formatPreset(value: number) {
  return `${value / 10_000}万`;
}

function validateField(field: FieldName, form: FormState): string | undefined {
  if (field === "name") {
    const value = form.name.trim();
    if (!value) return "请输入作品名；暂定名称也可以。";
    if (value.length > NAME_LIMIT) return `作品名不能超过 ${NAME_LIMIT} 个字符。`;
  }
  if (field === "description" && form.description.trim().length > DESCRIPTION_LIMIT) {
    return `故事种子不能超过 ${DESCRIPTION_LIMIT} 个字符。`;
  }
  if (field === "genre" && form.genre.trim().length > GENRE_LIMIT) {
    return `细分题材不能超过 ${GENRE_LIMIT} 个字符。`;
  }
  if (field === "wordCountTarget") {
    const value = Number(form.wordCountTarget);
    if (!form.wordCountTarget.trim() || !Number.isInteger(value)) {
      return "请输入整数目标字数。";
    }
    if (value < MIN_TARGET_WORDS || value > MAX_TARGET_WORDS) {
      return "目标总字数需在 1 万至 500 万之间。";
    }
  }
  return undefined;
}

function validateForm(form: FormState): FieldErrors {
  const fields: FieldName[] = ["name", "description", "genre", "wordCountTarget"];
  return Object.fromEntries(
    fields.flatMap((field) => {
      const message = validateField(field, form);
      return message ? [[field, message]] : [];
    }),
  );
}

function inputClass(hasError: boolean) {
  return clsx(
    "h-11 w-full rounded-[var(--radius-md)] border bg-[var(--surface-paper)] px-3 text-sm text-[var(--color-ink-strong)]",
    "outline outline-2 outline-transparent outline-offset-1 transition-[background-color,border-color] duration-150",
    "placeholder:text-[var(--color-ink-muted)] hover:bg-[var(--surface-raised)]",
    "focus-visible:border-[var(--border-emphasis)] focus-visible:outline-[var(--focus-ring)]",
    "disabled:cursor-not-allowed disabled:opacity-50",
    hasError ? "border-[var(--color-error)]" : "border-[var(--border-subtle)]",
  );
}

function FieldMessage({ id, error, helper }: { id: string; error?: string; helper?: string }) {
  return (
    <div
      id={id}
      className={clsx(
        "mt-1.5 flex min-h-[1.25rem] items-start gap-1.5 text-xs leading-5",
        error ? "text-[var(--color-error)]" : "text-[var(--color-ink-muted)]",
      )}
      aria-live="polite"
    >
      {error && <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
      <span>{error || helper || "\u00a0"}</span>
    </div>
  );
}

interface ProjectCreateDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (data: ProjectCreate) => Promise<Project>;
  onCreated: (project: Project) => void;
}

export default function ProjectCreateDialog({
  isOpen,
  onClose,
  onCreate,
  onCreated,
}: ProjectCreateDialogProps) {
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [touched, setTouched] = useState<Partial<Record<FieldName, boolean>>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const updateField = (field: FieldName, value: string) => {
    const next = { ...form, [field]: value };
    setForm(next);
    setSubmissionError(null);
    if (touched[field]) {
      setErrors((current) => ({ ...current, [field]: validateField(field, next) }));
    }
  };

  const handleBlur = (field: FieldName) => {
    setTouched((current) => ({ ...current, [field]: true }));
    setErrors((current) => ({ ...current, [field]: validateField(field, form) }));
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;

    const nextErrors = validateForm(form);
    setTouched({ name: true, description: true, genre: true, wordCountTarget: true });
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      if (nextErrors.name) nameRef.current?.focus();
      return;
    }

    const genre = form.genre.trim()
      || (form.genreProfileId === "general" ? "" : PROFILE_LABELS[form.genreProfileId]);
    const payload: ProjectCreate = {
      name: form.name.trim(),
      description: form.description.trim(),
      genre,
      genre_profile_id: form.genreProfileId,
      word_count_target: Number(form.wordCountTarget),
    };

    setSubmitting(true);
    setSubmissionError(null);
    try {
      const project = await onCreate(payload);
      setForm(INITIAL_FORM);
      setErrors({});
      setTouched({});
      onClose();
      onCreated(project);
    } catch (error) {
      const detail = error instanceof Error && error.message ? error.message : "请稍后重试。";
      setSubmissionError(`创建失败：${detail}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleStorySeedShortcut = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  };

  return (
    <Modal
      title="创建新作品"
      description="先记录作品起点，主题、人物与冲突将在大纲工作区继续完善。"
      isOpen={isOpen}
      onClose={onClose}
      closeDisabled={submitting}
      initialFocusRef={nameRef}
      maxWidth="max-w-[840px]"
      bodyMaxHeight="max-h-[calc(100dvh-11rem)]"
      footer={
        <>
          <GlassButton type="button" variant="ghost" onClick={onClose} disabled={submitting} className="h-11 px-5">
            取消
          </GlassButton>
          <GlassButton
            type="submit"
            form={FORM_ID}
            variant="primary"
            disabled={submitting}
            aria-busy={submitting}
            className="h-11 min-w-[10rem] px-5"
          >
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                正在创建…
              </>
            ) : (
              <>
                创建并进入大纲
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </>
            )}
          </GlassButton>
        </>
      }
    >
      <form id={FORM_ID} onSubmit={handleSubmit} noValidate aria-busy={submitting}>
        <div className="grid min-w-0 gap-6 md:grid-cols-[minmax(0,1.35fr)_minmax(15rem,0.65fr)]">
          <section className="min-w-0" aria-labelledby="project-origin-heading">
            <h3 id="project-origin-heading" className="text-sm font-semibold text-[var(--color-ink-strong)]">
              作品起点
            </h3>

            <div className="mt-5">
              <label htmlFor="project-name" className="mb-1.5 block text-sm font-medium text-[var(--color-ink)]">
                作品名 <span className="text-xs text-[var(--color-highlight)]">（必填）</span>
              </label>
              <input
                ref={nameRef}
                id="project-name"
                name="name"
                type="text"
                disabled={submitting}
                required
                aria-required="true"
                aria-invalid={Boolean(errors.name)}
                aria-describedby="project-name-message"
                maxLength={NAME_LIMIT}
                value={form.name}
                onChange={(event: ChangeEvent<HTMLInputElement>) => updateField("name", event.target.value)}
                onBlur={() => handleBlur("name")}
                className={inputClass(Boolean(errors.name))}
                placeholder="暂定名称也可以"
                autoComplete="off"
              />
              <FieldMessage id="project-name-message" error={errors.name} helper={`${form.name.length} / ${NAME_LIMIT}`} />
            </div>

            <div className="mt-4">
              <div className="mb-1.5 flex items-center justify-between gap-3">
                <label htmlFor="project-description" className="text-sm font-medium text-[var(--color-ink)]">
                  故事种子
                </label>
                <span className="text-xs tabular-nums text-[var(--color-ink-muted)]">
                  {form.description.length} / {DESCRIPTION_LIMIT}
                </span>
              </div>
              <textarea
                id="project-description"
                name="description"
                rows={7}
                disabled={submitting}
                maxLength={DESCRIPTION_LIMIT}
                aria-invalid={Boolean(errors.description)}
                aria-describedby="project-description-message"
                value={form.description}
                onChange={(event: ChangeEvent<HTMLTextAreaElement>) => updateField("description", event.target.value)}
                onBlur={() => handleBlur("description")}
                onKeyDown={handleStorySeedShortcut}
                className={clsx(inputClass(Boolean(errors.description)), "min-h-36 resize-y py-3 leading-6")}
                placeholder="例如：一名记忆修复师发现，客户遗失的记忆正在改写整座城市。"
              />
              <FieldMessage
                id="project-description-message"
                error={errors.description}
                helper="写下人物、欲望与阻力即可；这只是初始构想，不会自动成为世界观事实。"
              />
            </div>
          </section>

          <section
            className="min-w-0 border-t border-[var(--border-subtle)] pt-6 md:border-l md:border-t-0 md:pl-6 md:pt-0"
            aria-labelledby="project-settings-heading"
          >
            <h3 id="project-settings-heading" className="text-sm font-semibold text-[var(--color-ink-strong)]">
              项目设置
            </h3>

            <div className="mt-5">
              <label htmlFor="project-genre-profile" className="mb-1.5 block text-sm font-medium text-[var(--color-ink)]">
                题材基型
              </label>
              <select
                id="project-genre-profile"
                name="genre_profile_id"
                disabled={submitting}
                aria-describedby="project-genre-profile-message"
                value={form.genreProfileId}
                onChange={(event) => {
                  setForm((current) => ({
                    ...current,
                    genreProfileId: event.target.value as GenreProfileId,
                  }));
                  setSubmissionError(null);
                }}
                className={inputClass(false)}
              >
                {GENRE_PROFILES.map((profile) => (
                  <option key={profile.value} value={profile.value}>
                    {profile.label}
                  </option>
                ))}
              </select>
              <FieldMessage id="project-genre-profile-message" helper="用于选择系统实际执行的题材规则。" />
            </div>

            <div className="mt-4">
              <label htmlFor="project-genre" className="mb-1.5 block text-sm font-medium text-[var(--color-ink)]">
                细分题材
              </label>
              <input
                id="project-genre"
                name="genre"
                type="text"
                disabled={submitting}
                maxLength={GENRE_LIMIT}
                aria-invalid={Boolean(errors.genre)}
                aria-describedby="project-genre-message"
                value={form.genre}
                onChange={(event) => updateField("genre", event.target.value)}
                onBlur={() => handleBlur("genre")}
                className={inputClass(Boolean(errors.genre))}
                placeholder="例如：赛博朋克悬疑"
                autoComplete="off"
              />
              <FieldMessage id="project-genre-message" error={errors.genre} helper="留空时使用题材基型名称。" />
            </div>

            <div className="mt-4">
              <label htmlFor="project-word-target" className="mb-1.5 block text-sm font-medium text-[var(--color-ink)]">
                目标总字数
              </label>
              <input
                id="project-word-target"
                name="word_count_target"
                type="number"
                disabled={submitting}
                inputMode="numeric"
                min={MIN_TARGET_WORDS}
                max={MAX_TARGET_WORDS}
                step={10_000}
                aria-invalid={Boolean(errors.wordCountTarget)}
                aria-describedby="project-word-target-message"
                value={form.wordCountTarget}
                onChange={(event) => updateField("wordCountTarget", event.target.value)}
                onBlur={() => handleBlur("wordCountTarget")}
                className={clsx(inputClass(Boolean(errors.wordCountTarget)), "tabular-nums")}
              />
              <div className="mt-2 flex flex-wrap gap-2" aria-label="常用目标字数">
                {WORD_TARGET_PRESETS.map((preset) => {
                  const selected = Number(form.wordCountTarget) === preset;
                  return (
                    <button
                      key={preset}
                      type="button"
                      disabled={submitting}
                      aria-pressed={selected}
                      onClick={() => updateField("wordCountTarget", String(preset))}
                      className={clsx(
                        "h-9 whitespace-nowrap rounded-[var(--radius-md)] border px-3 text-xs font-medium",
                        "transition-[background-color,border-color,color] duration-150",
                        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--focus-ring)]",
                        "active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50",
                        selected
                          ? "border-[var(--border-emphasis)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                          : "border-[var(--border-subtle)] bg-[var(--surface-paper)] text-[var(--color-ink-muted)] hover:bg-[var(--surface-raised)] hover:text-[var(--color-ink)]",
                      )}
                    >
                      {formatPreset(preset)}
                    </button>
                  );
                })}
              </div>
              <FieldMessage id="project-word-target-message" error={errors.wordCountTarget} helper="用于进度和章节场景字数预算。" />
            </div>
          </section>
        </div>

        <div className="mt-6 border-t border-[var(--border-subtle)] pt-4 text-xs leading-5 text-[var(--color-ink-muted)]">
          创建本身不会调用 AI。进入大纲后，只有你主动发送消息或启动“引导构建”，系统才会调用 AI。
        </div>

        {submissionError && (
          <div
            role="alert"
            className="mt-4 flex items-start gap-2 rounded-[var(--radius-md)] border border-[var(--color-error)] bg-[var(--color-error-soft)] px-3 py-2.5 text-sm text-[var(--color-error)]"
          >
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{submissionError} 已填写内容仍然保留。</span>
          </div>
        )}
      </form>
    </Modal>
  );
}
