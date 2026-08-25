---
name: style-polish
description: Check generated prose for forbidden wording, repetition, transitions, and style conflicts.
version: 1.0.0
license: internal
tags:
  - generation
  - style
  - service
x-loreweft:
  id: style_polish
  display_name: "文风润色"
  format_version: open_skill_v1
  kind: service
  domain: generation
  category: utility
  agents:
    - editor_in_chief
    - core_generation
  priority: 50
  enabled_by_default: true
  runtime:
    service_class: app.skills.style_polish.StylePolishSkill
    service_method: polish
    execution_phase: post_generation
---

# Style Polish

## Use When

- Generated prose needs deterministic style diagnostics after drafting.
- The system needs to identify forbidden words, repeated phrases, weak transitions, or style-profile conflicts.
- A later repair pass needs concrete style findings.

## Instructions

- Treat this as a post-generation service because it requires the generated text.
- Preserve the original prose unless a downstream repair or accept/reject policy chooses to apply changes.
- Report findings, suggestions, transition changes, and style conflicts in structured output.

## Runtime Output

The service returns style findings, repaired transition suggestions, and style consistency diagnostics for the generated text.
