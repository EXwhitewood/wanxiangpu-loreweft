import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.scene_critic import SceneCriticAgent


@pytest.mark.asyncio
async def test_critic_drops_contract_free_clue_provenance_guess():
    critic = SceneCriticAgent()
    llm = AsyncMock()
    llm.generate.return_value = json.dumps({
        "violations": [{
            "type": "clue_provenance_error",
            "severity": "high",
            "detail": "三足鸟纹没有交代放置者",
            "target_span": "凤溪掌心的三足鸟纹亮起",
            "expected_behavior": "补写来源",
        }]
    }, ensure_ascii=False)
    contract = {
        "goal": "继续战斗",
        "clues": [{
            "description": "大师伯胸前的黑色阵纹",
            "source_actor": "大师伯",
            "placement_time": "战斗前",
            "discovery_condition": "衣袍破损后被看见",
        }],
    }

    with patch.object(critic, "get_llm_client", return_value=llm):
        violations = await critic._llm_check("凤溪掌心的三足鸟纹亮起。", contract, {}, [])

    assert violations == []


@pytest.mark.asyncio
async def test_critic_attaches_contract_authority_to_matching_clue():
    critic = SceneCriticAgent()
    llm = AsyncMock()
    llm.generate.return_value = json.dumps({
        "violations": [{
            "type": "clue_provenance_error",
            "severity": "high",
            "detail": "大师伯胸前的黑色阵纹缺少来源说明",
            "target_span": "黑色阵纹从破损衣袍下露出",
            "expected_behavior": "补写来源",
        }]
    }, ensure_ascii=False)
    contract = {
        "goal": "识别阵纹",
        "clues": [{
            "description": "大师伯胸前的黑色阵纹",
            "source_actor": "大师伯",
            "placement_time": "战斗中衣袍破损时",
            "discovery_condition": "凤溪近身时看见",
        }],
    }

    with patch.object(critic, "get_llm_client", return_value=llm):
        violations = await critic._llm_check("黑色阵纹从破损衣袍下露出。", contract, {}, [])

    assert len(violations) == 1
    assert violations[0]["evidence"]["authority_status"] == "confirmed"
    assert "source_actor=大师伯" in violations[0]["evidence"]["authority_fact"]
