---
name: outline-validation
description: 检查大纲的伏笔闭环、人物弧光连续性和时间线矛盾
version: '1'
x-loreweft:
  id: outline_validation
  display_name: 大纲校验
  format_version: open_skill_v1
  kind: service
  domain: generation
  category: utility
  agents:
  - outline_architect
  priority: 80
  enabled_by_default: true
  runtime:
    service_class: app.skills.outline_validation.OutlineValidationSkill
    service_method: validate
---

## 校验维度

- 伏笔闭环检查
- 人物弧光连续性
- 时间线矛盾
- 人物一致性
