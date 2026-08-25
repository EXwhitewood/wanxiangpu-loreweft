---
name: attention-director
description: Build focused generation guidance from the current scene beat and story state.
version: 1.0.0
license: internal
tags:
  - generation
  - focus
  - service
x-loreweft:
  id: attention_director
  display_name: "注意力导演"
  format_version: open_skill_v1
  kind: service
  domain: generation
  category: utility
  agents:
    - core_generation
    - dialogue_generation
    - description_generation
  priority: 90
  enabled_by_default: true
  runtime:
    service_class: app.skills.attention_director.AttentionDirectorSkill
    service_method: build_prompt
    execution_phase: pre_generation
---

# Attention Director

## Use When

- A scene has competing context sources and needs a clear attention hierarchy.
- The Writer needs focused guidance for conflict, POV knowledge, foreshadowing, style, or relevant rules.
- The generation prompt should avoid static templates and be assembled from the live scene context.

## Instructions

- Prioritize current scene conflict and POV over distant context.
- Keep attention guidance compact and actionable.
- Include foreshadowing, style, chapter summaries, forward constraints, and relevant rules only when present.
- Generate guidance from runtime context rather than a fixed generic template.

## Runtime Output

The service returns a focused prompt section for the current generation turn.
