---
name: show-dont-tell
description: >-
  将情绪标签、性格声明、抽象解释和对白情绪标注，转换为读者可观察的场景证据：
  压力下的选择、具体动作、对白潜台词、功能性感官细节、物件反应和角色化身体行为。
  它不禁止所有讲述，只禁止关键情绪和关系变化处的无证据结论。
version: "2.2.1"
tags:
  - writing
  - prose
  - emotion
  - evidence
x-loreweft:
  id: show_dont_tell
  display_name: "展示而非讲述"
  format_version: open_skill_v2
  source_format: x-loreweft
  kind: prompt
  domain: writing
  category: agent
  agents:
    - core_generation
    - dialogue_generation
    - description_generation
  priority: 85
  enabled_by_default: true

  compatibility:
    portable_layer: "Markdown 正文可作为通用写作 skill 使用。"
    loreweft_layer: "x-loreweft 声明 scene_evidence 合同、repair 路由和保护策略。"
    executable_runtime: false

  ownership:
    role: "scene_evidence_contract"
    not_roles:
      - pov_controller
      - scene_structure_planner
      - prose_repair_executor

  capability:
    summary: "要求关键情绪、关系变化和性格信息必须能从场景证据中被读者推断，而不是由旁白直接宣布。"
    mount_policy: required_for_emotional_or_conflict_scenes
    enforcement_policy: blocking_for_scene_evidence_contract_failure
    agent_method:
      - "先判断该信息是否值得展示；桥接、低价值事实可以简洁讲述。"
      - "对关键情绪或性格，找到能暴露它的压力点。"
      - "用选择、行动、潜台词、物件状态、感官痕迹或后果替代标签。"
      - "身体语言必须角色化，不能依赖通用表情包。"

  domain_scope:
    applies_to:
      - 情绪转折
      - 冲突场景
      - 关系变化
      - 揭示场景
      - 对白潜台词
      - 性格证明
    does_not_apply_to:
      - 低价值时间桥接
      - 简短事实摘要
      - 路由说明

  triggers:
    scene_roles:
      - conflict
      - emotional_turn
      - reveal
      - climax
      - turning_point
      - aftermath
    issue_types:
      - ai_emotion_label
      - emotional_claim_without_scene_evidence
      - character_trait_told_not_shown
      - abstract_explanation_standalone
      - body_language_cliche
      - thought_verb_abuse
      - dialogue_emotion_tag

  constraints:
    scene_evidence:
      emotion_label_replacement_required: true
      emotion_label_count_max: 0
      thought_verb_count_max_per_500: 2
      max_standalone_abstract_claims: 0
      trait_statement_count_max: 0
      body_language_cliche_count_max: 0
      dialogue_tag_emotion_label_forbidden: true
      min_senses_per_scene: 2
      evidence_per_emotion_claim_min: 1
      retry_policy:
        max_retries: 1
        action: emotion_to_scene_evidence
    literary_quality:
      max_medium_advisories: 1
      max_high_advisories: 0
      minimum_mode_fit_score: 5.5
      retry_policy:
        max_retries: 1
        action: prose_repair
    ai_flavor:
      max_high_advisories: 0
      retry_policy:
        max_retries: 1
        action: prose_repair

  runtime:
    kind: prompt
    execution_phase: pre_generation
    timeout_seconds: 30
    output_char_budget: 4200
    required_context:
      - scene_contract
      - pov_character_card
      - style_profile
    optional_context:
      - generated_prose_or_draft
      - previous_scene_summary
      - quality_memory
      - writing_mode_profile
      - emotion_map
      - narrative_config

  validators:
    - scene_evidence
    - literary_quality
    - ai_flavor
  validation_contracts:
    scene_evidence:
      emotion_label_replacement_required: true
      emotion_label_count_max: 0
      thought_verb_count_max_per_500: 2
      max_standalone_abstract_claims: 0
      trait_statement_count_max: 0
      body_language_cliche_count_max: 0
      dialogue_tag_emotion_label_forbidden: true
      min_senses_per_scene: 2
      evidence_per_emotion_claim_min: 1
      retry_policy:
        max_retries: 1
        action: emotion_to_scene_evidence
    literary_quality:
      max_medium_advisories: 1
      max_high_advisories: 0
      minimum_mode_fit_score: 5.5
      retry_policy:
        max_retries: 1
        action: prose_repair
    ai_flavor:
      max_high_advisories: 0
      retry_policy:
        max_retries: 1
        action: prose_repair
  repair_hooks:
    - prose_repair
  repair_strategies:
    - emotion_to_scene_evidence
    - exposition_to_action
    - trait_to_choice
    - abstract_to_sensory
    - dialogue_to_subtext

  failure_policy:
    validator_failed: "将 scene_evidence 失败指标作为 intent 路由给 prose_repair；修复后重新运行证据、文风和保护检查。"
    repair_failed: "返回 failed trace，不允许把关键情绪标签静默放行。"

  protection_policy:
    preserve:
      - facts
      - POV
      - information release order
      - scene markers
    forbid:
      - "添加无功能装饰细节。"
      - "把短标签替换成更长的抽象比喻。"

  trace_policy:
    required_fields:
      - tell_type
      - evidence_chain
      - senses_used
      - failed_metric
      - repair_hook
---
# 展示而非讲述

## 1. 定位

`show_dont_tell` 是“场景证据合同”。它要求关键情绪、关系变化、性格判断、危险感和羞耻感等内容，必须能从可观察证据中被读者推断出来。

它不等于“永远不能讲述”。成熟小说允许讲述低价值信息，例如“三日后”“他在城里住了半年”“她走到门口”。但当文本想让读者感到愤怒、恐惧、亲密、怀疑、失落、羞耻、忠诚或背叛时，必须展示。

## 2. 何时使用

必须使用：

- 场景包含情绪转折、冲突、揭示、高压选择、关系变化。
- 质量检查出现情绪标签、性格声明、抽象解释、思想动词滥用、身体语言陈词、对白情绪标注。
- 用户要求“更有画面”“少解释”“让读者自己感受到”。

可以弱化：

- 时间桥接、日常过渡、低价值背景、已知事实复述。
- 高速动作段落中，为保持速度只保留最必要证据。

## 3. 职责边界

本 skill 只负责“结论必须有证据”。它不负责：

- POV 和时间锚点：交给 `narrative_writing`。
- 场景目标、冲突、价值变化：交给 `scene_crafting`。
- AI 味模板词和破折号密度：交给 `anti_ai_prose`。
- 最终文字润色：交给 `style_polish` 或 `prose_repair`。

修复时不得改变事实、人物关系、信息释放顺序或章节 marker。

## 4. 展示优先级

证据强度从高到低：

| 优先级 | 证据类型 | 说明 |
|---|---|---|
| 1 | 压力下的选择 | 角色放弃、违背习惯、冒险、沉默、让步、反击。 |
| 2 | 不可逆行动或代价 | 钱花掉、信烧掉、钥匙交出、伤口裂开。 |
| 3 | 对白潜台词 | 答非所问、重复、停顿、转移话题、拒绝解释。 |
| 4 | 物件/环境反应 | 杯子被推远、灯灭、门闩落下、雨水浸湿契书。 |
| 5 | 角色化身体行为 | 必须来自角色习惯、身份、伤病、职业，不用通用表情。 |
| 6 | 感官细节 | 视觉、听觉、触觉、嗅觉、味觉，至少服务一个情绪或信息目标。 |
| 7 | 他人反应 | 可辅助，但不能替代 POV 角色自己的证据。 |

## 5. 识别与替换

| 讲述类型 | 识别信号 | 替换策略 |
|---|---|---|
| 情绪标签 | `他很愤怒`、`她感到害怕` | `emotion_to_scene_evidence`：动作、选择、对白、物件反应。 |
| 性格声明 | `她是个善良的人` | `trait_to_choice`：让角色在有代价时做选择。 |
| 抽象解释 | `气氛压抑`、`关系微妙` | `abstract_to_sensory`：声音、距离、物件、沉默、身体位置。 |
| 思想动词 | `意识到`、`觉得`、`明白`、`想起` | 改成触发物、判断过程或自由间接引语。 |
| 身体陈词 | `握拳`、`咬唇`、`叹气`、`皱眉` | 换成角色特有动作，或让物件承载。 |
| 对白情绪标注 | `悲伤地说`、`愤怒地问` | 用对白内容、节奏、停顿和伴随动作表达。 |

## 6. 替换公式

### 6.1 情绪标签

```text
情绪词 -> 触发压力 -> 角色选择/动作 -> 读者推断情绪
```

反例：

> 他很愤怒。

可用：

> 他把杯子放回桌面，杯底磕出一声脆响。茶水溅到契书上，他没有擦。

### 6.2 性格声明

```text
性格结论 -> 有代价的选择 -> 后果落地
```

反例：

> 她是个善良的人。

可用：

> 她把最后一包药塞进孩子怀里，自己把袖口重新缠紧。血从布边渗出来，她只把手背到身后。

### 6.3 抽象氛围

```text
抽象氛围 -> 两类以上感官/物件证据 -> 角色反应
```

反例：

> 气氛变得压抑。

可用：

> 没有人接话。炉火噼啪一声，坐在门边的人把手从刀柄上移开，又放了回去。

## 7. 对白展示规则

对白不依赖情绪副词。可以使用：

- 愤怒：短句、打断、重复、拒绝回答。
- 紧张：改口、过度解释、答非所问。
- 悲伤：未完成句、具体物件替代直接表达。
- 讽刺：礼貌用词和真实意图相反。
- 恐惧：停顿、确认、转移视线、压低声音。

禁止：

- “她悲伤地说”“他愤怒地吼道”作为主要证据。
- 每句对白后都加表情或身体动作。
- 用对白解释读者已经能看出来的情绪。

## 8. 身体语言规则

通用身体语言只能作为弱证据。使用前先问：

- 这是该角色特有的习惯吗？
- 和当前压力有因果关系吗？
- 是否能被更具体的选择或物件动作替代？
- 是否已经在附近段落重复出现？

如果答案是否定，优先不用。

## 9. 反例与改法

| 反例 | 问题 | 改法 |
|---|---|---|
| `她非常悲伤。` | 情绪标签 | 写她如何处理照片、信、衣物、称呼。 |
| `他意识到自己错了。` | 思想动词解释 | 写他撤回命令、避开某人视线、把证据藏起来。 |
| `这个人很聪明。` | 性格声明 | 写他根据微小线索提前布置或规避风险。 |
| `空气里充满紧张。` | 抽象裸句 | 写声音停止、手的位置、物件被谁移开。 |

## 10. 验收合同

必须运行 `scene_evidence`。关键指标：

| 指标 | 通过标准 | 失败动作 |
|---|---|---|
| 情绪标签 | `emotion_label_count == 0` | `emotion_to_scene_evidence` |
| 思想动词密度 | `thought_verb_count_per_500 <= 2` | `emotion_to_scene_evidence` |
| 抽象裸句 | `standalone_abstract_claims == 0` | `abstract_to_sensory` |
| 性格声明 | `trait_statement_count == 0` | `trait_to_choice` |
| 身体陈词 | `body_language_cliche_count == 0` | `emotion_to_scene_evidence` |
| 对白情绪标签 | `dialogue_emotion_tag_count == 0` | `dialogue_to_subtext` |
| 感官通道 | `sense_category_count >= 2` | `abstract_to_sensory` |

## 11. 失败路由与保护

失败后：

```text
scene_evidence failed -> prose_repair with emotion_to_scene_evidence intent
repair completed -> rerun scene_evidence
then rerun marker/protection audit
```

修复不得：

- 改变事实或信息释放顺序。
- 为展示而增加无功能细节。
- 把短标签扩写成更长的抽象比喻。
- 让非 POV 角色内心直接暴露。

## 12. Trace 要求

```json
{
  "skill_id": "show_dont_tell",
  "status": "passed | repaired | failed | degraded",
  "tell_type": "emotion_label | trait_statement | abstract_explanation | thought_verb | body_cliche | dialogue_tag",
  "evidence_chain": [
    {"type": "choice_under_pressure", "text": "observable evidence"}
  ],
  "senses_used": ["visual", "auditory"],
  "failed_metric": null,
  "repair_hook": "prose_repair"
}
```

## 13. 参考来源

- Reedsy 对 show, don't tell 的行动、感官与推断原则：https://reedsy.com/blog/show-dont-tell/
- Vanderbilt Writing Studio 关于用具体证据替代总结的写作说明：https://www.vanderbilt.edu/writing/resources/handouts/show-dont-tell/
- Purdue OWL 关于创意写作辅导中场景、细节和读者推断的建议：https://owl.purdue.edu/owl/resources/writing_tutors/tutoring_creative_writing_students/tutoring_creative_writers.html
- MasterClass 关于 exposition 何时可用、何时应场景化的说明：https://www.masterclass.com/articles/how-to-write-effective-exposition
