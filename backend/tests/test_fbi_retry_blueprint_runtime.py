"""T5: 新会话重出图入口测试。

验证 RetryBlueprintRuntime 的核心逻辑：
- 闸门②校验（operation 合法性、source_issue_ids 非空、字段完整性、覆盖完整性）
- 递归重试上限（MAX_RETRY_ROUNDS=2）
- blueprint_source 标记
- LLM 失败时递归到下一轮
- max_retry_exceeded 终止条件
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.fbi.retry_blueprint_runtime import (
    MAX_RETRY_ROUNDS,
    RetryBlueprintRuntime,
)


class _MockLLMClient:
    """Mock LLM client，返回预设的 tool_calls。"""

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = list(responses)
        self._call_count = 0

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        task_type: Any = None,
    ) -> dict:
        if self._call_count >= len(self._responses):
            return {"has_tool_calls": False, "content": "no more responses"}
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


def _make_submit_work_units_response(work_units_data: list[dict]) -> dict:
    """构造一个 submit_work_units tool_call 响应。"""
    return {
        "has_tool_calls": True,
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "submit_work_units",
                    "arguments": json.dumps({"work_units": work_units_data}),
                },
            }
        ],
        "assistant_message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "submit_work_units",
                        "arguments": json.dumps({"work_units": work_units_data}),
                    },
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_retry_returns_ready_when_gate2_passes():
    """闸门②通过 → status=ready，返回 work_units。"""
    work_units_data = [
        {
            "operation": "replace_exact",
            "scene_index": 0,
            "old_text": "布袋",
            "new_text": "药袋",
            "target_span": "布袋",
            "anchor_text": "布袋",
            "source_issue_ids": ["iss-1"],
        }
    ]
    mock_client = _MockLLMClient([_make_submit_work_units_response(work_units_data)])

    runtime = RetryBlueprintRuntime()
    context = {
        "violations": [{"issue_id": "iss-1", "type": "anti_ai", "source_scene": 0}],
        "scene_texts": {0: "她低头确认布袋还在。"},
        "_llm_client": mock_client,
    }

    result = await runtime.retry(
        failed_issue_ids=["iss-1"],
        failure_reason="old_text_not_found",
        retry_round=1,
        context=context,
    )

    assert result["status"] == "ready"
    assert len(result["work_units"]) == 1
    wu = result["work_units"][0]
    assert wu.blueprint_source == "fbi_retry_blueprint[retry:round1]"
    assert wu.source_violation_ids == ["iss-1"]
    assert result["trace"]["retry_round"] == 1


@pytest.mark.asyncio
async def test_retry_recurses_on_gate2_failure():
    """闸门②失败 → 递归到下一轮，直到成功。"""
    # 第1轮：空 work_units（闸门②失败）
    # 第2轮：成功
    mock_client = _MockLLMClient([
        _make_submit_work_units_response([]),
        _make_submit_work_units_response([
            {
                "operation": "replace_exact",
                "scene_index": 0,
                "old_text": "布袋",
                "new_text": "药袋",
                "source_issue_ids": ["iss-1"],
            }
        ]),
    ])

    runtime = RetryBlueprintRuntime()
    context = {
        "violations": [{"issue_id": "iss-1", "source_scene": 0}],
        "scene_texts": {0: "布袋还在。"},
        "_llm_client": mock_client,
    }

    result = await runtime.retry(
        failed_issue_ids=["iss-1"],
        failure_reason="recheck_failed",
        retry_round=1,
        context=context,
    )

    assert result["status"] == "ready"
    assert result["trace"]["retry_round"] == 2


@pytest.mark.asyncio
async def test_retry_returns_failed_when_max_rounds_exceeded():
    """超过 MAX_RETRY_ROUNDS → status=failed, reason=max_retry_exceeded。"""
    # 所有轮次都返回空 work_units
    mock_client = _MockLLMClient([
        _make_submit_work_units_response([]),
        _make_submit_work_units_response([]),
    ])

    runtime = RetryBlueprintRuntime()
    context = {
        "violations": [{"issue_id": "iss-1", "source_scene": 0}],
        "scene_texts": {0: "布袋。"},
        "_llm_client": mock_client,
    }

    result = await runtime.retry(
        failed_issue_ids=["iss-1"],
        failure_reason="old_text_not_found",
        retry_round=1,
        context=context,
    )

    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_retry_rejects_invalid_operation():
    """闸门②-1: operation 不在允许集合内 → 该 work_unit 被拒绝。"""
    work_units_data = [
        {
            "operation": "invalid_op",  # 不允许
            "scene_index": 0,
            "old_text": "布袋",
            "new_text": "药袋",
            "source_issue_ids": ["iss-1"],
        }
    ]
    # 第1轮失败（invalid_op），第2轮也失败（空），最终 failed
    mock_client = _MockLLMClient([
        _make_submit_work_units_response(work_units_data),
        _make_submit_work_units_response([]),
    ])

    runtime = RetryBlueprintRuntime()
    context = {
        "violations": [{"issue_id": "iss-1", "source_scene": 0}],
        "scene_texts": {0: "布袋。"},
        "_llm_client": mock_client,
    }

    result = await runtime.retry(
        failed_issue_ids=["iss-1"],
        failure_reason="test",
        retry_round=1,
        context=context,
    )

    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_retry_rejects_uncovered_issue_ids():
    """闸门②-4: 覆盖完整性 — 未覆盖的 issue_id 导致校验失败。"""
    work_units_data = [
        {
            "operation": "replace_exact",
            "scene_index": 0,
            "old_text": "布袋",
            "new_text": "药袋",
            "source_issue_ids": ["iss-1"],  # 只覆盖 iss-1，未覆盖 iss-2
        }
    ]
    mock_client = _MockLLMClient([
        _make_submit_work_units_response(work_units_data),
        _make_submit_work_units_response([]),
    ])

    runtime = RetryBlueprintRuntime()
    context = {
        "violations": [
            {"issue_id": "iss-1", "source_scene": 0},
            {"issue_id": "iss-2", "source_scene": 0},
        ],
        "scene_texts": {0: "布袋还在。"},
        "_llm_client": mock_client,
    }

    result = await runtime.retry(
        failed_issue_ids=["iss-1", "iss-2"],  # 需要覆盖两个
        failure_reason="test",
        retry_round=1,
        context=context,
    )

    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_retry_rejects_missing_source_issue_ids():
    """闸门②-2: source_issue_ids 为空 → 该 work_unit 被拒绝。"""
    work_units_data = [
        {
            "operation": "replace_exact",
            "scene_index": 0,
            "old_text": "布袋",
            "new_text": "药袋",
            "source_issue_ids": [],  # 空
        }
    ]
    mock_client = _MockLLMClient([
        _make_submit_work_units_response(work_units_data),
        _make_submit_work_units_response([]),
    ])

    runtime = RetryBlueprintRuntime()
    context = {
        "violations": [{"issue_id": "iss-1", "source_scene": 0}],
        "scene_texts": {0: "布袋。"},
        "_llm_client": mock_client,
    }

    result = await runtime.retry(
        failed_issue_ids=["iss-1"],
        failure_reason="test",
        retry_round=1,
        context=context,
    )

    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_retry_no_tool_call_returns_failed():
    """LLM 没调 tool → 闸门②失败，递归到下一轮。"""
    mock_client = _MockLLMClient([
        {"has_tool_calls": False, "content": "I cannot help"},
        {"has_tool_calls": False, "content": "still no tool"},
    ])

    runtime = RetryBlueprintRuntime()
    context = {
        "violations": [{"issue_id": "iss-1", "source_scene": 0}],
        "scene_texts": {0: "布袋。"},
        "_llm_client": mock_client,
    }

    result = await runtime.retry(
        failed_issue_ids=["iss-1"],
        failure_reason="test",
        retry_round=1,
        context=context,
    )

    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_retry_immediate_max_rounds_exceeded():
    """retry_round > MAX_RETRY_ROUNDS → 立即返回 failed，不调 LLM。"""
    mock_client = _MockLLMClient([])  # 没有响应，不应该被调用

    runtime = RetryBlueprintRuntime()
    context = {
        "violations": [{"issue_id": "iss-1"}],
        "scene_texts": {0: "text"},
        "_llm_client": mock_client,
    }

    result = await runtime.retry(
        failed_issue_ids=["iss-1"],
        failure_reason="test",
        retry_round=MAX_RETRY_ROUNDS + 1,
        context=context,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "max_retry_exceeded"
    assert mock_client._call_count == 0  # LLM 未被调用


@pytest.mark.asyncio
async def test_retry_infers_scene_index_from_violation():
    """scene_index 未提供时，从 violation.source_scene 推断。"""
    work_units_data = [
        {
            "operation": "replace_exact",
            "old_text": "布袋",
            "new_text": "药袋",
            "source_issue_ids": ["iss-1"],
            # 故意不提供 scene_index
        }
    ]
    mock_client = _MockLLMClient([_make_submit_work_units_response(work_units_data)])

    runtime = RetryBlueprintRuntime()
    context = {
        "violations": [{"issue_id": "iss-1", "source_scene": 2}],
        "scene_texts": {0: "a", 1: "b", 2: "布袋还在。"},
        "_llm_client": mock_client,
    }

    result = await runtime.retry(
        failed_issue_ids=["iss-1"],
        failure_reason="test",
        retry_round=1,
        context=context,
    )

    assert result["status"] == "ready"
    wu = result["work_units"][0]
    assert wu.owner_scene == 2
    assert wu.target_scenes == [2]


@pytest.mark.asyncio
async def test_retry_handles_llm_exception():
    """LLM 调用抛异常 → 当作失败，递归到下一轮。"""
    class _ExceptionClient:
        async def generate_with_tools(self, **kwargs):
            raise RuntimeError("LLM service unavailable")

    runtime = RetryBlueprintRuntime()
    context = {
        "violations": [{"issue_id": "iss-1", "source_scene": 0}],
        "scene_texts": {0: "布袋。"},
        "_llm_client": _ExceptionClient(),
    }

    result = await runtime.retry(
        failed_issue_ids=["iss-1"],
        failure_reason="test",
        retry_round=1,
        context=context,
    )

    assert result["status"] == "failed"
