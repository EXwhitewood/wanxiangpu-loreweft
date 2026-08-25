"""Generic provenance contract for narrative evidence introduced by writers."""
from __future__ import annotations

from typing import Any


DEFAULT_EVIDENCE_CARRIERS = [
    "信件",
    "纸条",
    "留言",
    "刻字",
    "碑文",
    "地图",
    "玉佩",
    "钥匙",
    "药方",
    "传信",
    "暗号",
    "证词",
    "记忆碎片",
    "遗留物",
    "证据物",
]

DEFAULT_REQUIRED_FIELDS = ["source", "acquisition_time", "causal_link"]


def build_evidence_provenance_contract(scene_contract: dict | None = None) -> dict[str, Any]:
    """Return a writer-facing hard contract for new plot-moving evidence.

    The contract is intentionally domain-neutral. It does not decide who placed
    a clue or what the clue means; it only forbids introducing action-driving
    evidence without visible provenance.
    """
    scene_contract = scene_contract if isinstance(scene_contract, dict) else {}
    existing = scene_contract.get("evidence_provenance_contract")
    if isinstance(existing, dict) and existing:
        return {
            **_base_contract(),
            **existing,
            "carriers": _dedupe([*DEFAULT_EVIDENCE_CARRIERS, *(_as_list(existing.get("carriers")))]),
            "required_fields": _dedupe([*DEFAULT_REQUIRED_FIELDS, *(_as_list(existing.get("required_fields")))]),
        }
    return _base_contract()


def render_evidence_provenance_rules(contract: dict | None = None) -> list[str]:
    contract = contract if isinstance(contract, dict) else build_evidence_provenance_contract()
    carriers = "、".join(str(item) for item in _as_list(contract.get("carriers"))[:12])
    required = "、".join(str(item) for item in _as_list(contract.get("required_fields"))[:6])
    examples = "；".join(str(item) for item in _as_list(contract.get("forbidden_shortcuts"))[:3])
    return [
        "证据来源硬约束：任何推动行动、解谜、身份判断、地点转移、关系反转或伏笔推进的证据载体，首次出现时必须在正文可见范围内交代来源。",
        f"证据载体包括但不限于：{carriers}。",
        f"新证据必须同时具备：{required}；若前文或合同没有建立来源，不得用“从袖中/怀中/靴中取出”等方式直接引入关键证据。",
        f"禁止偷渡方式：{examples}。",
        "允许写未知谜团，但不允许让关键证据凭空出现；可以不知道幕后真相，但必须知道证据是如何到达角色手中的。",
    ]


def _base_contract() -> dict[str, Any]:
    return {
        "rule": "no_unprovenanced_evidence",
        "blocking_if_missing": True,
        "same_scene_requirement": True,
        "applies_to": [
            "plot_action_trigger",
            "mystery_solution",
            "identity_inference",
            "location_transition",
            "relationship_reversal",
            "foreshadowing_operation",
        ],
        "carriers": list(DEFAULT_EVIDENCE_CARRIERS),
        "required_fields": list(DEFAULT_REQUIRED_FIELDS),
        "forbidden_shortcuts": [
            "角色突然从袖中、怀中、靴中取出此前未建立的关键证据",
            "正文直接给出暗号、纸条、地图、遗留物，却不说明获得时机",
            "用证据推动场景转向，却不说明放置者、传递者、发现位置或可信连接",
        ],
        "writer_instruction": (
            "如果你需要新增推动剧情的证据，请先写清它如何被发现、由谁留下/传递、"
            "为什么角色能据此采取下一步行动。"
        ),
    }


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, tuple | set):
        return [item for item in value if item not in (None, "")]
    return [value]


def _dedupe(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = str(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
