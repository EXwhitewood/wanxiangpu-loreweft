from __future__ import annotations

import json

import httpx
import pytest

from app.api import settings as settings_api
from app.services.llm_client import LLMClient


def _client(api_format: str = "anthropic_compatible") -> LLMClient:
    return LLMClient(
        api_format=api_format,  # type: ignore[arg-type]
        api_key="test-key",
        base_url="https://api.example.com",
        model="test-model",
    )


def _mock_sdk_http(monkeypatch, handler):
    monkeypatch.setattr(
        LLMClient,
        "_http_client",
        staticmethod(lambda timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
        )),
    )


def test_anthropic_message_conversion_preserves_tool_blocks():
    client = _client()

    system, messages = client._convert_to_anthropic_messages([
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "请查询"},
        {
            "role": "assistant",
            "content": "我来查一下。",
            "tool_calls": [
                {
                    "id": "tool-1",
                    "type": "function",
                    "function": {
                        "name": "get_outline",
                        "arguments": "{\"chapter\": 3}",
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tool-1", "content": "{\"title\": \"第三章\"}"},
    ])

    assert system == "系统提示"
    assert messages[0] == {"role": "user", "content": "请查询"}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == [
        {"type": "text", "text": "我来查一下。"},
        {
            "type": "tool_use",
            "id": "tool-1",
            "name": "get_outline",
            "input": {"chapter": 3},
        },
    ]
    assert messages[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": "{\"title\": \"第三章\"}",
            }
        ],
    }


def test_anthropic_tool_conversion_uses_function_schema():
    client = _client()

    tools = client._convert_to_anthropic_tools([
        {
            "type": "function",
            "function": {
                "name": "search_memory",
                "description": "搜索记忆",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    ])

    assert tools == [
        {
            "name": "search_memory",
            "description": "搜索记忆",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_openai_generate_uses_sdk_and_normalizes_base_url(monkeypatch):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append({
            "url": str(request.url),
            "body": json.loads(request.content),
        })
        return httpx.Response(200, json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "生成结果"},
                    "finish_reason": "stop",
                }
            ],
        })

    _mock_sdk_http(monkeypatch, handler)
    client = _client("openai_compatible")

    result = await client.generate(
        system_prompt="系统",
        user_prompt="用户",
        temperature=0.2,
        max_tokens=64,
        response_format={"type": "json_object"},
    )

    assert result == "生成结果"
    assert requests[0]["url"] == "https://api.example.com/v1/chat/completions"
    assert requests[0]["body"]["model"] == "test-model"
    assert requests[0]["body"]["messages"] == [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "用户"},
    ]
    assert requests[0]["body"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_generate_with_tools_preserves_response_shape(monkeypatch):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "chatcmpl-tools",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "先查询记忆。",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "search_memory",
                                    "arguments": "{\"query\": \"线索\"}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        })

    _mock_sdk_http(monkeypatch, handler)
    client = _client("openai_compatible")

    result = await client.generate_with_tools(
        messages=[{"role": "system", "content": "系统"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search_memory",
                    "description": "搜索记忆",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert requests[0]["messages"] == [
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "请继续。"},
    ]
    assert result["has_tool_calls"] is True
    assert result["tool_calls"] == [
        {
            "id": "call-1",
            "function": {
                "name": "search_memory",
                "arguments": "{\"query\": \"线索\"}",
            },
        }
    ]
    assert result["assistant_message"]["reasoning_content"] == "先查询记忆。"


@pytest.mark.asyncio
async def test_openai_stream_uses_sdk_stream_parser(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.example.com/v1/chat/completions"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"id":"chunk-1","object":"chat.completion.chunk","created":0,'
                '"model":"test-model","choices":[{"index":0,"delta":{"content":"你"},'
                '"finish_reason":null}]}\n\n'
                'data: {"id":"chunk-2","object":"chat.completion.chunk","created":0,'
                '"model":"test-model","choices":[{"index":0,"delta":{"content":"好"},'
                '"finish_reason":null}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    _mock_sdk_http(monkeypatch, handler)
    client = _client("openai_compatible")

    chunks = [chunk async for chunk in client.generate_stream("系统", "用户")]

    assert chunks == ["你", "好"]


@pytest.mark.asyncio
async def test_anthropic_generate_strips_v1_base_url_for_sdk(monkeypatch):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append({
            "url": str(request.url),
            "body": json.loads(request.content),
        })
        return httpx.Response(200, json={
            "id": "msg-test",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "content": [{"type": "text", "text": "生成结果"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    _mock_sdk_http(monkeypatch, handler)
    client = LLMClient(
        api_format="anthropic_compatible",
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="test-model",
    )

    result = await client.generate("系统", "用户", temperature=0.3, max_tokens=64)

    assert result == "生成结果"
    assert requests[0]["url"] == "https://api.example.com/v1/messages"
    assert requests[0]["body"]["system"] == "系统"
    assert requests[0]["body"]["messages"] == [{"role": "user", "content": "用户"}]


@pytest.mark.asyncio
async def test_anthropic_generate_with_tools_uses_sdk_tool_result(monkeypatch):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg-tools",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "content": [
                {"type": "text", "text": "需要查询。"},
                {
                    "type": "tool_use",
                    "id": "tool-2",
                    "name": "search_memory",
                    "input": {"query": "线索"},
                },
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    _mock_sdk_http(monkeypatch, handler)
    client = _client("anthropic_compatible")

    result = await client.generate_with_tools(
        messages=[
            {"role": "system", "content": "系统"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "tool-1",
                        "type": "function",
                        "function": {
                            "name": "search_memory",
                            "arguments": "{\"query\": \"旧线索\"}",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tool-1", "content": "{\"result\": \"旧线索\"}"},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search_memory",
                    "description": "搜索记忆",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert requests[0]["messages"][0]["content"][0]["type"] == "tool_use"
    assert requests[0]["messages"][1]["content"][0]["type"] == "tool_result"
    assert requests[0]["tools"] == [
        {
            "name": "search_memory",
            "description": "搜索记忆",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    assert result["has_tool_calls"] is True
    assert result["assistant_message"]["content"] == "需要查询。"
    assert result["tool_calls"][0]["function"]["arguments"] == "{\"query\": \"线索\"}"


@pytest.mark.asyncio
async def test_anthropic_stream_uses_sdk_stream_parser(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.example.com/v1/messages"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'event: content_block_delta\n'
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"你"}}\n\n'
                'event: content_block_delta\n'
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"好"}}\n\n'
                'event: message_stop\n'
                'data: {"type":"message_stop"}\n\n'
            ),
        )

    _mock_sdk_http(monkeypatch, handler)
    client = _client("anthropic_compatible")

    chunks = [chunk async for chunk in client.generate_stream("系统", "用户")]

    assert chunks == ["你", "好"]


@pytest.mark.asyncio
async def test_settings_test_connection_reuses_llm_client(monkeypatch):
    calls = []

    class FakeLLMClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def generate(self, **kwargs):
            calls.append({"client": self.kwargs, "generate": kwargs})
            return "ok"

    monkeypatch.setattr(settings_api, "LLMClient", FakeLLMClient)

    result = await settings_api.test_connection({
        "api_format": "openai_compatible",
        "api_key": "sk-test",
        "base_url": "https://api.example.com",
        "model": "test-model",
    })

    assert result == {"success": True, "message": "连接成功"}
    assert calls == [
        {
            "client": {
                "api_format": "openai_compatible",
                "api_key": "sk-test",
                "base_url": "https://api.example.com",
                "model": "test-model",
            },
            "generate": {
                "system_prompt": "你是一个连接测试助手。",
                "user_prompt": "Hi",
                "temperature": 0,
                "max_tokens": 5,
                "timeout": 30.0,
            },
        }
    ]


@pytest.mark.asyncio
async def test_settings_test_connection_reports_llm_error(monkeypatch):
    class FakeLLMClient:
        def __init__(self, **_kwargs):
            pass

        async def generate(self, **_kwargs):
            raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(settings_api, "LLMClient", FakeLLMClient)

    result = await settings_api.test_connection({
        "api_format": "anthropic_compatible",
        "api_key": "sk-test",
        "base_url": "https://api.example.com",
        "model": "test-model",
    })

    assert result["success"] is False
    assert result["message"] == "连接失败: RuntimeError: upstream unavailable"
