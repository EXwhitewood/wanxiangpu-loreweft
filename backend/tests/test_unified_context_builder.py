"""方案 2：统一上下文架构 + 缓存友好硬约束测试。

验证：
1. UnifiedContextBuilder 三个 build_xxx_context 方法返回结构正确的上下文
2. build_prompt_layers 输出 stable_prefix / semi_stable_prefix / dynamic_suffix 三层
3. stable_prefix 不含动态字段（envelope_id/created_at/task_id 等）
4. 同一内容多次构建 stable_prefix_hash 一致（缓存友好）
5. JSON 序列化稳定性（sort_keys=True）
6. 审查官复用 writer 的 stable_prefix（保证缓存命中）
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.unified_context_builder import (
    EditorContext,
    PromptLayers,
    ReviewerContext,
    UnifiedContextBuilder,
    WriterContext,
    _sha256_short,
    _stable_json_dumps,
)
from app.services.core_shell_service import CoreShellService


# 使用真实 UUID，避免 _get_recent_summaries 内部 uuid.UUID(project_id) 转换失败
_TEST_PROJECT_ID = str(uuid.uuid4())


# --------------------------------------------------------------------- Fakes


class _FakeCore:
    """伪造的 CoreMemoryService，绕过数据库。"""

    def __init__(
        self,
        rules: list[dict] | None = None,
        characters: list | None = None,
        locations: list[dict] | None = None,
        foreshadowing: list[dict] | None = None,
    ) -> None:
        self._rules = rules or []
        self._characters = characters or []
        self._locations = locations or []
        self._foreshadowing = foreshadowing or []

    async def list_world_rules(self, project_id: str) -> list[dict]:
        return list(self._rules)

    async def list_characters(self, project_id: str) -> list:
        return list(self._characters)

    async def list_locations(self, project_id: str) -> list[dict]:
        return list(self._locations)

    async def list_foreshadowing(
        self, project_id: str, include_archived: bool = False
    ) -> list[dict]:
        active_states = ("planned", "active", "dormant", "revealing", "revised")
        allowed = active_states + ("resolved",) if include_archived else active_states
        return [f for f in self._foreshadowing if f.get("status") in allowed]


class _FakeShell:
    """伪造的 ShellMemoryService。"""

    def __init__(self, seeds: list[dict] | None = None) -> None:
        self._seeds = seeds or []

    async def get_seeds_by_tier(self, project_id: str, tier=None) -> list[dict]:
        return list(self._seeds)


def _make_core_shell(
    rules: list[dict] | None = None,
    characters: list | None = None,
    locations: list[dict] | None = None,
    foreshadowing: list[dict] | None = None,
    seeds: list[dict] | None = None,
) -> CoreShellService:
    core = _FakeCore(
        rules=rules,
        characters=characters,
        locations=locations,
        foreshadowing=foreshadowing,
    )
    shell = _FakeShell(seeds=seeds)
    return CoreShellService(core_service=core, shell_service=shell)


def _make_builder(
    rules: list[dict] | None = None,
    characters: list | None = None,
    locations: list[dict] | None = None,
    foreshadowing: list[dict] | None = None,
    seeds: list[dict] | None = None,
) -> UnifiedContextBuilder:
    cs = _make_core_shell(
        rules=rules,
        characters=characters,
        locations=locations,
        foreshadowing=foreshadowing,
        seeds=seeds,
    )
    return UnifiedContextBuilder(core_shell=cs)


def _make_char(name: str):
    """构造一个伪造的 Character 对象。"""
    return SimpleNamespace(
        model_dump=lambda name=name: {
            "name": name,
            "desire": f"{name}的欲望",
            "arc": f"{name}的弧光",
        }
    )


# ----------------------------------------------------------- 上下文结构测试


@pytest.mark.asyncio
async def test_build_editor_context_returns_expected_structure():
    """主编上下文应包含 core / recent_summaries / outline / lookahead 四个字段。"""
    builder = _make_builder(
        rules=[{"name": "rule1", "priority": "high"}],
        characters=[_make_char("主角")],
    )

    ctx = await builder.build_editor_context(_TEST_PROJECT_ID, 5)

    assert isinstance(ctx, EditorContext)
    assert "world_rules" in ctx.core
    assert "characters" in ctx.core
    assert "foreshadowing" in ctx.core
    assert "items" in ctx.core
    assert isinstance(ctx.recent_summaries, list)
    assert isinstance(ctx.outline, dict)
    assert isinstance(ctx.lookahead, dict)
    # outline 占位实现应携带 chapter_number
    assert ctx.outline.get("chapter_number") == 5


@pytest.mark.asyncio
async def test_build_writer_context_returns_expected_structure():
    """writer 上下文应包含 core / shell / outline / contracts / style 五个字段。"""
    builder = _make_builder(
        characters=[_make_char("主角")],
        seeds=[{"tier": "T1", "entity_type": "fact", "chapter_number": 4}],
    )

    ctx = await builder.build_writer_context(_TEST_PROJECT_ID, 5)

    assert isinstance(ctx, WriterContext)
    assert "world_rules" in ctx.core
    assert "previous_chapter_text" in ctx.shell
    assert "recent_summaries" in ctx.shell
    assert "detail_seeds" in ctx.shell
    assert "established_facts" in ctx.shell
    assert isinstance(ctx.outline, dict)
    assert isinstance(ctx.contracts, list)
    assert isinstance(ctx.style, dict)


@pytest.mark.asyncio
async def test_build_reviewer_context_includes_writer_context_and_issues():
    """审查官上下文应嵌套 writer_context，并携带 issues 与 repair_history。"""
    builder = _make_builder(characters=[_make_char("主角")])
    issues = [{"id": "i1", "severity": "high"}]

    ctx = await builder.build_reviewer_context(
        _TEST_PROJECT_ID, 5, candidate_text="候选正文", issues=issues
    )

    assert isinstance(ctx, ReviewerContext)
    assert isinstance(ctx.writer_context, WriterContext)
    assert ctx.candidate_text == "候选正文"
    assert ctx.issues == issues
    assert isinstance(ctx.repair_history, list)


# --------------------------------------------------------- prompt 分层测试


@pytest.mark.asyncio
async def test_build_prompt_layers_outputs_three_layers_for_editor():
    """主编 prompt_layers 必须输出三层结构。"""
    builder = _make_builder(characters=[_make_char("主角")])
    ctx = await builder.build_editor_context(_TEST_PROJECT_ID, 5)

    layers = builder.build_prompt_layers(ctx)

    assert isinstance(layers, PromptLayers)
    assert layers.stable_prefix
    assert layers.semi_stable_prefix
    assert layers.dynamic_suffix
    # stable_prefix 必须包含 Core 层标记
    assert "世界观 Core 层" in layers.stable_prefix
    # semi_stable_prefix 必须包含前文摘要/章节大纲
    assert "前文摘要" in layers.semi_stable_prefix
    assert "章节大纲" in layers.semi_stable_prefix
    # dynamic_suffix 必须包含动态指令
    assert "动态指令" in layers.dynamic_suffix


@pytest.mark.asyncio
async def test_build_prompt_layers_outputs_three_layers_for_writer():
    """writer prompt_layers 必须输出三层结构。"""
    builder = _make_builder(characters=[_make_char("主角")])
    ctx = await builder.build_writer_context(_TEST_PROJECT_ID, 5)

    layers = builder.build_prompt_layers(ctx)

    # writer 的 stable_prefix 包含 Core 层 + 风格约束
    assert "世界观 Core 层" in layers.stable_prefix
    assert "风格约束" in layers.stable_prefix
    # semi_stable 包含上一章原文 + 前文摘要 + 大纲
    assert "上一章完整原文" in layers.semi_stable_prefix
    assert "前文摘要" in layers.semi_stable_prefix
    # dynamic_suffix 包含场景合同 + Detail Seeds + 已确立事实
    assert "场景合同" in layers.dynamic_suffix
    assert "Detail Seeds" in layers.dynamic_suffix
    assert "已确立事实" in layers.dynamic_suffix


@pytest.mark.asyncio
async def test_build_prompt_layers_for_reviewer_reuses_writer_stable_prefix():
    """审查官 stable_prefix 必须复用 writer 的 stable_prefix（保证缓存命中）。"""
    builder = _make_builder(characters=[_make_char("主角")])
    writer_ctx = await builder.build_writer_context(_TEST_PROJECT_ID, 5)
    reviewer_ctx = await builder.build_reviewer_context(
        _TEST_PROJECT_ID, 5, candidate_text="候选正文", issues=[{"id": "i1"}]
    )

    writer_layers = builder.build_prompt_layers(writer_ctx)
    reviewer_layers = builder.build_prompt_layers(reviewer_ctx)

    # stable_prefix 与 semi_stable_prefix 必须完全一致（缓存命中前提）
    assert writer_layers.stable_prefix == reviewer_layers.stable_prefix
    assert writer_layers.semi_stable_prefix == reviewer_layers.semi_stable_prefix
    # dynamic_suffix 必须不同（审查官多出候选正文/Issue/修复历史）
    assert writer_layers.dynamic_suffix != reviewer_layers.dynamic_suffix
    assert "候选正文" in reviewer_layers.dynamic_suffix
    assert "Issue 列表" in reviewer_layers.dynamic_suffix
    assert "修复历史" in reviewer_layers.dynamic_suffix


@pytest.mark.asyncio
async def test_build_prompt_layers_raises_on_unsupported_context_type():
    """不支持上下文类型应抛出 TypeError。"""
    builder = _make_builder()

    with pytest.raises(TypeError, match="Unsupported context type"):
        builder.build_prompt_layers(object())


# ----------------------------------------------------- 缓存友好硬约束测试


@pytest.mark.asyncio
async def test_stable_prefix_excludes_dynamic_fields():
    """stable_prefix 严禁包含 envelope_id / created_at / task_id / trace_id 等动态字段。"""
    builder = _make_builder(characters=[_make_char("主角")])
    ctx = await builder.build_writer_context(_TEST_PROJECT_ID, 5)

    layers = builder.build_prompt_layers(ctx)

    forbidden = [
        "envelope_id",
        "created_at",
        "task_id",
        "trace_id",
        "execution_round",
        "attempt_count",
    ]
    for field in forbidden:
        assert field not in layers.stable_prefix, (
            f"stable_prefix 严禁包含动态字段 {field}"
        )
        assert field not in layers.semi_stable_prefix, (
            f"semi_stable_prefix 严禁包含动态字段 {field}"
        )


@pytest.mark.asyncio
async def test_stable_prefix_hash_is_deterministic_for_same_content():
    """同一内容多次构建，stable_prefix_hash 必须一致（缓存友好的核心保证）。"""
    builder = _make_builder(
        rules=[{"name": "rule1", "priority": "high"}],
        characters=[_make_char("主角"), _make_char("配角")],
    )

    ctx1 = await builder.build_writer_context(_TEST_PROJECT_ID, 5)
    ctx2 = await builder.build_writer_context(_TEST_PROJECT_ID, 5)

    layers1 = builder.build_prompt_layers(ctx1)
    layers2 = builder.build_prompt_layers(ctx2)

    assert layers1.stable_prefix == layers2.stable_prefix
    assert layers1.block_hashes() == layers2.block_hashes()
    assert layers1.block_hashes()["stable_prefix_hash"] == layers2.block_hashes()["stable_prefix_hash"]


@pytest.mark.asyncio
async def test_stable_prefix_hash_changes_when_core_changes():
    """Core 内容变化时 stable_prefix_hash 必须变化（避免错误缓存命中）。"""
    builder_v1 = _make_builder(characters=[_make_char("主角")])
    builder_v2 = _make_builder(characters=[_make_char("主角"), _make_char("新角色")])

    ctx1 = await builder_v1.build_writer_context(_TEST_PROJECT_ID, 5)
    ctx2 = await builder_v2.build_writer_context(_TEST_PROJECT_ID, 5)

    hash1 = builder_v1.build_prompt_layers(ctx1).block_hashes()["stable_prefix_hash"]
    hash2 = builder_v2.build_prompt_layers(ctx2).block_hashes()["stable_prefix_hash"]

    assert hash1 != hash2


@pytest.mark.asyncio
async def test_stable_prefix_independent_of_dynamic_chapter_changes():
    """不同章节号 stable_prefix 必须相同（Core 跨章节稳定）。"""
    builder = _make_builder(characters=[_make_char("主角")])

    ctx_ch5 = await builder.build_writer_context(_TEST_PROJECT_ID, 5)
    ctx_ch10 = await builder.build_writer_context(_TEST_PROJECT_ID, 10)

    layers_ch5 = builder.build_prompt_layers(ctx_ch5)
    layers_ch10 = builder.build_prompt_layers(ctx_ch10)

    # stable_prefix 跨章节稳定（Core + style 不变）
    assert layers_ch5.stable_prefix == layers_ch10.stable_prefix
    # semi_stable_prefix 应随章节变化（前文摘要/上一章原文不同）
    assert layers_ch5.semi_stable_prefix != layers_ch10.semi_stable_prefix


# ------------------------------------------------------- JSON 序列化稳定性


def test_stable_json_dumps_sorts_keys():
    """_stable_json_dumps 必须按 key 排序输出，保证字面稳定。"""
    data_a = {"b": 2, "a": 1, "c": 3}
    data_b = {"c": 3, "a": 1, "b": 2}

    assert _stable_json_dumps(data_a) == _stable_json_dumps(data_b)


def test_stable_json_dumps_handles_nested_structures():
    """嵌套结构也必须按 key 排序。"""
    data_a = {"outer": {"z": 1, "a": 2}, "list": [3, 1, 2]}
    data_b = {"list": [3, 1, 2], "outer": {"a": 2, "z": 1}}

    assert _stable_json_dumps(data_a) == _stable_json_dumps(data_b)


def test_sha256_short_returns_16_chars():
    """_sha256_short 必须返回 16 位 hex 字符串。"""
    h = _sha256_short("hello")
    assert len(h) == 16
    # 同一输入多次调用结果一致
    assert _sha256_short("hello") == h
    # 不同输入结果不同
    assert _sha256_short("world") != h


@pytest.mark.asyncio
async def test_block_hashes_returns_three_hashes():
    """block_hashes 必须返回三个 16 位 hash。"""
    builder = _make_builder(characters=[_make_char("主角")])
    ctx = await builder.build_writer_context(_TEST_PROJECT_ID, 5)

    layers = builder.build_prompt_layers(ctx)
    hashes = layers.block_hashes()

    assert set(hashes.keys()) == {
        "stable_prefix_hash",
        "semi_stable_hash",
        "dynamic_suffix_hash",
    }
    for key, value in hashes.items():
        assert len(value) == 16, f"{key} 必须是 16 位 hash"
    # 三个 hash 应各不相同
    assert len(set(hashes.values())) == 3


# ------------------------------------------------------- Core 格式化稳定性


@pytest.mark.asyncio
async def test_format_core_stable_sorts_lists_by_name():
    """_format_core_stable 必须按 name 排序列表，保证字面稳定。"""
    builder = _make_builder(
        characters=[_make_char("乙"), _make_char("甲"), _make_char("丙")],
        locations=[{"name": "Z城"}, {"name": "A城"}, {"name": "M城"}],
    )
    ctx = await builder.build_writer_context(_TEST_PROJECT_ID, 5)
    layers_a = builder.build_prompt_layers(ctx)

    # 重新构造，顺序不同
    builder_b = _make_builder(
        characters=[_make_char("丙"), _make_char("甲"), _make_char("乙")],
        locations=[{"name": "A城"}, {"name": "Z城"}, {"name": "M城"}],
    )
    ctx_b = await builder_b.build_writer_context(_TEST_PROJECT_ID, 5)
    layers_b = builder_b.build_prompt_layers(ctx_b)

    # 排序后字面应一致
    assert layers_a.stable_prefix == layers_b.stable_prefix


def test_prompt_layers_to_dict_returns_four_keys():
    """PromptLayers.to_dict 必须返回四个 key（含 block_hashes，P2-1）。"""
    layers = PromptLayers(
        stable_prefix="s", semi_stable_prefix="m", dynamic_suffix="d"
    )
    d = layers.to_dict()
    assert set(d.keys()) == {"stable_prefix", "semi_stable_prefix", "dynamic_suffix", "block_hashes"}
    assert d["stable_prefix"] == "s"
    assert d["semi_stable_prefix"] == "m"
    assert d["dynamic_suffix"] == "d"
    assert set(d["block_hashes"].keys()) == {"stable_prefix_hash", "semi_stable_hash", "dynamic_suffix_hash"}
