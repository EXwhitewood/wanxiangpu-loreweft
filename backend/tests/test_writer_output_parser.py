"""方案9：writer 事实填写单解析器测试。"""
from __future__ import annotations

from app.services.writer_output_parser import parse_writer_output, validate_scene_facts


def test_plan9_parse_writer_output_with_facts():
    """含正文 + JSON 事实清单的 writer 输出，应正确分离。"""
    raw = """林鹤站在山洞入口，风从背后吹来。

```json
{
  "scene_facts": {
    "established_facts": ["林鹤是李云的师兄"],
    "character_states": {
      "林鹤": {"location": "山洞入口", "emotion_state": "警惕"}
    },
    "location_states": {},
    "timeline_events": ["黄昏时分到达山洞"],
    "item_states": {},
    "foreshadowing_operations": [
      {
        "name": "玉佩的秘密",
        "action": "bury",
        "evidence_text": "林鹤下意识按了一下胸前的玉佩",
        "intended_reveal": "第15章揭示"
      }
    ]
  }
}
```"""
    text, facts = parse_writer_output(raw)

    assert "林鹤站在山洞入口" in text
    assert "```json" not in text
    assert facts["established_facts"] == ["林鹤是李云的师兄"]
    assert "林鹤" in facts["character_states"]
    assert facts["character_states"]["林鹤"]["location"] == "山洞入口"
    assert len(facts["foreshadowing_operations"]) == 1
    assert facts["foreshadowing_operations"][0]["action"] == "bury"
    assert facts["foreshadowing_operations"][0]["evidence_text"] == "林鹤下意识按了一下胸前的玉佩"


def test_plan9_parse_writer_output_without_facts():
    """只有正文的 writer 输出，应降级返回 (raw_output, {})。"""
    raw = "林鹤站在山洞入口，风从背后吹来。"
    text, facts = parse_writer_output(raw)

    assert text == raw
    assert facts == {}


def test_plan9_parse_writer_output_with_invalid_json():
    """JSON 格式错误时，应降级返回 (raw_output, {})。"""
    raw = """正文内容。

```json
{invalid json content
```
"""
    text, facts = parse_writer_output(raw)

    assert text == raw
    assert facts == {}


def test_plan9_parse_writer_output_with_empty_string():
    """空字符串输入。"""
    text, facts = parse_writer_output("")

    assert text == ""
    assert facts == {}


def test_plan9_parse_writer_output_takes_last_json_block():
    """有多个 JSON 块时，取最后一个作为事实清单。"""
    raw = """正文。

```json
{"scene_facts": {"established_facts": ["第一块"]}}
```

更多文本。

```json
{"scene_facts": {"established_facts": ["第二块"]}}
```
"""
    text, facts = parse_writer_output(raw)

    assert facts["established_facts"] == ["第二块"]
    assert "正文" in text
    assert "更多文本" in text


def test_plan9_parse_writer_output_missing_scene_facts_key():
    """JSON 块存在但没有 scene_facts key，返回空 facts。"""
    raw = """正文。

```json
{"other_key": "value"}
```
"""
    text, facts = parse_writer_output(raw)

    assert facts == {}


def test_plan9_validate_scene_facts_complete():
    """完整的事实清单，validate 应返回空缺失列表。"""
    facts = {
        "established_facts": [],
        "character_states": {},
        "location_states": {},
        "timeline_events": [],
        "item_states": {},
        "foreshadowing_operations": [],
        "rule_implications": [],  # 方案 22 B4：新增字段
    }
    missing = validate_scene_facts(facts)
    assert missing == []


def test_plan9_validate_scene_facts_missing_fields():
    """缺失字段的事实清单，validate 应返回缺失字段列表。"""
    facts = {
        "established_facts": [],
        "character_states": {},
    }
    missing = validate_scene_facts(facts)
    assert "location_states" in missing
    assert "timeline_events" in missing
    assert "item_states" in missing
    assert "foreshadowing_operations" in missing
    assert "rule_implications" in missing  # 方案 22 B4：新字段也应在校验中


def test_plan9_validate_scene_facts_empty():
    """空事实清单，validate 应返回 scene_facts_empty。"""
    missing = validate_scene_facts({})
    assert "scene_facts_empty" in missing
