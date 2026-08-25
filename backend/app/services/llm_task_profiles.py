"""LLM 任务类型预设（方案 29）。

按任务类型集中管理 max_tokens/timeout/max_retries 参数，
避免各调用方散乱硬编码导致推理模型路由后必失败。

使用方式：
    from app.services.llm_task_profiles import LLMTaskType, get_profile

    profile = get_profile(LLMTaskType.REPAIR_WITH_TOOLS)
    # profile.max_tokens=65536, profile.timeout=120, profile.max_retries=3

    result = await llm_client.generate_with_tools(
        messages=messages,
        tools=tools,
        temperature=0.2,
        task_type=LLMTaskType.REPAIR_WITH_TOOLS,
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LLMTaskType(str, Enum):
    """LLM 任务类型，决定 max_tokens/timeout/retry 预设。"""

    # 章节生成，长输出
    CHAPTER_GENERATION = "chapter_generation"
    # 主编规划/编排 + 工具调用
    EDITOR_PLANNING = "editor_planning"
    # 大纲师生成 + 工具调用
    OUTLINE_GENERATION = "outline_generation"
    # 世界观师 + 工具调用
    WORLDBUILDER_WITH_TOOLS = "worldbuilder_with_tools"
    # FBI 蓝图 + 工具调用
    BLUEPRINT_WITH_TOOLS = "blueprint_with_tools"
    # FBI 修复 + 工具调用
    REPAIR_WITH_TOOLS = "repair_with_tools"
    # FBI 修复 agent（fact/pacing/voice/prose/style/ending/length/budget_arbiter）
    FBI_REPAIR = "fbi_repair"
    # JSON 检测/分类（FCIP、伏笔检测等）
    JSON_DETECTION = "json_detection"
    # JSON 审查（expert_* 系列）
    JSON_AUDIT = "json_audit"
    # 短文本抽取（style_learner 等）
    SHORT_EXTRACTION = "short_extraction"
    # 章节摘要
    CHAPTER_SUMMARY = "chapter_summary"
    # 连通性测试
    CONNECTIVITY_TEST = "connectivity_test"


@dataclass(frozen=True)
class TaskProfile:
    """任务参数预设。"""

    max_tokens: int
    timeout: float
    max_retries: int


# 预设表：推理模型（如 DeepSeek-R1）的推理阶段会消耗大量 token，
# 因此 max_tokens 普遍较大；timeout 留足推理时间。
TASK_PROFILES: dict[LLMTaskType, TaskProfile] = {
    LLMTaskType.CHAPTER_GENERATION: TaskProfile(
        max_tokens=65536, timeout=300, max_retries=2,
    ),
    # 主编规划/编排 + 工具调用：长输出 + 推理
    LLMTaskType.EDITOR_PLANNING: TaskProfile(
        max_tokens=65536, timeout=300, max_retries=1,
    ),
    # 大纲师生成 + 工具调用：长输出 + 推理
    LLMTaskType.OUTLINE_GENERATION: TaskProfile(
        max_tokens=65536, timeout=300, max_retries=1,
    ),
    # 世界观师 + 工具调用：长输出 + 推理
    LLMTaskType.WORLDBUILDER_WITH_TOOLS: TaskProfile(
        max_tokens=65536, timeout=300, max_retries=1,
    ),
    LLMTaskType.BLUEPRINT_WITH_TOOLS: TaskProfile(
        max_tokens=65536, timeout=300, max_retries=1,
    ),
    LLMTaskType.REPAIR_WITH_TOOLS: TaskProfile(
        max_tokens=65536, timeout=300, max_retries=1,
    ),
    # FBI repair agents return one scene/window candidate plus a compact audit.
    # A 65K ceiling lets structured repair calls spend chapter-sized output on
    # tiny patches; 8K still safely covers a full scene rewrite and JSON wrapper.
    LLMTaskType.FBI_REPAIR: TaskProfile(
        max_tokens=8192, timeout=180, max_retries=1,
    ),
    # Detection/audit schemas are compact and should never emit long prose.
    LLMTaskType.JSON_DETECTION: TaskProfile(
        max_tokens=4096, timeout=90, max_retries=2,
    ),
    LLMTaskType.JSON_AUDIT: TaskProfile(
        max_tokens=4096, timeout=90, max_retries=2,
    ),
    # Short extraction outputs are bounded lists/cards, not scene prose.
    LLMTaskType.SHORT_EXTRACTION: TaskProfile(
        max_tokens=2048, timeout=60, max_retries=1,
    ),
    LLMTaskType.CHAPTER_SUMMARY: TaskProfile(
        max_tokens=4096, timeout=90, max_retries=1,
    ),
    LLMTaskType.CONNECTIVITY_TEST: TaskProfile(
        max_tokens=5, timeout=30, max_retries=0,
    ),
}


def get_profile(task_type: LLMTaskType) -> TaskProfile:
    """获取任务预设。未知类型回退到 JSON_DETECTION。"""
    return TASK_PROFILES.get(task_type, TASK_PROFILES[LLMTaskType.JSON_DETECTION])
