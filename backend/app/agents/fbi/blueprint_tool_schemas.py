"""审查蓝图官 Agent 的 tool schema 定义（T3.1）。

讨论稿 §5.2.3 一次会话三轮分步：
- 第1轮：调 submit_review_and_route（复核+路由）→ 闸门①校验
- 第2轮：调 N 个 deterministic_tool_<metric>（并发）→ 系统执行确定性工具
- 第3轮：调 submit_work_units（LLM 出图）→ 闸门②校验

设计要点：
- 两个 tool schema + 两个硬校验闸门 + 会话内重试（§5.2.2 机制3）
- 确定性工具的 operation 必须来自路由表，LLM 不得自己生成
- LLM 必须调 tool 提交，不能在文本里自由输出（§5.2.4 防自由发挥）
"""
from __future__ import annotations

from app.services.fbi.blueprint_route_registry import get_blueprint_route


# ---------------------------------------------------------------------------
# 第1轮：复核+路由 tool
# ---------------------------------------------------------------------------
SUBMIT_REVIEW_AND_ROUTE_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "submit_review_and_route",
        "description": (
            "提交归一化复核结果和路由判断。每个 issue 必须出现在 review_results 里。"
            "agree=认可归一化结果；correct=纠正误判（必须给 corrected_family）；"
            "supplement=补判漏判（必须给 corrected_family）。"
            "route=deterministic 表示走确定性工具；route=llm 表示 LLM 自己出图。"
            "同句多问题必须统一 route=llm（同句一致性）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "review_results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "issue_id": {"type": "string"},
                            "action": {
                                "type": "string",
                                "enum": ["agree", "correct", "supplement"],
                                "description": "agree=认可归一化; correct=纠正误判; supplement=补判漏判",
                            },
                            "corrected_family": {
                                "type": "string",
                                "description": "action=correct/supplement 时必填",
                            },
                            "route": {
                                "type": "string",
                                "enum": ["deterministic", "llm"],
                                "description": "deterministic=走确定性工具; llm=LLM自己出图",
                            },
                            "evidence": {
                                "type": "string",
                                "description": "判断理由（可选）",
                            },
                        },
                        "required": ["issue_id", "action", "route"],
                    },
                }
            },
            "required": ["review_results"],
        },
    },
}


# ---------------------------------------------------------------------------
# 第3轮：出图 tool
# ---------------------------------------------------------------------------
SUBMIT_WORK_UNITS_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "submit_work_units",
        "description": (
            "提交 route=llm 的 issue 的修复 work_units。"
            "每个 work_unit 必须包含 source_issue_ids（覆盖哪些 issue）+ operation + 完整 patch 字段。"
            "operation 只允许 replace_exact/delete_exact/insert_before_anchor/insert_after_anchor，"
            "禁止使用确定性意图型 operation（如 replace_span、normalize_punctuation 等）。"
            "同句打包的 deterministic issue 也必须在此覆盖（整句不拆）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "work_units": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source_issue_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "本 work_unit 覆盖的 issue_id 列表",
                            },
                            "operation": {
                                "type": "string",
                                "enum": [
                                    "replace_exact",
                                    "delete_exact",
                                    "insert_before_anchor",
                                    "insert_after_anchor",
                                ],
                            },
                            "old_text": {
                                "type": "string",
                                "description": "要替换/删除的原文（replace_exact/delete_exact 必填）",
                            },
                            "new_text": {
                                "type": "string",
                                "description": "替换或插入的新文本（所有 operation 都必填）",
                            },
                            "anchor_text": {
                                "type": "string",
                                "description": "锚点文本（insert_before_anchor/insert_after_anchor 必填）",
                            },
                            "anchor_occurrence": {
                                "type": "integer",
                                "default": 1,
                                "description": "anchor_text 出现多次时指定第几次出现（从1开始）",
                            },
                            "scene_index": {
                                "type": "integer",
                                "description": "目标场景索引",
                            },
                            "before_context": {
                                "type": "string",
                                "description": "目标位置前的上下文片段（10-30字，消歧用）",
                            },
                            "after_context": {
                                "type": "string",
                                "description": "目标位置后的上下文片段（10-30字，消歧用）",
                            },
                            "repair_family": {
                                "type": "string",
                                "description": "修复 family（可选）",
                            },
                            "rationale": {
                                "type": "string",
                                "description": "修复理由（可选）",
                            },
                            "fact_preservation": {
                                "type": "boolean",
                                "description": (
                                    "方案5 C2 事中标记：声明本 work_unit 是否已确认不改变既定事实"
                                    "（established_facts / iron_rules / 前文事件）。"
                                    "true=已检查不矛盾；false或省略=未声明。"
                                    "闸门②会对此字段做记录，事后验证（C3）会复核。"
                                ),
                            },
                        },
                        "required": [
                            "source_issue_ids",
                            "operation",
                            "old_text",
                            "new_text",
                        ],
                    },
                }
            },
            "required": ["work_units"],
        },
    },
}


# ---------------------------------------------------------------------------
# 确定性工具 schema（每个 metric 一个，动态生成）
# ---------------------------------------------------------------------------
def build_deterministic_tool_schemas(metrics: list[str]) -> list[dict]:
    """为给定 metrics 生成确定性工具 schema。

    每个 metric 对应一个独立的 tool，tool name 编码 metric，方便 LLM 选择。
    只有在路由表里注册过、且属于确定性可修范畴的 metric 才会生成 schema。
    """
    schemas: list[dict] = []
    seen_metrics: set[str] = set()
    for metric in metrics:
        if not metric or metric in seen_metrics:
            continue
        route = get_blueprint_route(metric)
        if route is None:
            continue
        seen_metrics.add(metric)
        schemas.append({
            "type": "function",
            "function": {
                "name": f"deterministic_tool_{metric}",
                "description": (
                    f"确定性工具：处理 metric={metric} 的问题（family={route.family}）。"
                    f"调用此工具后系统会基于路由表自动生成可执行的 patch work_unit。"
                    f"注意：只有同句全部问题都是确定性的才调本工具；同句若有需 LLM 处理的问题，"
                    f"所有同句 issue 都应 route=llm 整句交给 LLM。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_id": {
                            "type": "string",
                            "description": "要修复的 issue_id（必须在 review_results 里 route=deterministic）",
                        },
                        "scene_index": {
                            "type": "integer",
                            "description": "issue 所属场景索引",
                        },
                    },
                    "required": ["issue_id", "scene_index"],
                },
            },
        })
    return schemas


def deterministic_tool_name_for_metric(metric: str) -> str:
    """构造确定性工具名（与 build_deterministic_tool_schemas 保持一致）。"""
    return f"deterministic_tool_{metric}"


def parse_deterministic_tool_name(tool_name: str) -> str | None:
    """从 tool name 反解 metric；非确定性工具返回 None。"""
    prefix = "deterministic_tool_"
    if tool_name.startswith(prefix):
        return tool_name[len(prefix):]
    return None


__all__ = [
    "SUBMIT_REVIEW_AND_ROUTE_SCHEMA",
    "SUBMIT_WORK_UNITS_SCHEMA",
    "build_deterministic_tool_schemas",
    "deterministic_tool_name_for_metric",
    "parse_deterministic_tool_name",
]
