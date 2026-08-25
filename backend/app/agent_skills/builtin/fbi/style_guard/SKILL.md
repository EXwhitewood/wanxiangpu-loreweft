---
name: style-guard
display_name: 风格守卫
description: >-
  守护已确认的项目文风、角色声线、叙事距离、节奏纹理和禁用风格边界，
  防止 FBI、Writer retry、style polish 或 prose repair 在修复局部问题时造成
  style drift、voice drift 或拼接痕迹。This skill defines style guardrails
  and drift attribution; it does not discover issues, rewrite prose, or decide commits.
version: "2.0.0"
format_version: open_skill_v1
license: internal
tags:
  - fbi
  - style
  - voice
  - governance
  - consistency
x-loreweft:
  id: style_guard
  display_name: 风格守卫
  format_version: open_skill_v2
  source_format: x-loreweft
  kind: prompt
  domain: fbi
  category: governance
  agents:
    - fbi_style_guard
    - fbi_prose_repair
    - fbi_voice_repair
    - fbi_pacing_repair
    - fbi_scene_restructure
  priority: 90
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
      - writing
      - quality
      - editorial

  activation:
    enabled_by_default: true
    mount_policy: required_when_matched
    enforcement: enforced
    hooks:
      - before_revision
      - after_revision
      - before_commit
    applies_to:
      - style_repair
      - prose_repair
      - voice_repair
      - pacing_repair
      - scene_restructure
      - writer_retry_candidate

  behaviors:
    - id: style_guard_guidance
      type: prompt
      hook: before_revision
      source: body.Agent Method
    - id: style_drift_audit_contract
      type: workflow
      hook: after_revision
      binding: style_drift_audit

  constraints:
    style_guard:
      style_baseline_required: true
      preserve_character_voice: true
      preserve_pov_distance: true
      preserve_narrative_register: true
      max_unexplained_style_drift: 0
      max_patch_seam_count: 0
      forbidden_style_import: true
      retry_policy:
        max_retries: 1
        action: route_to_voice_or_prose_repair

  quality_contract:
    judged_by:
      - agent
      - validator
      - quality_gate
      - workflow_audit
      - protection_audit
    attribution:
      skill_scope: true
      finding_codes:
        - style.voice_drift
        - style.register_drift
        - style.pov_distance_drift
        - style.rhythm_drift
        - style.patch_seam_visible
        - style.forbidden_pattern_introduced
        - style.baseline_missing
        - style.over_polished
    severity_reference:
      low: "轻微风格偏差，可记录为建议"
      medium: "局部风格不稳，建议修复或人工复核"
      high: "明显破坏角色声线或项目文体，需要处置"
      critical: "修复候选引入禁用风格、破坏 POV 或破坏保护合同，阻断提交"

  failure_policy:
    style.voice_drift:
      strategy: fbi_repair
      repair_lane: fbi_voice
      max_attempts: 1
      on_exhausted: human_review
    style.register_drift:
      strategy: fbi_repair
      repair_lane: fbi_prose
      max_attempts: 1
      on_exhausted: human_review
    style.pov_distance_drift:
      strategy: fbi_repair
      repair_lane: fbi_voice
      max_attempts: 1
      on_exhausted: block_commit
    style.patch_seam_visible:
      strategy: fbi_repair
      repair_lane: fbi_prose
      max_attempts: 1
      on_exhausted: human_review
    style.forbidden_pattern_introduced:
      strategy: block_commit
      on_exhausted: human_review
    style.baseline_missing:
      strategy: human_review
      on_exhausted: block_commit

  protection_policy:
    preserve:
      - style_baseline
      - character_voice
      - pov_distance
      - narrative_register
      - sentence_rhythm_profile
      - forbidden_style_patterns
      - protected_spans
      - plot_facts
      - scene_markers
    reject_if:
      - forbidden_style_pattern_introduced
      - character_voice_rewritten_without_finding
      - pov_boundary_broken
      - protected_span_modified
      - new_blocking_violation

  trace_policy:
    required_events:
      - activation
      - mount
      - style_baseline_snapshot
      - revision_scope
      - drift_check
      - finding_attribution
      - failure_policy_resolution
      - protection_result
      - commit_decision
    expose_to_frontend: true

  migration:
    from_format: legacy_agent_skill
    adapter_version: style_guard_v2
    normalized_fields:
      - rules -> constraints/quality_contract/protection_policy
      - kind -> behaviors
    dropped_fields: []
    degraded_features: []
---

# 风格守卫

## Purpose

本 Skill 定义修复链路中的风格保护合同，防止局部修复把作品改成另一个作者、另一个角色、另一个叙事距离或另一种商业语体。

`style_guard` 是治理类 Skill：

```text
它不发现问题；
它不直接改写正文；
它不裁决提交；
它定义风格基线、允许漂移、禁用风格、归因范围、默认处置建议和 trace 证据。
```

问题由 Agent、Validator、QualityGate、Workflow Audit 或 Protection Audit 发现；Runtime 可以把 style drift finding 归因到本 Skill；FBI 或确定性通道产生候选修订；Commit Gate 决定候选是否正式生效。

## Use When

当候选修改可能影响项目文风或角色声线时启用：

- prose repair、voice repair、pacing repair 或 style polish 修改正文。
- Writer retry 产出新候选，需要与已有风格基线比对。
- scene restructure 移动、合并或重写局部段落。
- 修复结果出现“补丁痕迹”、语体突变、角色说话不像本人、叙事距离忽远忽近。
- 项目已有 style profile、writing mode profile、quality memory、角色声线或冻结风格片段。

## Do Not Use When

- 用户明确要求完全改换文风，且 Workflow 已记录风格迁移许可。
- 任务只做只读分析，不产生候选修改。
- 当前文本没有风格基线，也没有进入正式提交链路。此时应先标记 `style.baseline_missing`，而不是伪造风格基线。

## Agent Method

Agent 或修复器处理候选修改时，应遵循：

1. 先读取当前可用风格基线：项目 style profile、writing mode、章节上下文、角色 voice card、质量记忆和禁用风格。
2. 明确本次修复目标：是事实修复、AI 味修复、节奏修复、声线修复，还是结构修复。
3. 只允许与修复目标相关的风格变化；无关风格变化必须视为 drift。
4. 保留角色声线：称谓、句长、口癖、回避方式、职业词汇、关系距离不得无来源漂移。
5. 保留叙事距离：不能把近距 POV 改成全知旁白，也不能把冷静叙述改成宣传腔。
6. 检查拼接痕迹：修改段落与前后文在节奏、词汇、语气和信息密度上必须自然衔接。
7. 把风格基线快照、候选漂移、归因 finding 和处置建议写入 trace，不写入正文。

风格守卫不要求文本永远不变。它要求每个风格变化有任务理由、有授权、有边界、有重验。

## Product Requirements

候选修订应能形成内部风格审计证据：

```json
{
  "skill_id": "style_guard",
  "style_baseline": {
    "voice_id": "pov_character_voice",
    "register": "restrained_commercial_fiction",
    "pov_distance": "close_third",
    "sentence_rhythm": "mixed_short_medium"
  },
  "revision_scope": {
    "finding_id": "finding_001",
    "repair_lane": "fbi_prose",
    "target": "remove_ai_flavor"
  },
  "drift_check": {
    "voice_drift": false,
    "register_drift": false,
    "pov_distance_drift": false,
    "patch_seam_visible": false,
    "forbidden_pattern_introduced": false
  },
  "decision": "allow_candidate | route_to_voice_repair | reject_candidate"
}
```

正文不得包含风格审计说明、评分、trace 或“已保持原风格”等元话语。

## Style Baseline Model

风格基线不是单一“好不好听”，而是多个可审计维度：

| 维度 | 示例 |
|---|---|
| `narrative_register` | 克制、冷峻、轻喜、古典、商业网文、纪实 |
| `pov_distance` | 近距第三人称、远距旁白、第一人称、有限全知 |
| `character_voice` | 称谓、句长、词汇、口癖、沉默方式、避让方式 |
| `sentence_rhythm` | 短句密度、长句位置、断句习惯、段落长度 |
| `sensory_bias` | 偏视觉、听觉、触觉、动作、物件 |
| `forbidden_patterns` | 项目禁用词、AI 味词、违背题材的语体 |
| `style_locked_lines` | 已冻结关键台词、项目标志性表达 |

风格基线来源优先级：

```text
用户显式风格要求
style_profile / writing_mode_profile
角色 voice card
已提交章节的稳定模式
quality_memory 中的风格约束
当前段落前后文
```

## Constraints

结构化约束：

```yaml
style_guard:
  style_baseline_required: true
  preserve_character_voice: true
  preserve_pov_distance: true
  preserve_narrative_register: true
  max_unexplained_style_drift: 0
  max_patch_seam_count: 0
  forbidden_style_import: true
```

解释：

- `style_baseline_required`: 正式修复链路必须有风格基线或显式 degraded。
- `preserve_character_voice`: 修复不得无来源改写角色声线。
- `preserve_pov_distance`: 修复不得改变叙事距离。
- `preserve_narrative_register`: 修复不得把项目语体改成另一类文本。
- `max_unexplained_style_drift: 0`: 不允许无 finding、无授权的风格漂移。
- `max_patch_seam_count: 0`: 不允许可见拼接痕迹。
- `forbidden_style_import: true`: 不允许引入项目禁用风格或外部作者标志性表达。

## Quality Contract

可归因到本 Skill 的 finding codes：

```text
style.voice_drift
style.register_drift
style.pov_distance_drift
style.rhythm_drift
style.patch_seam_visible
style.forbidden_pattern_introduced
style.baseline_missing
style.over_polished
```

严重度参考：

| severity | 参考含义 |
|---|---|
| `low` | 轻微风格偏差，可作为建议记录 |
| `medium` | 局部风格不稳，需要修复或人工复核 |
| `high` | 明显破坏角色声线、叙事距离或项目语体 |
| `critical` | 引入禁用风格、破坏 POV/保护合同或导致提交安全风险 |

## Failure Policy

本 Skill 的默认处置建议：

| finding | 默认策略 | 说明 |
|---|---|---|
| `style.voice_drift` | `fbi_repair / fbi_voice` | 交给 voice repair 以角色声线为中心修复 |
| `style.register_drift` | `fbi_repair / fbi_prose` | 交给 prose repair 调整语体 |
| `style.pov_distance_drift` | `fbi_repair / fbi_voice`，耗尽后 `block_commit` | POV 漂移风险高 |
| `style.patch_seam_visible` | `fbi_repair / fbi_prose` | 修补拼接痕迹 |
| `style.forbidden_pattern_introduced` | `block_commit` | 禁用风格进入候选，拒绝提交 |
| `style.baseline_missing` | `human_review` | 无法判断时不伪造结论 |

Workflow/Runtime 可以在用户明确要求改风格、全局风格迁移或重写任务中覆盖默认策略，但必须记录 `override_reason`。

## Protection Requirements

风格守卫必须保护：

```text
style_baseline；
character_voice；
pov_distance；
narrative_register；
sentence_rhythm_profile；
forbidden_style_patterns；
protected_spans；
plot_facts；
scene_markers。
```

候选必须拒绝或转人工的情况：

```text
引入项目禁用风格；
把角色声线改成通用旁白；
把近距 POV 改成全知解释；
为了润色改写 protected span；
风格修复引入新事实、新情绪或新剧情含义；
风格基线缺失却声称通过风格审计。
```

## Examples

### Example 1: Voice Drift

Baseline:

```text
角色说话短、硬、少解释。
```

Bad candidate:

```text
“我之所以这样做，是因为我内心深处始终无法放下过去的阴影。”
```

Decision:

```text
route_to_voice_repair
reason: style.voice_drift
```

Why:

候选把角色声线改成解释型旁白。

### Example 2: Register Drift

Baseline:

```text
克制现实感，少宏大抽象。
```

Bad candidate:

```text
这一刻，命运的齿轮终于在黑暗中发出轰鸣。
```

Decision:

```text
reject_candidate
reason: style.register_drift + style.forbidden_pattern_introduced
```

### Example 3: Allowed Style Change

Finding:

```text
ai_flavor.over_polished
```

Candidate:

```text
只把模板化承接词删掉，保留原句视角、动作和语气。
```

Decision:

```text
allow_candidate
reason: style change is finding-bound and within baseline
```

## Anti-Examples

Bad:

```text
为了让文本更高级，可以统一改成华丽文风。
```

Problem:

“高级”不是项目风格基线，且会造成 register drift。

Bad:

```text
风格守卫发现声线漂移后自动重写整段。
```

Problem:

Skill 不发现问题，也不直接修正文。它只能提供合同和默认处置建议。

Bad:

```text
没有 style_profile 时，按通用好文笔处理。
```

Problem:

缺少基线时应 degraded/human_review，不能伪造风格标准。

## Trace

Runtime 应记录：

```json
{
  "skill_id": "style_guard",
  "status": "passed | drift_detected | routed | rejected | degraded",
  "style_baseline": {
    "source": ["style_profile", "pov_character_card", "previous_chapter"],
    "pov_distance": "close_third",
    "register": "restrained_commercial_fiction"
  },
  "revision_scope": {
    "finding_id": "finding_001",
    "repair_lane": "fbi_prose"
  },
  "drift_check": {
    "voice_drift": false,
    "register_drift": false,
    "pov_distance_drift": false,
    "patch_seam_visible": false
  },
  "decision": {
    "declared_strategy": "fbi_repair",
    "effective_strategy": "fbi_repair",
    "commit_allowed": true
  }
}
```

Trace 不得写入正文，但应在 repair trace、preview 和 Commit Gate 审计中可见。

## References

本 Skill 借鉴了以下公开机制，并按万象谱小说生产链路重新建模：

- [Vale prose linter](https://vale.sh/)：将写作风格规则显式化、可配置化；这启发了 forbidden patterns、style baseline 和可审计风格规则。
- [Glean writing style guide](https://developers.glean.com/docs/internal/voice_and_tone/)：强调 voice、tone、clarity、audience fit；这启发了 narrative register 与角色声线分层。
- [IBM Style Quality API](https://github.com/IBM/style-quality-api)：用可重复的语言质量检查衡量风格与表达问题；这启发了 drift finding 与结构化指标。
- [FineEdit 精准文本编辑研究](https://arxiv.org/html/2502.13358v1)：编辑应聚焦具体位置和具体修改内容；这启发了 style drift 只围绕 revision scope 判断。
- [A Survey on Text Style Transfer](https://aclanthology.org/2022.lrec-1.66/)：风格迁移需要平衡目标风格、内容保留和流畅性；这启发了 style change 必须同时保护事实、POV 和内容含义。
- [OpenReview Long Text Style Transfer](https://openreview.net/forum?id=e1XQm9TmgP)：长文本风格转换要保持整体一致性；这启发了章节级风格基线和 patch seam 检查。
