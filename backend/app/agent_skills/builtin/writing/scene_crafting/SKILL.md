---
name: scene-crafting
description: >-
  小说场景营造协议：要求完整场景具备即时目标、主动阻力、危机或压力选择、行动结果、价值变化和退场动能。
  同时支持行动场景、反应场景和混合场景，避免无目标、无冲突、无变化、无钩子的散文式段落。
version: "2.2.1"
tags:
  - writing
  - scene
  - craft
x-loreweft:
  id: scene_crafting
  display_name: "场景营造"
  format_version: open_skill_v2
  source_format: x-loreweft
  kind: prompt
  domain: writing
  category: agent
  agents:
    - core_generation
    - dialogue_generation
    - description_generation
  priority: 84
  enabled_by_default: true

  compatibility:
    portable_layer: "Markdown 正文可作为通用场景写作 skill。"
    loreweft_layer: "x-loreweft 声明 scene_structure、narrative_experience、repair 路由和保护策略。"
    executable_runtime: false

  ownership:
    role: "scene_structure_contract"
    not_roles:
      - pov_controller
      - style_detector
      - repair_executor

  capability:
    summary: "让每个生成场景通过目标、阻碍、压力、选择、后果和读者动能承担叙事功能。"
    mount_policy: required_when_generating_complete_scene
    enforcement_policy: blocking_for_missing_goal_conflict_value_change_or_hook
    agent_method:
      - "先判定场景类型：行动、反应或混合。"
      - "生成前内部写清 POV 角色的即时场景目标。"
      - "设置会阻挡目标的主动阻力，并至少升级一次。"
      - "迫使角色面对压力选择、揭示或不可逆行动。"
      - "以价值变化和自然的下一场牵引收束。"

  domain_scope:
    applies_to:
      - 完整场景
      - 章节场景单元
      - 冲突段
      - 揭示段
      - 余波场景
    does_not_apply_to:
      - 孤立文风润色
      - 元数据摘要
      - 事实报告

  triggers:
    scene_roles:
      - conflict
      - emotional_turn
      - reveal
      - climax
      - turning_point
      - calm_before_storm
      - setup
      - resolution
    issue_types:
      - scene_structure_broken
      - low_pressure_scene
      - passive_scene
      - missing_hook
      - information_order_wrong
      - scene_goal_unclear
      - scene_no_value_change

  constraints:
    scene_structure:
      goal_present: required
      conflict_present: required
      conflict_count_min: 1
      value_change: required
      hook_present: required
      passive_percentage_max: 0.15
      pure_exposition_block_max_words: 150
      retry_policy:
        max_retries: 1
        action: scene_restructure
    narrative_experience:
      pressure_score_min: 3
      reader_momentum_at_exit: required
      max_high_advisories: 0
      retry_policy:
        max_retries: 1
        action: scene_restructure
    scene_minimum:
      goal_clarity: required
      conflict_count_min: 1
      value_change: required
    hook:
      required: true
      cheap_hook_forbidden: true
    entry_exit:
      entry_establish_sentences_max: 3
      exit_no_abrupt_cut: true
    scene_quality:
      passive_percentage_max: 0.15
      pure_exposition_block_max_words: 150
    retry_policy:
      max_retries: 1
      action: scene_restructure

  runtime:
    kind: prompt
    execution_phase: pre_generation
    timeout_seconds: 30
    output_char_budget: 4000
    required_context:
      - scene_contract
      - chapter_outline
      - pov_character_card
      - world_rules_relevant
      - previous_scene_summary
      - foreshadowing_map
    optional_context:
      - narrative_experience_contract
      - writing_mode_profile
      - quality_memory
      - next_scene_preview

  validators:
    - scene_structure
    - narrative_experience
  validation_contracts:
    scene_structure:
      goal_present: required
      conflict_present: required
      conflict_count_min: 1
      value_change: required
      hook_present: required
      passive_percentage_max: 0.15
      pure_exposition_block_max_words: 150
      retry_policy:
        max_retries: 1
        action: scene_restructure
    narrative_experience:
      pressure_score_min: 3
      reader_momentum_at_exit: required
      max_high_advisories: 0
      retry_policy:
        max_retries: 1
        action: scene_restructure
  repair_hooks:
    - scene_restructure
  repair_strategies:
    - inject_goal
    - escalate_conflict
    - restructure_value_change
    - add_hook
    - exposition_to_action

  failure_policy:
    validator_failed: "scene_structure 或 narrative_experience 失败时路由给 scene_restructure；修复后重跑场景结构和保护审计。"
    repair_failed: "除非 workflow 明确接受 degraded scene，否则阻断提交。"

  protection_policy:
    preserve:
      - facts
      - POV
      - foreshadowing obligations
      - scene markers
      - information order
    forbid:
      - "为了制造压力而新增无关冲突。"
      - "使用空洞预言句作为钩子。"

  trace_policy:
    required_fields:
      - scene_type
      - objective
      - conflict_count
      - value_change
      - hook_type
      - pressure_score
      - failed_metric
---
# 场景营造

## 1. 定位

`scene_crafting` 是场景结构合同。它确保一个场景不是“有人走路、有人想事、背景被解释一遍”，而是一个会改变局面的叙事单元。

有效场景至少回答：

```text
角色此刻想要什么？
什么阻止他得到？
压力如何升级？
他做了什么选择或行动？
场景结束时，局面和开场相比改变了什么？
读者为什么想进入下一场？
```

## 2. 何时使用

必须使用：

- 生成完整场景或章节内场景单元。
- 场景类型为 conflict、reveal、climax、turning_point、emotional_turn、aftermath。
- 质量系统报告无目标、低压力、无冲突、无价值变化、无钩子、被动场景、说明段过长。

可以弱化：

- 极短过渡段。
- 非正文摘要。
- 单句动作连接。

## 3. 职责边界

| 相邻 skill | 边界 |
|---|---|
| `narrative_writing` | 它管谁在看、如何叙述；本 skill 管场景事件是否成立。 |
| `show_dont_tell` | 它管情绪证据；本 skill 只要求关键目标、冲突和结局被现场化。 |
| `anti_ai_prose` | 它管模板腔和语言信号；本 skill 不因词句问题重构场景。 |
| `scene_restructure` | 本 skill 声明失败原因和验收标准；真正修复由 repair 系统执行。 |

## 4. 场景类型判定

| 类型 | 适用 | 必备结构 |
|---|---|---|
| 行动场景 | 追逐、谈判、对抗、揭示、高压任务 | 目标 -> 阻力 -> 升级 -> 危机选择 -> 行动结果 |
| 反应场景 | 失败后、余波、冷静前、情绪转折 | 反应 -> 困境判断 -> 下一步决定 |
| 混合场景 | 揭示后立刻行动，逃亡后立刻决策 | 先完成反应，再生成新目标和新阻力 |

如果类型不明确，默认按行动场景处理，但不得把反应场景写成无目标的内心独白。

## 5. 行动场景协议

### 5.1 目标

目标必须是本场景内可争夺、可失败、可部分完成的具体结果。

弱目标：

- 他想变强。
- 她想弄清真相。

有效目标：

- 他要在巡夜人回来前拿到账册。
- 她要让对方承认那封信不是伪造的。
- 他要把受伤的孩子带出城门。

### 5.2 阻力

阻力必须主动阻挡目标：

- 对手拒绝、欺骗、攻击、拖延。
- 时间限制逼近。
- 规则、地形、能力代价限制行动。
- 角色内在恐惧或旧伤阻碍选择。

没有阻力的场景只是任务清单。

### 5.3 升级

冲突不能平铺。至少出现一次局势恶化：

```text
简单方法失败 -> 新代价出现 -> 角色被迫改变策略
```

### 5.4 危机与高潮

危机不是“事情发生”，而是角色面临有代价的选择：

- 说出真相会伤害同伴；沉默会让无辜者被带走。
- 留下能救一个人；离开能保住证据。
- 交出钥匙可暂时脱身；保住钥匙会暴露身份。

高潮必须由角色行动触发，不是外力替他解决。

### 5.5 结局与价值变化

价值变化必须可读：

| 变化 | 例子 |
|---|---|
| 安全 -> 危险 | 追兵发现暗门。 |
| 未知 -> 已知 | 角色知道信来自谁。 |
| 信任 -> 怀疑 | 同伴的证词和物证冲突。 |
| 控制 -> 失控 | 谈判对象反拿出筹码。 |
| 孤立 -> 结盟 | 敌人提出临时合作。 |

## 6. 反应场景协议

反应场景不是停下来感叹，而是上一场结果进入角色系统的过程：

1. Reaction：可观察反应。沉默、动作失衡、逃避、关系退缩、身体代价。
2. Dilemma：两个后续选择都不轻松，困境来自已发生事件。
3. Decision：角色做出下一步决定，并形成下一场目标或钩子。

如果只有情绪，没有决定，反应场景未完成。

## 7. 入场和退场

入场：

- 前三句内建立地点、时间、POV、当前压力。
- 优先从动作、对话、异常物件或正在发生的变化切入。
- 不用大段背景解释开场。

退场：

- 必须完成本轮冲突回合。
- 不在冲突半截突然断章。
- 钩子必须来自本场后果或下一场压力。

廉价钩子示例：

- `他不知道，这将改变一切。`
- `而这，只是一个开始。`

替代：

- 门后的脚步声停在第三阶。
- 她发现账册最后一页少了一个名字。
- 他把钥匙交出去后，对方仍没有放人。

## 8. 信息分配

说明信息必须嵌入行动：

- 性格通过选择展示。
- 世界规则通过角色与规则的碰撞展示。
- 背景信息分散进对话、物件、动作后果。
- 连续纯说明段不超过 150 字。
- 被动描写占比不超过场景总量的 15%。

## 9. 禁止事项

- 无目标场景。
- 无阻力场景。
- 无价值变化场景。
- 冲突未形成回合就断章。
- 用空洞预言当钩子。
- 为满足冲突而新增无授权设定。
- 用大段世界观说明替代角色行动。

## 10. 验收合同

必须运行：

- `scene_structure`
- `narrative_experience`

| 指标 | 通过标准 | 失败动作 |
|---|---|---|
| 场景目标 | `goal_present == true` | `inject_goal` |
| 冲突存在 | `conflict_present == true` | `escalate_conflict` |
| 冲突数量 | `conflict_count >= 1` | `escalate_conflict` |
| 价值变化 | `value_change == true` | `restructure_value_change` |
| 钩子存在 | `hook_present == true` | `add_hook` |
| 被动描写 | `passive_percentage <= 0.15` | `exposition_to_action` |
| 说明段长度 | `pure_exposition_block <= 150` | `exposition_to_action` |
| 压力分 | `pressure_score >= 3` | `scene_restructure` |

## 11. 失败路由与保护

```text
scene_structure failed -> scene_restructure
narrative_experience failed -> scene_restructure
repair completed -> rerun validators -> rerun marker/protection audit
```

修复不得：

- 改变已确定事实。
- 删除伏笔义务。
- 切换 POV。
- 改变信息释放顺序。
- 为制造冲突而添加无来源敌人、灾难、设定。

## 12. Trace 要求

```json
{
  "skill_id": "scene_crafting",
  "status": "passed | repaired | failed | degraded",
  "scene_type": "action | reaction | hybrid",
  "objective": "本场景即时目标",
  "conflict_count": 2,
  "value_change": "未知 -> 已知",
  "hook_type": "信息钩子",
  "pressure_score": 6,
  "failed_metric": null
}
```

## 13. 参考来源

- Story Grid Five Commandments of Storytelling：https://storygrid.com/five-commandments-of-storytelling/
- Valerie Francis 对 Story Grid 场景五要素的总结：https://valeriefrancis.ca/post-2-5-commandments-storytelling/
- September C. Fawkes 对 Swain 行动场景结构的整理：https://www.septembercfawkes.com/2021/09/scene-structure-according-to-dwight-v.html
- September C. Fawkes 对 sequel / reaction structure 的整理：https://www.septembercfawkes.com/2021/10/sequel-structure-according-to-swain.html
- Advanced Fiction Writing 关于 scenes 与 sequels 的说明：https://www.advancedfictionwriting.com/blog/2009/05/25/scenes-sequels-and-chapters/
