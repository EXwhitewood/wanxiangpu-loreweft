"""直接测试 deepseek API 的 tool calling 能力。"""
import asyncio
import json
import os
import sys
sys.path.insert(0, ".")

from app.services.llm_client import LLMClient


API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
if not API_KEY:
    raise SystemExit("Set DEEPSEEK_API_KEY before running this live API diagnostic.")


async def main():
    client = LLMClient(
        api_format="openai_compatible",
        api_key=API_KEY,
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
    )

    messages = [
        {"role": "system", "content": "你是 FBI 审查蓝图官。必须调用 submit_review_and_route 工具提交复核结果。"},
        {"role": "user", "content": "有一个问题：type=ai_flavor, metric=high_advisory, detail='AI 痕迹过重'。请调用 submit_review_and_route 提交复核结果。"},
    ]

    tools = [{
        "type": "function",
        "function": {
            "name": "submit_review_and_route",
            "description": "提交复核结果和路由判断",
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
                        },
                    },
                },
                "required": ["review_results"],
            },
        },
    }]

    print(f"=== 测试 deepseek-v4-flash tool calling ===")
    try:
        result = await client.generate_with_tools(
            messages=messages,
            tools=tools,
            temperature=0.2,
            max_tokens=4096,
            timeout=30.0,
        )
        print(f"has_tool_calls: {result.get('has_tool_calls')}")
        print(f"content: {repr(result.get('content', ''))[:500]}")
        print(f"reasoning_content: {repr(result.get('reasoning_content'))[:200]}")
        if result.get("tool_calls"):
            print(f"tool_calls: {json.dumps(result['tool_calls'], ensure_ascii=False, indent=2)[:1000]}")
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")

    # 再试 deepseek-chat
    print(f"\n=== 测试 deepseek-chat tool calling ===")
    client2 = LLMClient(
        api_format="openai_compatible",
        api_key=API_KEY,
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )
    try:
        result2 = await client2.generate_with_tools(
            messages=messages,
            tools=tools,
            temperature=0.2,
            max_tokens=4096,
            timeout=30.0,
        )
        print(f"has_tool_calls: {result2.get('has_tool_calls')}")
        print(f"content: {repr(result2.get('content', ''))[:500]}")
        if result2.get("tool_calls"):
            print(f"tool_calls: {json.dumps(result2['tool_calls'], ensure_ascii=False, indent=2)[:1000]}")
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
