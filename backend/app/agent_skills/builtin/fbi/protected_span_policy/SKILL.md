---
name: protected-span-policy
display_name: 保护区间策略
description: >-
  保护小说生产中的不可改写区间、结构标记、事实锚点、授权边界和提交安全。
  Use when FBI、修复器、风格润色、补丁仲裁或提交前审计可能修改正文、结构标记、
  事实状态、伏笔信息或用户指定原文。This skill defines protected ranges and
  edit permissions; it does not discover issues, rewrite prose, or decide commits.
version: "2.0.0"
format_version: open_skill_v1
license: internal
tags:
  - fbi
  - protection
  - governance
  - protected-spans
  - commit-safety
x-loreweft:
  id: protected_span_policy
  display_name: 保护区间策略
  format_version: open_skill_v2
  source_format: x-loreweft
  kind: prompt
  domain: fbi
  category: governance
  agents:
    - fbi_fact_repair
    - fbi_prose_repair
    - fbi_pacing_repair
    - fbi_voice_repair
    - fbi_style_guard
    - fbi_scene_restructure
  priority: 98
  enabled_by_default: true

  compatibility:
    level: 3
    status: native
    portable_export_supported: true
    loreweft_native_supported: true
    degraded_features: []

  ownership:
    domain_owner: fbi
    maintained_by: editorial_system

  capability:
    primary_role: governance
    secondary_roles:
      - evaluation
      - guidance

  domain_scope:
    primary: fbi
    secondary:
      - quality
      - continuity
      - editorial
      - generation

  activation:
    enabled_by_default: true
    mount_policy: system_locked
    enforcement: blocking
    hooks:
      - before_generation
      - before_revision
      - before_commit
      - after_revision
    applies_to:
      - fbi_repair
      - style_repair
      - deterministic_fix
      - writer_retry_candidate
      - commit_gate

  behaviors:
    - id: protected_span_guidance
      type: prompt
      hook: before_revision
      source: body.Agent Method
    - id: protected_span_audit_contract
      type: workflow
      hook: before_commit
      binding: protection_audit

  constraints:
    protected_span_policy:
      absolute_protected_overlap_max: 0
      marker_edit_allowed: false
      fact_regression_allowed: false
      protected_span_text_drift_max: 0
      conditional_protection_requires_authorization: true
      downgrade_requires_explicit_approval: true
      max_unattributed_edit_ratio: 0.0
      retry_policy:
        max_retries: 0
        action: block_commit

  quality_contract:
    judged_by:
      - agent
      - workflow_audit
      - quality_gate
      - protection_audit
    attribution:
      skill_scope: true
      finding_codes:
        - protection.absolute_span_modified
        - protection.conditional_span_unauthorized
        - protection.marker_missing
        - protection.fact_regression
        - protection.pov_boundary_broken
        - protection.do_not_reveal_leaked
        - protection.unattributed_edit
        - protection.protection_level_downgrade
    severity_reference:
      low: "保护元数据缺失但未影响正文候选"
      medium: "条件保护区间需要授权或人工复核"
      high: "候选修改触碰保护边界，必须拒绝或重做"
      critical: "绝对保护区、结构标记、事实或保密信息被破坏，阻断提交"

  failure_policy:
    protection.absolute_span_modified:
      strategy: block_commit
      on_exhausted: human_review
    protection.conditional_span_unauthorized:
      strategy: human_review
      on_exhausted: block_commit
    protection.marker_missing:
      strategy: deterministic_fix
      max_attempts: 1
      on_exhausted: block_commit
    protection.fact_regression:
      strategy: block_commit
      on_exhausted: human_review
    protection.unattributed_edit:
      strategy: block_commit
      on_exhausted: human_review

  protection_policy:
    preserve:
      - protected_spans
      - scene_markers
      - chapter_markers
      - plot_facts
      - pov_boundary
      - character_voice_locked_lines
      - do_not_reveal_yet
      - user_locked_text
      - citations_and_quotes
      - system_contract_tokens
    reject_if:
      - absolute_protected_overlap
      - marker_missing
      - marker_rewritten
      - fact_regression
      - pov_boundary_broken
      - do_not_reveal_leaked
      - protection_level_downgrade_without_approval
      - candidate_lacks_change_attribution

  trace_policy:
    required_events:
      - activation
      - mount
      - protected_span_snapshot
      - edit_scope_declaration
      - overlap_check
      - authorization_check
      - finding_attribution
      - failure_policy_resolution
      - protection_result
      - commit_decision
    expose_to_frontend: true

  migration:
    from_format: legacy_agent_skill
    adapter_version: protected_span_policy_v2
    normalized_fields:
      - kind -> behaviors
      - domain -> domain_scope
      - rules -> constraints/protection_policy/quality_contract
    dropped_fields: []
    degraded_features: []
---

# 保护区间策略

## Purpose

本 Skill 定义小说生产中的不可改写区间、结构标记、事实锚点、授权边界和提交保护规则。它的目标不是提升文笔，而是防止 Writer、FBI、风格润色、确定性修复或提交前处理把作品已经确认的结构和事实改坏。

`protected_span_policy` 是治理类 Skill：

```text
它不发现问题；
它不修复正文；
它不裁决提交；
它定义保护等级、可编辑范围、授权条件、拒绝条件和 trace 证据。
```

问题由 Agent、Workflow Audit、QualityGate 或 Protection Audit 发现；Runtime 可以把相关 finding 归因到本 Skill 合同；候选修改由 FBI 或确定性通道产生；最终是否生效由 Commit Gate 裁决。

## Use When

当任何流程可能修改正文、结构、事实或状态时，本 Skill 必须作为保护合同存在：

- FBI 执行事实修复、文笔修复、节奏修复、角色声音修复或风格守卫。
- Writer retry、style polish、prose repair、deterministic fix 产生新的正文候选。
- 补丁仲裁、长度预算、场景重构、结尾补全可能移动、删除或合并文本。
- 提交前需要确认 `[[SCENE:...]]`、章节 marker、事实、POV、伏笔保密和用户锁定文本没有被破坏。
- 用户明确要求某段话、某个称谓、某句台词、某个诗句、契约文本、碑文或引用必须原样保留。

## Do Not Use When

本 Skill 不应被当成普通文风建议使用：

- 任务只要求给出创作建议，不产生候选修改。
- 文本没有进入正式作品链路，也没有 protected metadata。
- 用户明确要求重新生成全新草稿，且 Workflow 已确认旧文本不需要保护。

即便不直接挂载到当前 Agent，任何正式提交链路仍应继承全局 Protection/Commit Gate 的保护规则。

## Agent Method

Agent 或修复器在处理候选修改时，应遵循以下方法：

1. 先识别候选修改的 `edit_scope`：修改的起止位置、目标问题、修改原因、预期影响。
2. 将 `edit_scope` 与当前 `protected_spans`、scene/chapter marker、事实锚点和保密边界做重叠检查。
3. 如果触碰绝对保护区，停止修改该区域，把 finding 交给 Workflow/Commit Gate。
4. 如果触碰条件保护区，检查是否存在授权条件；无授权时不得修改。
5. 对可编辑区间执行最小补丁，避免顺手重写相邻未命中内容。
6. 修改后重新比较 protected snapshot，确认受保护文本、marker、事实、POV 和信息释放没有漂移。
7. 把检查结果写入 trace，不写入正文。

不要为了修复文风、节奏或 AI 味而改写 protected span。保护边界优先于局部文笔收益。

## Product Requirements

保护区间策略要求每次候选修改至少能形成以下内部证据：

```json
{
  "skill_id": "protected_span_policy",
  "edit_scope": {
    "start": 120,
    "end": 184,
    "reason": "prose_repair",
    "finding_id": "finding_001"
  },
  "protected_span_snapshot": [
    {
      "span_id": "ps_001",
      "start": 80,
      "end": 130,
      "level": "absolute",
      "reason": "user_locked_text",
      "text_hash": "..."
    }
  ],
  "overlap_result": {
    "overlaps_absolute": false,
    "overlaps_conditional": true,
    "authorization": "missing"
  },
  "decision": "reject_candidate | allow_candidate | require_human_review"
}
```

正文不得出现保护审计说明、JSON、trace、系统字段或“本段受保护”之类的元叙述，除非用户明确要求输出审计报告。

## Protected Span Model

受保护区间至少包含：

```yaml
span_id: ps_001
start: 120
end: 184
text_snapshot: "必须保留的原文"
text_hash: "hash-of-text-snapshot"
level: absolute
reason: user_locked_text
owner: user | workflow | commit_gate | fbi | system
allowed_operations:
  - read
authorization_required: true
expires_at: null
source_finding_id: finding_001
created_at: "2026-06-18T00:00:00Z"
```

### Protection Levels

| level | 含义 | 默认处置 |
|---|---|---|
| `absolute` | 不可修改。包括用户锁定原文、结构 marker、已确认关键台词、事实锚点、保密信息 | 任何重叠修改都拒绝候选 |
| `conditional` | 条件保护。可在明确授权、人工审批或上游合同允许时修改 | 无授权时进入 human_review 或 block_commit |
| `soft` | 软保护。允许修改，但必须说明理由并保留事实/声线/结构 | 修改后重验，失败则拒绝 |
| `free` | 普通可编辑区 | 按目标修复策略处理 |

### Protected Content Types

必须优先保护：

- `[[SCENE:...]]`、章节 marker、系统 contract token。
- 用户指定必须原样保留的句子、标题、诗句、引用、契约文本、碑文、密码、咒语、称谓。
- 已提交的世界事实、角色状态、死亡/存活、道具归属、地点关系、时间线。
- POV 边界、角色不知道的信息、`do_not_reveal_yet`、未揭露伏笔。
- 已通过 Commit Gate 的关键台词、关键动作、关键事实锚点。
- 受版权、引用、格式或法务约束不能随意改写的文本。

## Constraints

结构化约束：

```yaml
protected_span_policy:
  absolute_protected_overlap_max: 0
  marker_edit_allowed: false
  fact_regression_allowed: false
  protected_span_text_drift_max: 0
  conditional_protection_requires_authorization: true
  downgrade_requires_explicit_approval: true
  max_unattributed_edit_ratio: 0.0
```

解释：

- `absolute_protected_overlap_max: 0`：候选修改不得覆盖绝对保护区。
- `marker_edit_allowed: false`：不得删除、改写或重排 scene/chapter marker。
- `fact_regression_allowed: false`：不得把已确认事实改回旧状态或错误状态。
- `protected_span_text_drift_max: 0`：绝对保护文本必须字面一致。
- `conditional_protection_requires_authorization: true`：条件保护区修改必须有授权证据。
- `downgrade_requires_explicit_approval: true`：保护等级降低必须由有权限主体批准。
- `max_unattributed_edit_ratio: 0.0`：修改必须能追溯到 finding、repair order 或用户请求。

## Quality Contract

本 Skill 的 finding 来源可以是：

```text
Agent 自检；
Workflow Audit；
QualityGate；
Protection Audit；
Commit Gate 前置检查。
```

可归因到本 Skill 的 finding codes：

```text
protection.absolute_span_modified
protection.conditional_span_unauthorized
protection.marker_missing
protection.marker_rewritten
protection.fact_regression
protection.pov_boundary_broken
protection.do_not_reveal_leaked
protection.unattributed_edit
protection.protection_level_downgrade
```

严重度参考：

| severity | 参考含义 |
|---|---|
| `low` | 保护元数据缺失、不完整或需要补充说明，但候选尚未触碰正文 |
| `medium` | 条件保护区被触碰，需要授权或人工复核 |
| `high` | 候选修改触碰保护边界，必须拒绝或重做 |
| `critical` | 绝对保护区、结构 marker、事实、POV 或保密信息被破坏，阻断提交 |

## Failure Policy

本 Skill 只声明默认处置建议：

| finding | 默认策略 | 说明 |
|---|---|---|
| `protection.absolute_span_modified` | `block_commit` | 绝对保护区被修改，拒绝候选 |
| `protection.conditional_span_unauthorized` | `human_review` | 条件保护区无授权，需人工或上游审批 |
| `protection.marker_missing` | `deterministic_fix` | marker 可尝试一次确定性恢复，失败则阻断 |
| `protection.fact_regression` | `block_commit` | 事实倒退或事实破坏，拒绝候选 |
| `protection.pov_boundary_broken` | `block_commit` | POV 越界可能破坏叙事合同 |
| `protection.do_not_reveal_leaked` | `block_commit` | 未揭露信息泄漏，拒绝候选 |
| `protection.unattributed_edit` | `block_commit` | 修改缺少来源，拒绝候选 |

FBI 或确定性通道可以产生新的候选，但不得绕过 Protection。Commit Gate 可以基于全局策略覆盖默认处置，但必须记录 `override_reason`。

## Protection Requirements

任何修改型处置都必须保留：

```text
protected_spans；
scene_markers；
chapter_markers；
plot_facts；
pov_boundary；
character_voice_locked_lines；
do_not_reveal_yet；
user_locked_text；
citations_and_quotes；
system_contract_tokens。
```

候选必须被拒绝的情况：

```text
触碰 absolute protected span；
删除或改写 marker；
导致事实回退；
越过 POV 边界；
泄漏 do_not_reveal_yet；
降低保护等级但没有授权；
修改无法追溯到 finding、repair order 或用户请求；
为了风格收益改写用户锁定原文。
```

## Authorization Rules

授权遵循最小权限：

| 操作 | 需要权限 |
|---|---|
| 读取 protected span | 普通修复流程可读 |
| 修改 soft span | finding 或 repair order 可授权 |
| 修改 conditional span | Workflow/Commit Gate/用户显式授权 |
| 修改 absolute span | 默认禁止，只能人工解除保护后重新进入流程 |
| 降低保护等级 | Commit Gate 或用户显式授权 |
| 删除保护记录 | Commit Gate 或系统迁移任务授权 |

外部导入的 Skill、script 或 tool 不得自动获得 protected span 修改权。所有授权必须可追踪。

## Examples

### Example 1: Marker Protection

Original:

```text
[[SCENE:scene_12]]
她推开门，屋里没有点灯。
```

Bad candidate:

```text
她推开门，屋里没有点灯。
```

Decision:

```text
reject_candidate
reason: protection.marker_missing
```

Why:

`[[SCENE:scene_12]]` 是结构 marker，不能被文风修复删除。

### Example 2: User Locked Text

Protected:

```text
"别回头。"
```

Bad candidate:

```text
"不要回头。"
```

Decision:

```text
reject_candidate
reason: protection.absolute_span_modified
```

Why:

即使语义接近，用户锁定文本也要求字面一致。

### Example 3: Conditional Span

Protected:

```yaml
level: conditional
reason: foreshadowing_setup
authorization_required: true
```

Candidate 修改了伏笔段落，但没有授权。

Decision:

```text
require_human_review
reason: protection.conditional_span_unauthorized
```

## Anti-Examples

Bad:

```text
为了让句子更自然，FBI 可以轻微调整用户锁定台词。
```

Problem:

这把风格收益放在保护边界之上，违反 absolute protection。

Bad:

```text
如果 marker 影响阅读，可以删除 marker。
```

Problem:

marker 是系统结构合同，不是正文风格元素。

Bad:

```text
没有 protected_spans 时，任何修改都可以直接提交。
```

Problem:

即使没有显式 protected span，事实、POV、do_not_reveal_yet 和 marker 仍受保护。

## Trace

Runtime 应记录：

```json
{
  "skill_id": "protected_span_policy",
  "status": "passed | rejected | requires_review | blocked",
  "activation": {
    "mount_policy": "system_locked",
    "enforcement": "blocking"
  },
  "edit_scope": {
    "start": 120,
    "end": 184,
    "reason": "fbi_prose_repair",
    "finding_id": "finding_001"
  },
  "overlap_check": {
    "absolute_overlap_count": 0,
    "conditional_overlap_count": 1,
    "authorization_status": "missing"
  },
  "decision": {
    "declared_strategy": "human_review",
    "effective_strategy": "human_review",
    "commit_allowed": false
  }
}
```

Trace 不得写入正文，但前端详情、preview、workflow monitor 和审计面板应能展示关键结果。

## References

本 Skill 借鉴了以下公开机制，并按万象谱小说生产链路重新建模：

- [Agent Skills open standard](https://agentskills.io/specification)：Skill 以 `SKILL.md` 为核心，使用 frontmatter、正文说明和可选资源组织能力包。
- [Claude Agent Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) 与 [Claude Code Skills](https://code.claude.com/docs/en/skills)：Skill 正文承载程序性知识，资源和脚本按需使用；这启发了 portable 层和 Loreweft 原生层分离。
- [Microsoft Word Restrict Editing](https://support.microsoft.com/en-us/word/allow-changes-to-parts-of-a-protected-word-document)：文档可整体只读，并给特定区域或特定用户开放编辑权限；这启发了 protected span + authorization 模型。
- [Google Sheets Protected Ranges](https://developers.google.com/apps-script/reference/spreadsheet/protection)：保护区可绑定静态或命名范围，并记录描述、编辑者和 warning-only 状态；这启发了 span_id、reason、owner、level 和 warning/blocked 分级。
- [ONLYOFFICE Protecting Ranges](https://api.onlyoffice.com/docs/docs-api/get-started/how-it-works/protecting-ranges/)：受保护范围编辑需要按用户权限请求可编辑者列表；这补强了授权检查和 trace 记录。
- [FineEdit 精准文本编辑研究](https://arxiv.org/html/2502.13358v1)：LLM 编辑应聚焦精确位置和具体修改内容；这启发了 edit_scope、change attribution 和最小补丁要求。
- [OpenSSF AI code assistant security guidance](https://best.openssf.org/Security-Focused-Guide-for-AI-Code-Assistant-Instructions.html)：外部输入不可信、权限最小化、授权检查和安全默认值；这启发了外部 Skill/script/tool 不自动获得 protected span 修改权。
