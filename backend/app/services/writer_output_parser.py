"""Writer 输出解析器。

方案9：分离 writer 输出的正文和结构化事实清单。
writer 在创作时同步输出事实清单（JSON），替代事后 LLM 抽取，实现零信息损失。
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


# 匹配最后的 ```json``` 块（非贪婪匹配最后一个）
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def parse_writer_output(raw_output: str) -> tuple[str, dict]:
    """分离正文和事实清单。

    方案9：writer 在正文之后输出 ```json``` 包裹的事实清单。
    本函数负责分离两部分，返回 (正文, 事实清单)。
    如果没有事实清单（格式错误、模型未输出），返回 (raw_output, {})。

    Args:
        raw_output: writer 的原始输出（正文 + 可选的 JSON 事实清单）

    Returns:
        tuple (正文, 事实清单 dict)。事实清单为空 dict 表示降级到 LLM 抽取。
    """
    if not raw_output or not raw_output.strip():
        return raw_output or "", {}

    # 查找所有 JSON 块，取最后一个作为事实清单
    matches = list(_JSON_BLOCK_RE.finditer(raw_output))
    if not matches:
        return raw_output, {}

    match = matches[-1]
    json_str = match.group(1)
    try:
        facts = json.loads(json_str)
    except json.JSONDecodeError as exc:
        # 方案22修复：LLM 输出的 JSON 字符串值中可能含未转义的控制字符（裸换行符等），
        # 导致 json.loads 失败。尝试 strict=False 容错模式重试。
        try:
            facts = json.loads(json_str, strict=False)
        except json.JSONDecodeError as exc2:
            logger.warning(
                "[writer_output_parser] JSON decode failed (strict=False also failed): %s", exc2
            )
            return raw_output, {}

    if not isinstance(facts, dict):
        logger.warning("[writer_output_parser] facts is not a dict: %s", type(facts).__name__)
        return raw_output, {}

    scene_facts = facts.get("scene_facts", {})
    if not isinstance(scene_facts, dict):
        logger.warning("[writer_output_parser] scene_facts is not a dict: %s", type(scene_facts).__name__)
        return raw_output, {}

    # 正文是 JSON 块之前的部分
    text = raw_output[: match.start()].rstrip()
    logger.debug(
        "[writer_output_parser] parsed: text_len=%d, facts_keys=%s",
        len(text),
        sorted(scene_facts.keys()),
    )
    return text, scene_facts


def validate_scene_facts(scene_facts: dict) -> list[str]:
    """校验事实清单的完整性，返回缺失字段列表。

    方案9：用于降级判断——如果关键字段缺失，记录日志但仍接受（部分降级）。

    Args:
        scene_facts: parse_writer_output 返回的事实清单

    Returns:
        缺失的字段名列表（空列表表示完整）
    """
    if not scene_facts:
        return ["scene_facts_empty"]

    required_fields = [
        "established_facts",
        "character_states",
        "location_states",
        "timeline_events",
        "item_states",
        "foreshadowing_operations",
        # 方案 22 B4：rule_implications 为新增字段，writer 应声明（可为空列表）。
        # 若 writer 未输出该字段，仍按降级处理（缺失记录在 missing 中）。
        "rule_implications",
    ]
    missing = [field for field in required_fields if field not in scene_facts]
    return missing
