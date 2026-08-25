---
name: patch-minimality
display_name: 补丁最小化
description: >-
  控制 FBI、确定性修复、Writer retry candidate 和补丁仲裁的修改范围，
  要求每个候选修改都有明确 finding 来源、edit scope、变更预算、回滚边界和
  post-change verification。This skill defines minimal patch policy; it does
  not discover issues, rewrite prose, or decide commits.
version: "2.0.0"
format_version: open_skill_v1
license: internal
tags:
  - fbi
  - repair
  - patch
  - governance
  - minimal-change
x-loreweft:
  id: patch_minimality
  display_name: 补丁最小化
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
  priority: 96
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
      - guidance
      - evaluation

  domain_scope:
    primary: fbi
    secondary:
      - quality
      - editorial
      - generation

  activation:
    enabled_by_default: true
    mount_policy: system_locked
    enforcement: enforced
    hooks:
      - before_revision
      - after_revision
      - before_commit
    applies_to:
      - fbi_repair
      - deterministic_fix
      - style_repair
      - writer_retry_candidate
      - patch_arbitration

  behaviors:
    - id: minimal_patch_guidance
      type: prompt
      hook: before_revision
      source: body.Agent Method
    - id: patch_scope_audit_contract
      type: workflow
      hook: after_revision
      binding: patch_scope_audit

  constraints:
    patch_minimality:
      finding_required: true
      edit_scope_required: true
      max_unattributed_edit_ratio: 0.0
      max_adjacent_context_expansion_chars: 120
      default_changed_chars_ratio_max: 0.2
      default_changed_paragraphs_max: 2
      chain_repair_allowed: false
      multi_issue_patch_allowed: false
      independent_rollback_required: true
      retry_policy:
        max_retries: 1
        action: shrink_patch_or_split

  quality_contract:
    judged_by:
      - agent
      - workflow_audit
      - quality_gate
      - protection_audit
    attribution:
      skill_scope: true
      finding_codes:
        - patch.scope_too_large
        - patch.unattributed_edit
        - patch.multi_issue_mixed
        - patch.chain_repair
        - patch.not_independently_reversible
        - patch.context_expansion_excessive
        - patch.rewrites_unrelated_text
    severity_reference:
      low: "补丁说明不完整，但候选范围仍可人工理解"
      medium: "补丁超过默认预算，需要拆分或补充理由"
      high: "候选修改影响无关文本，必须缩小或重做"
      critical: "补丁无法回滚、混合多个问题或破坏保护边界，阻断提交"

  failure_policy:
    patch.scope_too_large:
      strategy: deterministic_fix
      action: shrink_patch_or_split
      max_attempts: 1
      on_exhausted: human_review
    patch.unattributed_edit:
      strategy: block_commit
      on_exhausted: human_review
    patch.multi_issue_mixed:
      strategy: deterministic_fix
      action: split_patch_by_finding
      max_attempts: 1
      on_exhausted: human_review
    patch.chain_repair:
      strategy: human_review
      on_exhausted: block_commit
    patch.not_independently_reversible:
      strategy: block_commit
      on_exhausted: human_review

  protection_policy:
    preserve:
      - protected_spans
      - scene_markers
      - plot_facts
      - pov_boundary
      - character_voice
      - unrelated_text
    reject_if:
      - candidate_lacks_finding
      - candidate_lacks_edit_scope
      - unrelated_text_rewritten
      - independent_rollback_missing
      - new_blocking_violation
      - protected_span_overlap_without_authorization

  trace_policy:
    required_events:
      - activation
      - mount
      - finding_link
      - edit_scope_declaration
      - patch_budget_check
      - changed_span_summary
      - rollback_unit
      - failure_policy_resolution
      - protection_result
      - commit_decision
    expose_to_frontend: true

  migration:
    from_format: legacy_agent_skill
    adapter_version: patch_minimality_v2
    normalized_fields:
      - rules -> constraints/quality_contract/failure_policy
      - kind -> behaviors
    dropped_fields: []
    degraded_features: []
---

# 补丁最小化

## Purpose

本 Skill 定义 FBI 和确定性修复的最小补丁合同：每个候选修改必须只解决一个明确 finding，必须声明 edit scope，必须能独立回滚，且不得顺手重写无关文本。

`patch_minimality` 是治理类 Skill：

```text
它不发现问题；
它不修复正文；
它不裁决提交；
它定义补丁边界、变更预算、拆分策略、回滚要求和 trace 证据。
```

问题由 Agent、Workflow Audit、QualityGate 或 Protection Audit 发现；Runtime 可以把补丁范围问题归因到本 Skill；FBI 或确定性通道负责产生候选；Commit Gate 决定候选是否正式生效。

## Use When

当系统准备修改已有正文或结构化候选时启用：

- FBI fact/prose/pacing/voice/style/scene repair 生成局部补丁。
- deterministic fix 尝试恢复 marker、格式、长度、重复片段或结构字段。
- Writer retry 产出候选文本，需要和原文本或合同义务比对。
- patch arbitration 需要在多个候选补丁之间选择。
- 提交前需要解释“为什么改这里、只改这里、能否回滚”。

## Do Not Use When

- 任务是从零生成全新正文，且没有旧文本需要保护。
- 用户明确要求全文重写，并且 Workflow 已确认旧文本不作为候选基线。
- 当前只做只读审计，不产生任何修改候选。

即使是全文重写，也应由 Workflow 明确记录 `rewrite_allowed`，否则不能把大范围改写伪装成局部补丁。

## Agent Method

Agent 或修复器生成候选补丁时，应按以下顺序工作：

1. 绑定唯一 finding：说明补丁解决哪个问题，不允许一个补丁混合多个无关问题。
2. 声明 `edit_scope`：起止位置、段落编号、原文快照、目标操作和预期结果。
3. 先设计最小可行修改：优先替换命中 span，其次调整句子，最后才扩大到段落。
4. 限制上下文扩张：相邻上下文只用于连接语义，不得成为额外改写范围。
5. 保持回滚友好：候选补丁必须能独立撤回，不影响其它补丁。
6. 修改后检查 changed span 是否超出预算、是否触碰 protected span、是否引入新 blocking violation。
7. 将 finding、edit scope、changed spans、预算结果和回滚单元写入 trace。

不要为了“顺便更好看”修改无关句子。修复收益必须和 finding 一一对应。

## Product Requirements

每个候选补丁应形成内部证据：

```json
{
  "skill_id": "patch_minimality",
  "finding_id": "finding_001",
  "patch_id": "patch_001",
  "edit_scope": {
    "start": 120,
    "end": 184,
    "paragraph_ids": ["p_03"],
    "operation": "replace_span"
  },
  "change_budget": {
    "changed_chars_ratio": 0.08,
    "changed_paragraph_count": 1,
    "context_expansion_chars": 42
  },
  "rollback_unit": {
    "independent": true,
    "original_hash": "..."
  },
  "decision": "allow_candidate | shrink_patch | split_patch | reject_candidate"
}
```

正文不得包含补丁解释、trace、JSON 或“我只做了最小改动”等元话语。

## Patch Scope Model

补丁应使用四层范围模型：

| level | 允许场景 | 风险 |
|---|---|---|
| `span` | 命中词、短语、marker、局部事实错误 | 最安全，默认优先 |
| `sentence` | 单句逻辑、语气、AI 味或事实修正 | 需要确认不改变相邻句义务 |
| `paragraph` | 段内节奏、衔接、局部结构问题 | 需要预算和保护重验 |
| `scene` | 局部补丁无法解决结构断裂 | 不再是普通 minimal patch，必须进入 scene_restructure 或 rewrite 许可 |

规则：

```text
能 span 不 sentence；
能 sentence 不 paragraph；
能 paragraph 不 scene；
需要 scene 级修改时，不能继续声称是最小补丁。
```

## Constraints

结构化约束：

```yaml
patch_minimality:
  finding_required: true
  edit_scope_required: true
  max_unattributed_edit_ratio: 0.0
  max_adjacent_context_expansion_chars: 120
  default_changed_chars_ratio_max: 0.2
  default_changed_paragraphs_max: 2
  chain_repair_allowed: false
  multi_issue_patch_allowed: false
  independent_rollback_required: true
```

解释：

- `finding_required`: 修改必须有明确问题来源。
- `edit_scope_required`: 修改前必须声明作用范围。
- `max_unattributed_edit_ratio: 0.0`: 不允许无来源改动。
- `max_adjacent_context_expansion_chars`: 防止把上下文当成额外改写区域。
- `default_changed_chars_ratio_max`: 默认候选改动比例上限；Workflow 可覆盖但必须留痕。
- `chain_repair_allowed: false`: 不允许修 A 依赖先改 B。
- `multi_issue_patch_allowed: false`: 一个补丁只解决一个问题。
- `independent_rollback_required: true`: 每个补丁必须可独立撤回。

## Quality Contract

可归因到本 Skill 的 finding codes：

```text
patch.scope_too_large
patch.unattributed_edit
patch.multi_issue_mixed
patch.chain_repair
patch.not_independently_reversible
patch.context_expansion_excessive
patch.rewrites_unrelated_text
```

严重度参考：

| severity | 参考含义 |
|---|---|
| `low` | 补丁说明不完整，但范围仍可人工理解 |
| `medium` | 超过默认预算，需要拆分或补充理由 |
| `high` | 修改无关文本，必须缩小或重做 |
| `critical` | 无法回滚、混合多个问题或触碰保护边界，阻断提交 |

## Failure Policy

本 Skill 的默认处置建议：

| finding | 默认策略 | 说明 |
|---|---|---|
| `patch.scope_too_large` | `deterministic_fix: shrink_patch_or_split` | 缩小范围或拆成多个补丁 |
| `patch.unattributed_edit` | `block_commit` | 无来源改动不允许提交 |
| `patch.multi_issue_mixed` | `deterministic_fix: split_patch_by_finding` | 按 finding 拆分 |
| `patch.chain_repair` | `human_review` | 链式修复风险高，需要人工或上游重排 |
| `patch.not_independently_reversible` | `block_commit` | 不可独立回滚的补丁不能提交 |
| `patch.rewrites_unrelated_text` | `block_commit` | 重写无关文本，拒绝候选 |

Workflow/Runtime 可以在明确 rewrite 许可、预算策略或用户授权下覆盖默认策略，但必须写入 `override_reason`。

## Protection Requirements

补丁最小化必须与保护系统共同生效：

```text
不得触碰 protected_spans；
不得删除或改写 scene/chapter marker；
不得改变未命中事实；
不得改变 POV 边界；
不得把风格润色扩大成情节重写；
不得把多个 finding 合并成一个不可拆补丁。
```

如果最小补丁无法解决问题，正确动作不是扩大补丁，而是升级处置路线：

```text
local patch failed -> scene_restructure / writer_retry / human_review / block_commit
```

## Examples

### Example 1: Good Span Patch

Finding:

```text
ai_flavor.excessive_dash at span 152-154
```

Patch:

```text
只替换该处破折号连接，不改写整段。
```

Decision:

```text
allow_candidate
reason: edit_scope matches finding
```

### Example 2: Bad Opportunistic Rewrite

Finding:

```text
一句话存在 AI 味模板词。
```

Bad patch:

```text
重写整段，并顺手调整人物对白、动作和节奏。
```

Decision:

```text
reject_candidate
reason: patch.rewrites_unrelated_text
```

### Example 3: Split Required

Finding set:

```text
fact_regression in paragraph 2
ai_flavor in paragraph 5
```

Bad patch:

```text
一次性改写 paragraph 2-5。
```

Decision:

```text
split_patch_by_finding
```

## Anti-Examples

Bad:

```text
为了让整体更自然，可以把周围几段一起润色。
```

Problem:

这把局部 finding 扩大成无界风格重写。

Bad:

```text
修复 A 问题前，先改 B 处作为铺垫。
```

Problem:

链式修复会让原因、范围和回滚边界失真。

Bad:

```text
多个问题很近，可以合并成一个大补丁。
```

Problem:

相邻不等于同源。补丁必须按 finding 归因。

## Trace

Runtime 应记录：

```json
{
  "skill_id": "patch_minimality",
  "status": "passed | shrink_required | split_required | rejected",
  "finding_id": "finding_001",
  "patch_id": "patch_001",
  "edit_scope": {
    "level": "sentence",
    "start": 120,
    "end": 184
  },
  "budget": {
    "changed_chars_ratio": 0.08,
    "changed_paragraph_count": 1,
    "context_expansion_chars": 42
  },
  "rollback_unit": {
    "independent": true
  },
  "decision": {
    "declared_strategy": "deterministic_fix",
    "effective_strategy": "deterministic_fix",
    "commit_allowed": true
  }
}
```

Trace 不得进入正文，但应在 repair trace、preview 和 Commit Gate 审计中可见。

## References

本 Skill 借鉴了以下公开机制，并按万象谱 FBI 修复链路重新建模：

- [OpenAI Codex prompting guide](https://developers.openai.com/codex/prompting)：强调小而可验证的修改、清晰目标和测试反馈；这启发了 finding-bound patch 与 post-change verification。
- [Aider Unified Diffs](https://aider.chat/docs/more/edit-formats.html)：LLM 编辑可用统一 diff 表达精确范围；这启发了 edit_scope、changed span 和 rollback unit。
- [Amazon Q Developer transformation approach](https://aws.amazon.com/blogs/devops/best-practices-for-amazon-q-developer-transformation-customization/)：建议一次做可逆、范围小的更改；这启发了 independent rollback 与 small reversible change。
- [FineEdit 精准文本编辑研究](https://arxiv.org/html/2502.13358v1)：LLM 编辑应聚焦具体位置和具体修改内容；这启发了 span/sentence/paragraph/scene 四层范围模型。
- [OpenSSF AI code assistant security guidance](https://best.openssf.org/Security-Focused-Guide-for-AI-Code-Assistant-Instructions.html)：强调最小权限和不信任外部输入；这启发了无来源修改阻断和危险扩张留痕。
