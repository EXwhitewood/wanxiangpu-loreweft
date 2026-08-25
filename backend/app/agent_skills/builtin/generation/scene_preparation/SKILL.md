---
name: scene-preparation
description: Build the scene context package before Chinese fiction generation.
version: 1.0.0
license: internal
tags:
  - generation
  - context
  - service
x-loreweft:
  id: scene_preparation
  display_name: "场景准备"
  format_version: open_skill_v1
  kind: service
  domain: generation
  category: utility
  agents:
    - core_generation
    - dialogue_generation
    - description_generation
    - editor_in_chief
  priority: 100
  enabled_by_default: true
  runtime:
    service_class: app.skills.scene_preparation.ScenePreparationSkill
    service_method: prepare
    execution_phase: pre_generation
---

# Scene Preparation

## Use When

- A Writer or generation agent needs scene-specific context before drafting.
- The generation task has a scene beat, project id, chapter state, or scene contract.
- The system needs a stable context snapshot before the model writes prose.

## Instructions

- Gather only context directly relevant to the current scene.
- Prefer authoritative project/state data over inference.
- Preserve the prepared context as a stable snapshot during the generation turn.
- Run before lower-priority generation services.

## Runtime Output

The service may provide character cards, location cards, historical details, foreshadowing guidance, style context, relevant rules, and attention guidance for the active scene.
