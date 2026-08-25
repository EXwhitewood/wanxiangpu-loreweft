---
name: shell-search
description: 通过向量搜索检索相关叙事片段
version: '1'
x-loreweft:
  id: shell_search
  display_name: Shell 搜索
  format_version: open_skill_v1
  kind: service
  domain: generation
  agents:
  - dialogue_generation
  - description_generation
  - detail_detective
  priority: 60
  enabled_by_default: true
  runtime:
    service_class: app.skills.shell_search.ShellSearchSkill
    service_method: execute
---

# 外壳搜索

在生成过程中搜索外部知识库或参考资料，为文本提供事实性支撑。

## 规则

1. **按需搜索**：仅在生成涉及专业领域知识（历史、科学、文化细节等）时触发搜索，日常描写不触发。
2. **来源可信度**：搜索结果必须标注来源可信度等级，低可信度来源的信息仅供参考，不得直接写入正文。
3. **搜索深度控制**：单次搜索深度不超过 3 层关联，防止搜索链无限延伸。
4. **结果去重**：对搜索结果进行去重和冲突检测，矛盾信息需标注供人工判断。
