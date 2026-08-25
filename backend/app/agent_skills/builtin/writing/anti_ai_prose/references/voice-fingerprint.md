# 声纹指纹规范

## 目标

去 AI 味不能只做减法。删除通用模型模式后，必须恢复当前叙述者或 POV 角色自己的观察和表达方式。

## 四层声纹来源

1. 项目叙述声纹：全书稳定的文体、标点、句长和抽象度。
2. POV 角色声纹：职业、知识、偏见、身体状态、关系立场。
3. 场景压力声纹：恐惧、疲劳、疼痛、兴奋会改变句长与注意力。
4. 题材声纹：悬疑、权谋、言情、动作、日常对细节选择不同。

## 推荐结构

```yaml
voice_fingerprint:
  sentence_distribution:
    average: 22
  punctuation_habits:
    preferred: ["。", "，"]
    avoided: ["——"]
  lexical_register:
    level: restrained
    preferred_words: []
    avoided_words: []
  observation_domains:
    profession: []
    sensory: []
    relationship: []
  metaphor_source_domains: []
  transition_habits: []
  uncertainty_style: indirect
  emotional_indirection: high
  preferred_markers: []
  signature_phrases: []
```

## 缺失策略

没有声纹时：

- 记录 `degraded`；
- 执行通用声纹检查；
- 不自行发明角色口癖；
- 不默认阻断，除非合同要求 `profile_required: true`。

## 重建原则

- 恢复角色注意什么，比替换几个词更重要。
- 恢复角色如何误解，比增加口语更重要。
- 恢复角色不愿说什么，比增加情绪标签更重要。
- 恢复句子在压力下的变化，比随机制造 burstiness 更重要。
