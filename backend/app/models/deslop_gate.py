"""去AI味 Gate 化修复数据模型。

去AI味只改"怎么说"，不改"说什么"。
受保护跨度包括伏笔、钩子、人物状态、事实命题、结尾合同。
删除比例必须有上限。

Gate 分级：
  A = 禁用词/高频AI词
  B = 句式套路
  C = 心理告知/解释腔
  D = 段落节奏均匀
  E = 对话标签与人物声音同质
  F = 章末总结/升华腔
  G = 夸大意义/宣传性语言
  H = 填充短语/模糊归因
  I = 明喻过度
  J = 通感公式
  K = 虚假代理
  L = 模板承接（记忆回溯类）
  M = 重复句（完全相同或高度相似的句子重复出现）
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class DeslopGateReport(BaseModel):
    """Gate 检测报告"""
    gate: Literal["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"] = Field(
        default="A",
        description="Gate: A=禁用词/高频AI词, B=句式套路, C=心理告知/解释腔, D=段落节奏均匀, E=对话标签与人物声音同质, F=章末总结/升华腔, G=夸大意义/宣传性语言, H=填充短语/模糊归因, I=明喻过度, J=通感公式, K=虚假代理, L=模板承接（记忆回溯类）, M=重复句"
    )
    severity: Literal["critical", "high", "medium", "low"] = Field(default="medium", description="严重程度")
    evidence_spans: list[str] = Field(default_factory=list, description="证据文本片段")
    repair_scope: Literal["word", "phrase", "sentence", "paragraph"] = Field(default="phrase", description="修复范围")
    deletion_limit: float = Field(default=0.3, description="删除比例上限")
    protected_spans: list[str] = Field(default_factory=list, description="受保护的文本跨度")
    whitelist_hits: list[str] = Field(default_factory=list, description="白名单命中")


class DeslopRepairPlan(BaseModel):
    """去AI味修复计划"""
    gates_to_apply: list[Literal["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"]] = Field(default_factory=list, description="要应用的Gate列表")
    max_delete_ratio: float = Field(default=0.3, description="最大删除比例")
    preserve_plot_functions: bool = Field(default=True, description="保护剧情功能句")
    patch_strategy: Literal["replace", "rephrase", "delete", "restructure"] = Field(default="rephrase", description="修复策略")


class DeslopResult(BaseModel):
    """去AI味结果"""
    gate_reports: list[DeslopGateReport] = Field(default_factory=list, description="Gate检测报告")
    repair_plan: DeslopRepairPlan | None = Field(default=None, description="修复计划")
    original_text: str = Field(default="", description="原始文本")
    repaired_text: str = Field(default="", description="修复后文本")
    total_issues: int = Field(default=0, description="总问题数")
    fixed_issues: int = Field(default=0, description="已修复问题数")
    deletion_ratio: float = Field(default=0.0, description="实际删除比例")


# Gate 描述映射
GATE_DESCRIPTIONS: dict[str, str] = {
    "A": "禁用词/高频AI词",
    "B": "句式套路",
    "C": "心理告知/解释腔",
    "D": "段落节奏均匀",
    "E": "对话标签与人物声音同质",
    "F": "章末总结/升华腔",
    "G": "夸大意义/宣传性语言",
    "H": "填充短语/模糊归因",
    "I": "明喻过度",
    "J": "通感公式",
    "K": "虚假代理",
    "L": "模板承接（记忆回溯类）",
    "M": "重复句",
}

# Gate 与现有系统的对应
GATE_CHECKER_MAP: dict[str, str] = {
    "A": "ai_flavor_checker",
    "B": "expression_variety_checker",
    "C": "narrative_experience_checker",
    "D": "literary_quality_checker",
    "E": "character_voice_checker",
    "F": "commercial_pacing_checker",
    "G": "ai_flavor_checker",
    "H": "ai_flavor_checker",
    "I": "ai_flavor_checker",
    "J": "ai_flavor_checker",
    "K": "ai_flavor_checker",
    "L": "ai_flavor_checker",
    "M": "ai_flavor_checker",
}

# Gate 与修复策略的对应
GATE_REPAIR_MAP: dict[str, str] = {
    "A": "patch_text",
    "B": "patch_literary_quality",
    "C": "patch_literary_quality",
    "D": "patch_literary_quality",
    "E": "patch_text",
    "F": "patch_literary_quality",
    "G": "patch_text",
    "H": "patch_text",
    "I": "patch_text",
    "J": "patch_text",
    "K": "patch_text",
    "L": "patch_text",
    "M": "patch_text",
}
