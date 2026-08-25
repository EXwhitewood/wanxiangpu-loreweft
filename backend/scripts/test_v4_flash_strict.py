"""重新测试 deepseek-v4-flash 的 tool calling —— 用明确 prompt + strict 模式。"""
import asyncio
import json
import os
import sys
sys.path.insert(0, ".")

from app.services.llm_client import LLMClient


API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
if not API_KEY:
    raise SystemExit("Set DEEPSEEK_API_KEY before running this live API diagnostic.")


async def test_v4_flash_explicit_prompt():
    """测试1：用更明确的 prompt（提供具体 issue_id 和参数）。"""
    print("=== 测试1: deepseek-v4-flash + 明确 prompt（标准 endpoint）===")
    client = LLMClient(
        api_format="openai_compatible",
        api_key=API_KEY,
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
    )

    messages = [
        {"role": "system", "content": (
            "你是 FBI 审查蓝图官。你的唯一职责是调用 submit_review_and_route 工具提交复核结果。\n"
            "禁止用文本回复，必须调用工具。\n"
            "现在收到一个 issue，请立即调用工具提交复核。"
        )},
        {"role": "user", "content": (
            "issue_id: iss_001\n"
            "type: ai_flavor\n"
            "metric: high_advisory\n"
            "detail: 检测到 AI 痕迹过重，建议降低 advisories 密度\n"
            "归一化工具给出的 family: anti_ai_local\n\n"
            "请调用 submit_review_and_route 工具，action=agree, route=llm"
        )},
    ]

    tools = [{
        "type": "function",
        "function": {
            "name": "submit_review_and_route",
            "description": "提交复核结果和路由判断（必须调用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "review_results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "issue_id": {"type": "string"},
                                "action": {"type": "string", "enum": ["agree", "correct", "supplement"]},
                                "route": {"type": "string", "enum": ["deterministic", "llm"]},
                            },
                            "required": ["issue_id", "action", "route"],
                        },
                    },
                },
                "required": ["review_results"],
            },
        },
    }]

    try:
        result = await client.generate_with_tools(
            messages=messages, tools=tools, temperature=0.2, max_tokens=4096, timeout=30.0,
        )
        print(f"has_tool_calls: {result.get('has_tool_calls')}")
        print(f"content: {repr(result.get('content', ''))[:400]}")
        if result.get("tool_calls"):
            print(f"tool_calls: {json.dumps(result['tool_calls'], ensure_ascii=False, indent=2)[:800]}")
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")


async def test_v4_flash_strict_mode():
    """测试2：strict 模式（beta endpoint）。"""
    print("\n=== 测试2: deepseek-v4-flash + strict 模式（beta endpoint）===")
    client = LLMClient(
        api_format="openai_compatible",
        api_key=API_KEY,
        base_url="https://api.deepseek.com/beta",
        model="deepseek-v4-flash",
    )

    messages = [
        {"role": "system", "content": "你是 FBI 审查蓝图官。必须调用 submit_review_and_route 工具。"},
        {"role": "user", "content": (
            "issue_id: iss_001, type: ai_flavor, family: anti_ai_local\n"
            "请调用 submit_review_and_route，action=agree, route=llm"
        )},
    ]

    tools = [{
        "type": "function",
        "function": {
            "name": "submit_review_and_route",
            "strict": True,
            "description": "提交复核结果和路由判断（必须调用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "review_results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "issue_id": {"type": "string"},
                                "action": {"type": "string", "enum": ["agree", "correct", "supplement"]},
                                "route": {"type": "string", "enum": ["deterministic", "llm"]},
                            },
                            "required": ["issue_id", "action", "route"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["review_results"],
                "additionalProperties": False,
            },
        },
    }]

    try:
        result = await client.generate_with_tools(
            messages=messages, tools=tools, temperature=0.2, max_tokens=4096, timeout=30.0,
        )
        print(f"has_tool_calls: {result.get('has_tool_calls')}")
        print(f"content: {repr(result.get('content', ''))[:400]}")
        if result.get("tool_calls"):
            print(f"tool_calls: {json.dumps(result['tool_calls'], ensure_ascii=False, indent=2)[:800]}")
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")


async def test_v4_flash_tool_choice():
    """测试3：tool_choice=required 强制调用工具。"""
    print("\n=== 测试3: deepseek-v4-flash + tool_choice=required ===")
    import httpx
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "你是 FBI 审查蓝图官。必须调用 submit_review_and_route 工具。"},
            {"role": "user", "content": "issue_id: iss_001, action=agree, route=llm。请调用工具。"},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "submit_review_and_route",
                "description": "提交复核结果",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "review_results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "issue_id": {"type": "string"},
                                    "action": {"type": "string", "enum": ["agree", "correct", "supplement"]},
                                    "route": {"type": "string", "enum": ["deterministic", "llm"]},
                                },
                                "required": ["issue_id", "action", "route"],
                            },
                        },
                    },
                    "required": ["review_results"],
                },
            },
        }],
        "tool_choice": "required",
        "temperature": 0.2,
        "max_tokens": 4096,
    }
    async with httpx.AsyncClient(timeout=30.0) as hc:
        resp = await hc.post(
            "https://api.deepseek.com/chat/completions",
            headers=headers, json=payload,
        )
        print(f"status: {resp.status_code}")
        data = resp.json()
        if "choices" in data:
            msg = data["choices"][0]["message"]
            print(f"content: {repr(msg.get('content', ''))[:300]}")
            print(f"tool_calls: {json.dumps(msg.get('tool_calls', []), ensure_ascii=False, indent=2)[:800]}")
        else:
            print(f"response: {json.dumps(data, ensure_ascii=False)[:500]}")


async def main():
    await test_v4_flash_explicit_prompt()
    await test_v4_flash_strict_mode()
    await test_v4_flash_tool_choice()


if __name__ == "__main__":
    asyncio.run(main())
