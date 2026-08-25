---
name: specific-detail-anchor
description: >-
  具体细节锚定协议：确保关键人物、物件、地点和氛围拥有具体、可感、与 POV 相关、
  且承载信息的细节锚点，避免抽象、装饰性、库存式描写或只有视觉的空泛布景。
version: "2.2.1"
tags:
  - writing
  - detail
  - sensory
  - concrete
  - fiction
x-loreweft:
  id: specific_detail_anchor
  display_name: "具体细节锚定"
  format_version: open_skill_v2
  source_format: x-loreweft
  kind: prompt
  domain: writing
  category: agent
  agents:
    - core_generation
    - description_generation
    - editor_in_chief
  priority: 72
  enabled_by_default: false

  compatibility:
    portable_layer: "Markdown 正文可作为通用 concrete detail skill 使用。"
    loreweft_layer: "specificity_budget 合同和 detail_anchor_repair 路由位于 x-loreweft。"
    executable_runtime: false

  ownership:
    role: "specificity_and_sensory_anchor_contract"
    not_roles:
      - scene_structure_planner
      - emotion_evidence_contract
      - fact_repair_executor

  capability:
    summary: "用精确、POV 相关、承载信息的感官锚点替代抽象和库存式描写。"
    mount_policy: on_demand_or_issue_triggered
    enforcement_policy: repair_when_specificity_budget_fails
    agent_method:
      - "在场景早段选择一个核心锚点。"
      - "每个重要场景元素至少有一个具体感官锚点。"
      - "优先选择能揭示使用痕迹、变化、缺失、阶层、时间、职业或关系的细节。"
      - "非视觉感官用于提高场景不可替代性。"
      - "删除不承载信息的装饰细节。"

  domain_scope:
    applies_to:
      - 地点建立
      - 人物首次出场
      - 关键物件
      - 氛围
      - 情绪转折的质感
    does_not_apply_to:
      - 会被描写拖慢的纯动作段
      - 低价值桥接句
      - 元数据摘要

  triggers:
    scene_roles:
      - description
      - character_intro
      - setting_establish
      - atmosphere
      - emotional_turn
      - first_appearance
    issue_types:
      - specificity_budget_unmet
      - abstract_without_evidence
      - sensory_flat
      - detail_overload
      - anchor_missing

  constraints:
    anchor_presence:
      anchor_per_element_min: 1
      anchor_sensory_modes_min: 2
      scene_anchor_position: opening_20_percent
      dominant_sense_required: true
      retry_policy:
        max_retries: 1
        action: detail_anchor_repair
    specificity_budget:
      budget_per_scene_element: 2
      budget_max: 4
      retry_policy:
        max_retries: 1
        action: detail_anchor_repair
    abstract_replacement:
      forbidden_abstract_starts: true
      abstract_evidence_pairing_required: true
      retry_policy:
        max_retries: 1
        action: detail_anchor_repair
    detail_quality:
      must_carry_information: true
      decoration_only_forbidden: true
      single_anchor_principle: true
      pov_relevance_required: true
      precise_noun_and_verb_preferred: true
      trace_or_change_preferred: true
      setting_authenticity_required: true
      retry_policy:
        max_retries: 1
        action: detail_anchor_repair

  runtime:
    kind: prompt
    execution_phase: pre_generation
    timeout_seconds: 30
    output_char_budget: 4000
    required_context:
      - scene_contract
      - scene_elements
    optional_context:
      - style_profile
      - quality_memory
      - pov_character_card
      - genre_profile

  validators:
    - specificity_budget
  validation_contracts:
    specificity_budget:
      anchor_per_element_min: 1
      sensory_modes_min: 2
      abstract_evidence_pairing_required: true
      anchor_must_carry_information: true
      core_anchor_required: true
      retry_policy:
        max_retries: 1
        action: detail_anchor_repair

  repair_hooks:
    - detail_anchor_repair
  repair_strategies:
    - select_anchor_object
    - translate_to_sensory
    - pair_abstract_with_evidence
    - reduce_detail_overload
    - add_missing_anchor
    - replace_abstract_starter
    - diversify_sensory_modes
    - loop_back_to_anchor

  failure_policy:
    validator_failed: "specificity_budget 失败时路由给 detail_anchor_repair；修复后重跑 specificity 和保护审计。"
    repair_failed: "返回 degraded trace；不得编造违反场景合同的事实或物件。"

  protection_policy:
    preserve:
      - facts
      - POV knowledge
      - scene pressure
      - genre tone
      - markers
    forbid:
      - "添加清单式库存描写。"
      - "用漂亮、古老、神秘、压抑等泛化词冒充证据。"
      - "添加 POV 无法感知或理解的细节。"

  trace_policy:
    required_fields:
      - core_anchor
      - element_anchor_coverage
      - sensory_modes_used
      - abstract_bare_count
      - decoration_only_detail_count
      - repair_hook
---
# 具体细节锚定

## 1. 定位

`specific_detail_anchor` 是场景质感合同。它解决的问题不是“描写不够多”，而是“描写没有落点”。一个有效锚点必须具体、可感、与 POV 相关，并且承载信息。

好细节不靠数量，而靠选择。一个磨亮的桌角、少了一枚扣子的袖口、雨水顺着窗棂滴落的声音，往往比十句“古老、压抑、美丽、神秘”更有用。

## 2. 何时使用

触发场景：

- 地点第一次建立。
- 人物或关键物件第一次出场。
- 氛围、情绪转折需要质感支撑。
- 质量系统报告 anchor_missing、sensory_flat、abstract_without_evidence、detail_overload。
- 用户要求“更具体”“更有画面”“少空泛”“增加质感”。

可以弱化：

- 高速动作段，过多描写会拖慢节奏。
- 已经被读者熟悉的地点重复出现。
- 低价值过渡句。

## 3. 职责边界

| 相邻 skill | 边界 |
|---|---|
| `show_dont_tell` | 它管情绪和性格是否有证据；本 skill 管场景元素是否有具体锚点。 |
| `scene_crafting` | 它管场景目标和冲突；本 skill 管这些元素如何被感知。 |
| `anti_ai_prose` | 它管泛化词和 AI 腔；本 skill 提供替代泛化词的具体材料。 |
| `detail_anchor_repair` | 本 skill 声明失败标准；repair 系统执行局部替换。 |

## 4. 锚点选择原则

核心锚点应当：

- 小而具体：杯沿、门闩、袖口、蜡油、药味、木刺、钥匙齿。
- 可被感官捕捉：看见、听见、摸到、闻到、尝到。
- 与 POV 有关：角色会注意它、误读它、避开它或使用它。
- 有信息量：暗示时间、阶层、职业、伤病、关系、伏笔、变化。
- 能回扣：开头出现，中段被使用，结尾发生变化或形成呼应。

不要选：

- 大而空的概念：环境、氛围、命运、古老感。
- 可套用到任何地方的库存布景。
- 只有漂亮但没有信息的装饰。

## 5. 元素锚定优先级

| 优先级 | 元素 | 最低锚点 |
|---|---|---|
| 1 | 关键人物 | 外形/动作/声音/气味/习惯至少 1 个，最好带处境信息。 |
| 2 | 关键物件 | 至少 1 个可感细节，说明使用痕迹、变化或功能。 |
| 3 | 关键地点 | 至少 1 个定位细节和 1 个不可替代细节。 |
| 4 | 氛围 | 用感官证据承载，不直接说“压抑/温暖/神秘”。 |

## 6. 感官策略

视觉负责定位，非视觉负责不可替代性。

| 场景 | 优先感官 |
|---|---|
| 厨房、药铺、雨后街巷 | 嗅觉、触觉 |
| 黑暗、隔墙、追踪 | 听觉 |
| 受伤、寒冷、紧张 | 触觉 |
| 记忆、旧物、亲密 | 嗅觉、触觉 |
| 权力空间、法庭、宗门大殿 | 声音、距离、材质、礼仪细节 |

不要机械凑五感。选择最能区分本场景的主感官，再用一个次感官补强。

## 7. 抽象转具体

转换公式：

```text
抽象形容词 -> 可感证据 -> 角色反应或信息功能
```

示例：

| 抽象 | 具体锚点 |
|---|---|
| 破旧 | 墙皮裂开露出灰砖，门轴每次转动都发出干涩声。 |
| 安静 | 钟摆滴答声被放大，纸页翻动像擦过耳边。 |
| 紧张 | 指甲一下一下扣着桌边，木屑落进袖口。 |
| 思念 | 杯沿被摩挲得发亮，茶渍停在旧裂纹里。 |
| 压抑 | 炉火噼啪一声，所有人同时停住筷子。 |

## 8. 高信息细节类型

优先使用：

1. 使用痕迹：磨亮、补丁、裂纹、褪色、旧伤、反复摆放位置。
2. 变化痕迹：少了一把椅子、新裂缝、气味消失、灰尘被擦掉一角。
3. 地域/时代/职业细节：器具名称、礼仪、材料、工具、食物、称谓。
4. 缺席细节：灵堂没有香味、儿童房没有玩具、厨房没有烟火气。
5. 互动细节：角色碰到、绕开、误认、破坏或修复某物。

低价值细节应删除：

- 只说明漂亮、华丽、古老、特殊。
- 清单式陈列：桌子、椅子、柜子都有，但没有信息。
- 和场景目标、情绪、伏笔、人物无关。

## 9. 细节预算

不是越多越好。

| 元素 | 建议预算 |
|---|---|
| 核心锚点 | 2-3 个细节，可在场景中回扣。 |
| 关键人物 | 1-2 个细节。 |
| 关键物件 | 1 个强细节。 |
| 地点 | 1-2 个定位/不可替代细节。 |
| 氛围 | 1 个感官证据即可。 |

每个场景元素最多 4 个细节，总细节量不宜超过正文的 15%。超过后优先删除装饰性细节。

## 10. POV 过滤

细节必须属于当前 POV：

- 医者先注意伤口、药味、脉象。
- 盗贼先注意门闩、出口、脚步声。
- 孩子先注意高度、颜色、声音和恐惧。
- 贵族先注意礼仪、材质、座次。

同一地点在不同 POV 下应呈现不同锚点。

## 11. 禁止事项

- 重要元素用抽象形容词开头但没有证据。
- 全场只有视觉，没有声音、触感、气味或味觉。
- 无核心锚点，所有描写散点铺开。
- 堆砌细节，拖慢场景目标。
- 装饰性细节不承载任何信息。
- 锚点开头出现，后文再也不回扣。
- 添加 POV 无法感知或理解的专业信息。
- 重复介绍同一地点或物件。

## 12. 验收合同

必须运行 `specificity_budget`。

| 指标 | 通过标准 | 失败动作 |
|---|---|---|
| 核心锚点 | `core_anchor_present == true` | `select_anchor_object` |
| 元素覆盖 | 每个 scene_element 至少 1 个锚点 | `add_missing_anchor` |
| 感官多样性 | `sensory_modes_used >= 2` | `diversify_sensory_modes` |
| 抽象证据配对 | 每个抽象描述紧跟感官证据 | `pair_abstract_with_evidence` |
| 信息承载 | 无纯装饰细节 | `reduce_detail_overload` |
| 锚点闭环 | 核心锚点在结尾附近回扣或变形 | `loop_back_to_anchor` |

## 13. 失败路由与保护

```text
specificity_budget failed -> detail_anchor_repair
repair completed -> rerun specificity_budget -> rerun protected audit
```

修复不得：

- 新增违反事实合同的物件。
- 改变场景目标和压力。
- 让动作场景被描写拖慢。
- 添加 POV 不可能知道的地域、职业或历史细节。

## 14. Trace 要求

```json
{
  "skill_id": "specific_detail_anchor",
  "status": "passed | repaired | failed | degraded",
  "core_anchor": "老旧瓷杯",
  "element_anchor_coverage": 1.0,
  "sensory_modes_used": ["visual", "tactile", "auditory"],
  "abstract_bare_count": 0,
  "decoration_only_detail_count": 0,
  "repair_hook": "detail_anchor_repair"
}
```

## 15. 参考来源

- MasterClass 关于 concrete details 的写作原则：https://www.masterclass.com/articles/how-to-use-concrete-details-to-enhance-your-writing
- MasterClass 关于 sensory imagery 的说明：https://www.masterclass.com/articles/sensory-imagery-in-creative-writing
- WAC Clearinghouse 关于 descriptive detail 的指南：https://wacclearinghouse.org/resources/writing/guides/detail/
- Purdue OWL 关于 specificity 与语言具体性的建议：https://owl.purdue.edu/owl/subject_specific_writing/professional_technical_writing/donation_request_letters/language_considerations.html
- Purdue OWL 关于 concision 与去除空泛语言：https://owl.purdue.edu/owl/general_writing/academic_writing/conciseness/index.html
- Traci Chee 关于 significant concrete detail 的写作过程：https://www.tracichee.com/news/work-and-process-week-22
