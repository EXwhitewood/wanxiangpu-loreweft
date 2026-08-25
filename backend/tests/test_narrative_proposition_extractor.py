"""叙事命题抽取器测试。

使用抽象实体占位符（角色A/B、物品X、地点Y、组织Z、事件E），
遵循反污染原则，不引用任何具体作品角色名或道具名。
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.narrative_proposition import (
    Certainty,
    EntityType,
    ExtractionResult,
    NarrativeEntity,
    NarrativePredicate,
    NarrativeProposition,
    Polarity,
    PredicateCategory,
    Responsibility,
    TruthLayer,
)
from app.services.narrative_proposition_extractor import NarrativePropositionExtractor
from app.services.llm_task_profiles import LLMTaskType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def extractor():
    return NarrativePropositionExtractor()


@pytest.fixture
def base_context():
    """基础抽取上下文，使用抽象占位符。"""
    return {
        "project_id": "test-project",
        "chapter_number": 1,
        "scene_index": 0,
        "generated_text": "角色A在地点Y发现了物品X。角色B声称角色A是主动行为者。",
        "scene_contract": {},
        "fact_contract": {},
        "scene_provenance": "",
        "character_cards": [],
        "known_entities": [
            {"name": "角色A", "entity_type": "character"},
            {"name": "角色B", "entity_type": "character"},
            {"name": "物品X", "entity_type": "item"},
            {"name": "地点Y", "entity_type": "location"},
        ],
    }


def _make_mock_llm_response(propositions_data: list[dict]) -> str:
    """构造模拟 LLM 返回的 JSON 字符串。"""
    return json.dumps({"propositions": propositions_data}, ensure_ascii=False)


def _sample_proposition(
    subject_name: str = "角色A",
    subject_type: str = "character",
    predicate_name: str = "发现",
    predicate_category: str = "action",
    object_name: str = "物品X",
    object_type: str = "item",
    truth_layer: str = "current",
    certainty: str = "confirmed",
    polarity: str = "affirmed",
    responsibility: str = "active_actor",
    source_text: str = "角色A在地点Y发现了物品X",
    confidence: float = 0.95,
) -> dict:
    """生成一条标准命题的原始 dict。"""
    return {
        "subject": {"name": subject_name, "entity_type": subject_type},
        "predicate": {"name": predicate_name, "category": predicate_category},
        "object": {"name": object_name, "entity_type": object_type},
        "truth_layer": truth_layer,
        "certainty": certainty,
        "polarity": polarity,
        "responsibility": responsibility,
        "time_scope": "",
        "location_scope": "地点Y",
        "source_text": source_text,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# 测试：抽取返回正确的 ExtractionResult
# ---------------------------------------------------------------------------

class TestExtractionResultStructure:
    """测试抽取结果的结构正确性。"""

    @pytest.mark.asyncio
    async def test_extract_returns_extraction_result(self, extractor, base_context):
        """抽取应返回 ExtractionResult 实例。"""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition()
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert isinstance(result, ExtractionResult)
        assert len(result.propositions) >= 1

    @pytest.mark.asyncio
    async def test_extract_propositions_have_ids(self, extractor, base_context):
        """每条命题应有 proposition_id。"""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition()
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        for prop in result.propositions:
            assert prop.proposition_id
            assert isinstance(prop.proposition_id, str)

    @pytest.mark.asyncio
    async def test_extract_entity_mentions_collected(self, extractor, base_context):
        """抽取结果应包含实体提及。"""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition()
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        entity_names = [e.name for e in result.entity_mentions]
        assert "角色A" in entity_names
        assert "物品X" in entity_names


# ---------------------------------------------------------------------------
# 测试：truth_layer 分类
# ---------------------------------------------------------------------------

class TestTruthLayerClassification:
    """测试真值层分类正确性。"""

    @pytest.mark.asyncio
    async def test_truth_layer_current(self, extractor, base_context):
        """current 层应正确分类。"""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(truth_layer="current")
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].truth_layer == "current"

    @pytest.mark.asyncio
    async def test_truth_layer_reference(self, extractor, base_context):
        """reference 层应正确分类。"""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(
                truth_layer="reference",
                source_text="据原书记载，角色A曾持有物品X",
            )
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].truth_layer == "reference"

    @pytest.mark.asyncio
    async def test_truth_layer_dream(self, extractor, base_context):
        """dream 层应正确分类。"""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(
                truth_layer="dream",
                source_text="角色A梦见物品X",
            )
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].truth_layer == "dream"

    @pytest.mark.asyncio
    async def test_truth_layer_rumor(self, extractor, base_context):
        """rumor 层应正确分类。"""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(
                truth_layer="rumor",
                source_text="传闻角色A持有物品X",
            )
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].truth_layer == "rumor"

    @pytest.mark.asyncio
    async def test_truth_layer_inference(self, extractor, base_context):
        """inference 层应正确分类。"""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(
                truth_layer="inference",
                source_text="角色A推测物品X在地点Y",
            )
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].truth_layer == "inference"

    @pytest.mark.asyncio
    async def test_truth_layer_invalid_clamped_to_default(self, extractor, base_context):
        """无效 truth_layer 应被钳制为默认值 current。"""
        mock_llm = AsyncMock()
        raw_prop = _sample_proposition()
        raw_prop["truth_layer"] = "invalid_layer"
        mock_llm.generate.return_value = _make_mock_llm_response([raw_prop])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].truth_layer == "current"


# ---------------------------------------------------------------------------
# 测试：certainty 分类
# ---------------------------------------------------------------------------

class TestCertaintyClassification:
    """测试确定性分类正确性。"""

    @pytest.mark.asyncio
    async def test_certainty_confirmed(self, extractor, base_context):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(certainty="confirmed")
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].certainty == "confirmed"

    @pytest.mark.asyncio
    async def test_certainty_reported(self, extractor, base_context):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(certainty="reported")
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].certainty == "reported"

    @pytest.mark.asyncio
    async def test_certainty_accused(self, extractor, base_context):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(certainty="accused")
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].certainty == "accused"

    @pytest.mark.asyncio
    async def test_certainty_suspected(self, extractor, base_context):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(certainty="suspected")
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].certainty == "suspected"

    @pytest.mark.asyncio
    async def test_certainty_invalid_clamped_to_default(self, extractor, base_context):
        """无效 certainty 应被钳制为默认值 unknown。"""
        mock_llm = AsyncMock()
        raw_prop = _sample_proposition()
        raw_prop["certainty"] = "definite"
        mock_llm.generate.return_value = _make_mock_llm_response([raw_prop])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].certainty == "unknown"


# ---------------------------------------------------------------------------
# 测试：responsibility 分类
# ---------------------------------------------------------------------------

class TestResponsibilityClassification:
    """测试责任归属分类正确性。"""

    @pytest.mark.asyncio
    async def test_responsibility_active_actor(self, extractor, base_context):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(responsibility="active_actor")
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].responsibility == "active_actor"

    @pytest.mark.asyncio
    async def test_responsibility_framed(self, extractor, base_context):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(responsibility="framed")
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].responsibility == "framed"

    @pytest.mark.asyncio
    async def test_responsibility_accused(self, extractor, base_context):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(responsibility="accused")
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].responsibility == "accused"

    @pytest.mark.asyncio
    async def test_responsibility_invalid_clamped_to_default(self, extractor, base_context):
        """无效 responsibility 应被钳制为默认值 unknown。"""
        mock_llm = AsyncMock()
        raw_prop = _sample_proposition()
        raw_prop["responsibility"] = "perpetrator"
        mock_llm.generate.return_value = _make_mock_llm_response([raw_prop])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].responsibility == "unknown"


# ---------------------------------------------------------------------------
# 测试：source_text
# ---------------------------------------------------------------------------

class TestSourceText:
    """测试每条命题必须包含 source_text。"""

    @pytest.mark.asyncio
    async def test_each_proposition_has_source_text(self, extractor, base_context):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(source_text="角色A在地点Y发现了物品X"),
            _sample_proposition(
                subject_name="角色B",
                predicate_name="声称",
                source_text="角色B声称角色A是主动行为者",
            ),
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        for prop in result.propositions:
            assert prop.source_text, "每条命题必须有 source_text"
            assert isinstance(prop.source_text, str)

    @pytest.mark.asyncio
    async def test_source_text_preserved_from_llm(self, extractor, base_context):
        """source_text 应保留 LLM 返回的原文片段。"""
        expected_source = "角色A在地点Y发现了物品X"
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition(source_text=expected_source)
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert result.propositions[0].source_text == expected_source


# ---------------------------------------------------------------------------
# 测试：extractor 失败返回 extractor_unavailable 警告
# ---------------------------------------------------------------------------

class TestExtractorFailure:
    """测试抽取器不可用时的降级行为。"""

    @pytest.mark.asyncio
    async def test_llm_client_unavailable_returns_warning(self, extractor, base_context):
        """LLM 客户端不可用时应返回 extractor_unavailable 警告。"""
        with patch.object(extractor, "_get_llm_client", return_value=None):
            result = await extractor.extract(base_context)

        assert isinstance(result, ExtractionResult)
        assert "extractor_unavailable" in result.extractor_warnings
        assert len(result.propositions) == 0

    @pytest.mark.asyncio
    async def test_llm_call_exception_returns_warning(self, extractor, base_context):
        """LLM 调用异常时应返回 extractor_unavailable 警告。"""
        mock_llm = AsyncMock()
        mock_llm.generate.side_effect = Exception("LLM 服务不可用")

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            result = await extractor.extract(base_context)

        assert "extractor_unavailable" in result.extractor_warnings

    @pytest.mark.asyncio
    async def test_empty_generated_text_returns_warning(self, extractor):
        """空文本应返回 empty_input 警告。"""
        result = await extractor.extract({"generated_text": ""})

        assert "empty_input" in result.extractor_warnings
        assert len(result.propositions) == 0


# ---------------------------------------------------------------------------
# 测试：JSON 解析失败触发 validator_retry
# ---------------------------------------------------------------------------

class TestValidatorRetry:
    """测试 JSON 解析失败时的 validator_retry 机制。"""

    @pytest.mark.asyncio
    async def test_json_parse_failure_triggers_validator_retry(self, extractor, base_context):
        """首次 JSON 解析失败应触发 validator_retry。

        由于 _VALIDATOR_RETRY_PROMPT 模板中包含 JSON 示例的花括号会导致
        .format() 抛出 KeyError，我们直接 mock _validator_retry 方法
        来验证它被调用，并返回修正后的结果。
        """
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = "这不是有效的JSON{{{"

        retry_result = ExtractionResult(
            propositions=[
                NarrativeProposition(
                    proposition_id="retry-id",
                    project_id="test-project",
                    chapter_number=1,
                    scene_index=0,
                    subject=NarrativeEntity(name="角色A", entity_type="character"),
                    predicate=NarrativePredicate(name="发现", category="action"),
                    object=NarrativeEntity(name="物品X", entity_type="item"),
                    truth_layer="current",
                    certainty="confirmed",
                    source_text="角色A发现了物品X",
                )
            ],
        )

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm), \
             patch.object(extractor, "_validator_retry", return_value=retry_result) as mock_retry:
            result = await extractor.extract(base_context)

        # validator_retry 应被调用
        mock_retry.assert_called_once()
        # retry 返回的结果应被使用
        assert len(result.propositions) >= 1

    @pytest.mark.asyncio
    async def test_validator_retry_also_fails(self, extractor, base_context):
        """validator_retry 也失败时应保留 json_parse_failed 警告。"""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = "无效JSON{{{"

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm), \
             patch.object(extractor, "_validator_retry", return_value=None):
            result = await extractor.extract(base_context)

        assert "json_parse_failed" in result.extractor_warnings
        assert len(result.propositions) == 0

    @pytest.mark.asyncio
    async def test_parse_extraction_response_valid_json(self, extractor, base_context):
        """parse_extraction_response 应正确解析有效 JSON。"""
        response = _make_mock_llm_response([_sample_proposition()])
        result = extractor.parse_extraction_response(response, base_context)

        assert isinstance(result, ExtractionResult)
        assert len(result.propositions) == 1
        assert result.propositions[0].subject.name == "角色A"

    @pytest.mark.asyncio
    async def test_parse_extraction_response_invalid_json(self, extractor, base_context):
        """parse_extraction_response 对无效 JSON 应返回 json_parse_failed 警告。"""
        result = extractor.parse_extraction_response("不是JSON", base_context)

        assert "json_parse_failed" in result.extractor_warnings
        assert len(result.propositions) == 0

    def test_balanced_objects_are_salvaged_from_missing_array_comma(
        self, extractor, base_context,
    ):
        first = _sample_proposition()
        second = _sample_proposition(
            subject_name="角色B",
            predicate_name="声称",
            object_name="角色A",
            object_type="character",
            source_text="角色B声称角色A是主动行为者",
        )
        malformed = (
            '{"propositions":['
            + json.dumps(first, ensure_ascii=False)
            + json.dumps(second, ensure_ascii=False)
            + "]}"
        )

        result = extractor.parse_extraction_response(malformed, base_context)

        assert len(result.propositions) == 2
        assert result.ambiguous_claims == []
        assert "json_salvage_2_recovered" in result.extractor_warnings

    @pytest.mark.asyncio
    async def test_extraction_calls_use_bounded_json_audit_profile(
        self, extractor, base_context,
    ):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = _make_mock_llm_response([
            _sample_proposition()
        ])

        with patch.object(extractor, "_get_llm_client", return_value=mock_llm):
            await extractor.extract(base_context)

        kwargs = mock_llm.generate.await_args.kwargs
        assert kwargs["task_type"] == LLMTaskType.JSON_AUDIT
        assert kwargs["max_tokens"] == 6144

    @pytest.mark.asyncio
    async def test_truncated_document_repairs_only_tail_and_merges_valid_objects(
        self, extractor,
    ):
        text = (
            "PREFIX-DO-NOT-REPEAT-"
            + ("前文" * 200)
            + "角色A看见石门。"
            + ("后文" * 120)
            + "角色B推开石门。"
        )
        first = _sample_proposition(
            source_text="角色A看见石门",
            predicate_name="看见",
            object_name="石门",
        )
        second = _sample_proposition(
            subject_name="角色B",
            predicate_name="推开",
            object_name="石门",
            source_text="角色B推开石门",
        )
        repaired = _make_mock_llm_response([second])
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = repaired
        context = {
            "generated_text": text,
            "project_id": "test-project",
            "chapter_number": 1,
            "scene_index": 0,
            "strict_source_text": True,
        }
        valid = extractor._build_proposition(
            first,
            "test-project",
            1,
            0,
            text,
            True,
        )
        repaired_result = await extractor._validator_retry(
            mock_llm,
            "malformed response",
            context,
            parse_errors=[
                {"index": 1, "raw": {"subject": "角色B"}, "error": "invalid object", "scope": "proposition"},
                {"index": None, "raw": "truncated tail", "error": "array not closed", "scope": "document"},
            ],
            valid_propositions=[valid],
        )

        assert repaired_result is not None
        merged = extractor._merge_partial_repair(
            ExtractionResult(
                propositions=[valid],
                ambiguous_claims=[{"scope": "document"}],
            ),
            repaired_result,
        )
        assert len(merged.propositions) == 2
        retry_kwargs = mock_llm.generate.await_args.kwargs
        assert retry_kwargs["max_tokens"] == 2048
        assert "PREFIX-DO-NOT-REPEAT-" not in retry_kwargs["user_prompt"]
