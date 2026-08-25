"""叙事命题抽取器。

从生成文本中抽取结构化叙事命题，运行于 CoreGenerationAgent 之后、
QualityGate 之前。只回答"正文声称了什么"，不评价文笔。
"""

import json
import logging
import re
import uuid
from difflib import SequenceMatcher
from typing import Any

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
from app.services.json_response import parse_json_response
from app.services.llm_client import LLMClient
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)

_MAX_VALIDATOR_RETRIES = 2


def split_proposition_chunks(text: str, max_chars: int = 6000) -> list[tuple[int, int, str]]:
    """Return contiguous, lossless text chunks with original offsets."""
    text = str(text or "")
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + max_chars)
        end = hard_end
        if hard_end < len(text):
            search_floor = start + max_chars // 2
            best = -1
            best_width = 0
            for delimiter in ("\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? "):
                found = text.rfind(delimiter, search_floor, hard_end)
                if found > best:
                    best = found
                    best_width = len(delimiter)
            if best >= search_floor:
                end = best + best_width
        if end <= start:
            end = hard_end
        chunks.append((start, end, text[start:end]))
        start = end
    return chunks


def align_exact_source_span(
    candidate: str,
    source: str,
    anchors: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    """Map a near-verbatim claim back to a conservative exact source slice."""
    candidate = str(candidate or "").strip()
    source = str(source or "")
    if not candidate or not source:
        return None
    if candidate in source:
        return candidate

    def normalized_with_offsets(value: str) -> tuple[str, list[int]]:
        chars: list[str] = []
        offsets: list[int] = []
        for index, char in enumerate(value):
            if char.isalnum():
                chars.append(char.casefold())
                offsets.append(index)
        return "".join(chars), offsets

    normalized_candidate, _ = normalized_with_offsets(candidate)
    normalized_source, source_offsets = normalized_with_offsets(source)
    if not normalized_candidate or not normalized_source:
        return None
    start = normalized_source.find(normalized_candidate)
    if start >= 0:
        end = start + len(normalized_candidate) - 1
        return source[source_offsets[start]:source_offsets[end] + 1]

    # Models sometimes join two exact evidence fragments with an ellipsis.  The
    # stored evidence must still be one contiguous source slice, so preserve the
    # complete original window between the first and last aligned fragments.
    multi_parts = [
        part.strip()
        for part in re.split(r"(?:…{1,}|\.{3,})", candidate)
        if part.strip()
    ]
    if len(multi_parts) >= 2:
        aligned_ranges: list[tuple[int, int]] = []
        cursor = 0
        for part in multi_parts:
            aligned = align_exact_source_span(part, source[cursor:], anchors=anchors)
            if not aligned:
                continue
            relative_start = source.find(aligned, cursor)
            if relative_start < 0:
                continue
            relative_end = relative_start + len(aligned)
            aligned_ranges.append((relative_start, relative_end))
            cursor = relative_end
        minimum_parts = max(2, (len(multi_parts) + 1) // 2)
        if len(aligned_ranges) >= minimum_parts:
            window_start = aligned_ranges[0][0]
            window_end = aligned_ranges[-1][1]
            if 0 < window_end - window_start <= 2200:
                return source[window_start:window_end]

    # Dialogue attribution often interrupts one quoted claim (for example,
    # "first half," the speaker said, "second half").  Accept only a bounded
    # ordered-subsequence match and return the attribution-inclusive source.
    if len(normalized_candidate) >= 8:
        shortest: tuple[int, int] | None = None
        search_from = 0
        while True:
            candidate_start = normalized_source.find(normalized_candidate[0], search_from)
            if candidate_start < 0:
                break
            cursor = candidate_start
            matched = True
            for char in normalized_candidate:
                cursor = normalized_source.find(char, cursor)
                if cursor < 0:
                    matched = False
                    break
                cursor += 1
            if matched:
                span_width = cursor - candidate_start
                if span_width <= max(len(normalized_candidate) * 4, len(normalized_candidate) + 24):
                    if shortest is None or span_width < shortest[1] - shortest[0]:
                        shortest = (candidate_start, cursor)
            search_from = candidate_start + 1
        if shortest is not None:
            start_index, end_index = shortest
            return source[source_offsets[start_index]:source_offsets[end_index - 1] + 1]

    normalized_anchors = [
        normalized
        for value in (anchors or [])
        if len(normalized := normalized_with_offsets(str(value or ""))[0]) >= 2
    ]
    clauses = [
        match.group(0)
        for match in re.finditer(r"[^。！？!?；;\n]+[。！？!?；;\n]*", source)
        if match.group(0).strip()
    ]
    candidates: list[str] = []
    for index in range(len(clauses)):
        combined = ""
        for width in range(3):
            target = index + width
            if target >= len(clauses):
                break
            combined += clauses[target]
            if len(combined) > 260:
                break
            candidates.append(combined)

    best_span = ""
    best_ratio = 0.0
    best_anchor_hits = 0
    best_score = 0.0
    for span in candidates:
        normalized_span, _ = normalized_with_offsets(span)
        if not normalized_span:
            continue
        ratio = SequenceMatcher(
            None,
            normalized_candidate,
            normalized_span,
            autojunk=False,
        ).ratio()
        anchor_hits = sum(1 for anchor in normalized_anchors if anchor in normalized_span)
        score = ratio + min(anchor_hits, 3) * 0.04
        if score > best_score:
            best_span = span.strip()
            best_ratio = ratio
            best_anchor_hits = anchor_hits
            best_score = score

    strong_text_match = best_ratio >= 0.84
    anchored_text_match = (
        best_ratio >= 0.72 and best_anchor_hits >= 2
    ) or (
        best_ratio >= 0.78 and best_anchor_hits >= 1
    )
    if best_span and (strong_text_match or anchored_text_match):
        return best_span
    return None

# ---------------------------------------------------------------------------
# 枚举合法值（用于提示词和校验）
# ---------------------------------------------------------------------------

_TRUTH_LAYERS: list[str] = [
    "current", "reference", "memory", "dream", "rumor",
    "inference", "plan", "future_hint", "counterfactual",
]

_CERTAINTIES: list[str] = [
    "confirmed", "reported", "accused", "suspected",
    "inferred", "unknown", "negated", "misleading",
]

_RESPONSIBILITIES: list[str] = [
    "active_actor", "victim", "framed", "accused",
    "coerced", "witness", "unknown",
]

_POLARITIES: list[str] = ["affirmed", "negated", "ambiguous"]

_PREDICATE_CATEGORIES: list[str] = [
    "action", "state", "location", "ownership",
    "relationship", "knowledge", "intention", "event", "clue",
]

_ENTITY_TYPES: list[str] = [
    "character", "item", "location", "organization",
    "concept", "event", "unknown",
]

# ---------------------------------------------------------------------------
# 系统提示词（通用，不含任何项目角色名或道具名）
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """你是叙事命题抽取器。你的唯一任务是从给定的叙事文本中抽取结构化命题。

核心原则：
1. 只抽取文本明确声称的内容——角色明确相信的、记录明确记载的、传闻明确传播的
2. 绝不编造情节，绝不评价文笔质量
3. 每条命题必须包含 source_text（正文原文片段）
4. 必须区分以下真值层（truth_layer）：
   - current: 当前事实（叙述者直接陈述的当下事实）
   - reference: 参考设定、原书、历史资料、外部剧本
   - memory: 角色回忆、记忆中的内容
   - dream: 梦境、幻觉、预知画面
   - rumor: 传闻、道听途说
   - inference: 角色推测、推断
   - plan: 计划、意图
   - future_hint: 未来暗示、预言、未发生片段
   - counterfactual: 假设、如果、脑补
5. 必须区分确定性（certainty）：
   - confirmed: 已确认
   - reported: 被记载、被说法声称
   - accused: 被指控
   - suspected: 被怀疑
   - inferred: 推断
   - unknown: 未确认
   - negated: 被否定
   - misleading: 可能误导
6. 必须区分责任归属（responsibility）：
   - active_actor: 主动行为者
   - victim: 受害者
   - framed: 被栽赃者
   - accused: 被指认者
   - coerced: 被迫者
   - witness: 目击或知情者
   - unknown: 不明
7. 必须区分极性（polarity）：
   - affirmed: 肯定
   - negated: 否定
   - ambiguous: 模糊

输出格式：严格按以下 JSON 格式输出，不要输出其他内容。
```json
{
  "propositions": [
    {
      "subject": {"name": "实体名", "entity_type": "character"},
      "predicate": {"name": "谓词名", "category": "action"},
      "object": {"name": "宾语实体名", "entity_type": "item"},
      "truth_layer": "current",
      "certainty": "confirmed",
      "polarity": "affirmed",
      "responsibility": "active_actor",
      "time_scope": "时间范围描述",
      "location_scope": "地点范围描述",
      "source_text": "正文原文片段",
      "confidence": 0.95
    }
  ]
}
```

字段说明：
- subject: 主语实体，必须包含 name 和 entity_type
- predicate: 谓词，必须包含 name 和 category
- object: 宾语实体（可为 null），如存在则包含 name 和 entity_type
- truth_layer: 真值层，取值范围：current / reference / memory / dream / rumor / inference / plan / future_hint / counterfactual
- certainty: 确定性，取值范围：confirmed / reported / accused / suspected / inferred / unknown / negated / misleading
- polarity: 极性，取值范围：affirmed / negated / ambiguous
- responsibility: 责任归属，取值范围：active_actor / victim / framed / accused / coerced / witness / unknown
- time_scope: 时间范围（自由文本）
- location_scope: 地点范围（自由文本）
- source_text: 正文原文依据片段（必须提供）
- confidence: 置信度，0.0 到 1.0 之间的浮点数

注意事项：
- entity_type 取值：character / item / location / organization / concept / event / unknown
- predicate category 取值：action / state / location / ownership / relationship / knowledge / intention / event / clue
- 如果没有宾语，object 设为 null
- source_text 必须是正文中的原文片段，便于追溯
- 不要遗漏隐含命题，但不要过度推断
- 同一事件可拆分为多条命题（不同视角、不同真值层）
"""

_COMPACT_VALIDATOR_RETRY_PROMPT = """The previous proposition extraction contained invalid JSON objects.
Return only valid JSON using this schema:
{
  "propositions": [
    {
      "subject": {"name": "...", "entity_type": "..."},
      "predicate": {"name": "...", "category": "..."},
      "object": {"name": "...", "entity_type": "..."},
      "truth_layer": "...",
      "certainty": "...",
      "polarity": "...",
      "responsibility": "...",
      "time_scope": "...",
      "location_scope": "...",
      "source_text": "...",
      "confidence": 0.0
    }
  ]
}

Every source_text must be an exact contiguous substring copied from SOURCE TEXT.
Keep field values concise. Do not add markdown or explanations.

MODE:
{repair_mode}

INVALID MATERIAL:
{invalid_material}"""


def _salvage_proposition_payload(response: str) -> tuple[list[dict], list[dict]]:
    """Recover balanced proposition objects while keeping truncation fail-closed."""
    response = str(response or "")
    marker = re.search(r'["\']propositions["\']\s*:\s*\[', response, re.IGNORECASE)
    if marker is None:
        return [], []

    array_start = marker.end() - 1
    object_start: int | None = None
    object_depth = 0
    array_depth = 0
    in_string = False
    quote = ""
    escaped = False
    fragments: list[str] = []
    array_closed = False

    for index in range(array_start, len(response)):
        char = response[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue

        if char in {'"', "'"}:
            in_string = True
            quote = char
            continue
        if char == "[":
            array_depth += 1
            continue
        if char == "]":
            array_depth -= 1
            if array_depth == 0:
                array_closed = True
                break
            continue
        if array_depth != 1:
            continue
        if char == "{":
            if object_depth == 0:
                object_start = index
            object_depth += 1
        elif char == "}" and object_depth:
            object_depth -= 1
            if object_depth == 0 and object_start is not None:
                fragments.append(response[object_start:index + 1])
                object_start = None

    if object_start is not None:
        fragments.append(response[object_start:])

    recovered: list[dict] = []
    failures: list[dict] = []
    for index, fragment in enumerate(fragments):
        try:
            item = parse_json_response(fragment)
            if not isinstance(item, dict):
                raise ValueError("proposition fragment is not an object")
            recovered.append(item)
        except (ValueError, json.JSONDecodeError) as exc:
            failures.append({
                "index": index,
                "raw": fragment[:4000],
                "error": str(exc),
                "scope": "proposition",
            })

    if not array_closed:
        failures.append({
            "index": None,
            "raw": response[max(array_start, len(response) - 3000):],
            "error": "propositions array is not closed; response may be truncated",
            "scope": "document",
        })
    return recovered, failures


class NarrativePropositionExtractor:
    """叙事命题抽取器：从生成文本中抽取结构化命题。"""

    async def extract(self, context: dict) -> ExtractionResult:
        """从生成文本中抽取叙事命题。

        Args:
            context: 包含 generated_text, scene_contract, fact_contract,
                     scene_provenance, character_cards, known_entities 等字段。

        Returns:
            ExtractionResult 包含抽取的命题、实体提及和警告。
        """
        generated_text = context.get("generated_text", "")
        strict_source_text = bool(context.get("strict_source_text", False))
        if not generated_text or not generated_text.strip():
            logger.warning("[NarrativePropositionExtractor] generated_text 为空，跳过抽取")
            return ExtractionResult(extractor_warnings=["empty_input"])

        llm = await self._get_llm_client()
        if llm is None:
            logger.error("[NarrativePropositionExtractor] 无法创建 LLM 客户端")
            return ExtractionResult(extractor_warnings=["extractor_unavailable"])

        chunks = split_proposition_chunks(generated_text)
        propositions: list[NarrativeProposition] = []
        mentions: list[NarrativeEntity] = []
        ambiguous_claims: list[dict] = []
        warnings: list[str] = []
        failed_chunks: list[int] = []
        covered_chars = 0
        seen_props: set[tuple] = set()

        for chunk_index, (start, end, chunk_text) in enumerate(chunks):
            chunk_context = dict(context)
            chunk_context["generated_text"] = chunk_text
            chunk_context["source_chunk_index"] = chunk_index
            chunk_context["source_chunk_start"] = start
            chunk_context["source_chunk_end"] = end
            try:
                response = await llm.generate(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=self.build_extraction_prompt(chunk_context),
                    temperature=0.2,
                    max_tokens=6144,
                    response_format={"type": "json_object"},
                    task_type=LLMTaskType.JSON_AUDIT,
                )
            except Exception as exc:
                logger.error("[NarrativePropositionExtractor] chunk %d LLM 调用失败: %s", chunk_index, exc)
                failed_chunks.append(chunk_index)
                warnings.append("extractor_unavailable")
                warnings.append(f"chunk_{chunk_index}_extractor_unavailable")
                continue
            if not response or not response.strip():
                failed_chunks.append(chunk_index)
                warnings.append("empty_llm_response")
                warnings.append(f"chunk_{chunk_index}_empty_llm_response")
                continue

            result = self.parse_extraction_response(response, chunk_context)
            needs_validator_retry = (
                "json_parse_failed" in result.extractor_warnings
                or bool(result.ambiguous_claims)
            )
            if needs_validator_retry:
                original_result = result
                for _ in range(_MAX_VALIDATOR_RETRIES):
                    repaired = await self._validator_retry(
                        llm,
                        response,
                        chunk_context,
                        parse_errors=original_result.ambiguous_claims,
                        valid_propositions=original_result.propositions,
                    )
                    if repaired is not None:
                        if original_result.propositions and original_result.ambiguous_claims:
                            result = self._merge_partial_repair(original_result, repaired)
                        else:
                            result = repaired
                        break
            if "json_parse_failed" in result.extractor_warnings:
                failed_chunks.append(chunk_index)
                warnings.append("json_parse_failed")
                warnings.append(f"chunk_{chunk_index}_json_parse_failed")
                continue

            covered_chars += len(chunk_text)
            warnings.extend(
                f"chunk_{chunk_index}_{warning}"
                for warning in result.extractor_warnings
                if warning != "json_parse_failed"
            )
            ambiguous_claims.extend(result.ambiguous_claims)
            for proposition in result.propositions:
                proposition.source_chunk_index = chunk_index
                marker = (
                    proposition.subject.entity_id or proposition.subject.name,
                    proposition.predicate.name,
                    proposition.object.entity_id if proposition.object else "",
                    proposition.object.name if proposition.object else "",
                    proposition.truth_layer,
                    proposition.polarity,
                    proposition.source_text,
                )
                if marker not in seen_props:
                    seen_props.add(marker)
                    propositions.append(proposition)
            existing_mentions = {(mention.entity_id or "", mention.name) for mention in mentions}
            for mention in result.entity_mentions:
                marker = (mention.entity_id or "", mention.name)
                if marker not in existing_mentions:
                    mentions.append(mention)
                    existing_mentions.add(marker)

        complete = not failed_chunks and covered_chars == len(generated_text) and not ambiguous_claims
        if not complete:
            warnings.append("incomplete_extraction")
        return ExtractionResult(
            propositions=propositions,
            entity_mentions=mentions,
            ambiguous_claims=ambiguous_claims,
            extractor_warnings=list(dict.fromkeys(warnings)),
            input_chars=len(generated_text),
            covered_chars=covered_chars,
            chunk_count=len(chunks),
            failed_chunks=failed_chunks,
            complete=complete,
        )

    def build_extraction_prompt(self, context: dict) -> str:
        """构建 LLM 用户提示词。

        项目实体只能作为输入数据进入，不得固化进系统提示词。
        """
        generated_text = context.get("generated_text", "")
        scene_contract = context.get("scene_contract", {})
        fact_contract = context.get("fact_contract", {})
        scene_provenance = context.get("scene_provenance", "")
        character_cards = context.get("character_cards", [])
        known_entities = context.get("known_entities", [])

        parts: list[str] = []

        # 已知实体列表（作为输入数据，非固化）
        if known_entities:
            entity_lines = []
            for ent in known_entities:
                if isinstance(ent, dict):
                    name = ent.get("name", "")
                    etype = ent.get("entity_type", "unknown")
                    entity_lines.append(f"- {name}（{etype}）")
                else:
                    entity_lines.append(f"- {ent}")
            parts.append("已知实体：\n" + "\n".join(entity_lines))
        else:
            parts.append("已知实体：无")

        # 角色卡摘要
        if character_cards:
            card_lines = []
            for card in character_cards:
                if isinstance(card, dict):
                    cname = card.get("name", "")
                    card_lines.append(f"- {cname}")
                else:
                    card_lines.append(f"- {card}")
            parts.append("出场角色：\n" + "\n".join(card_lines))

        # 场景来源
        if scene_provenance:
            parts.append(f"场景来源：{scene_provenance}")

        # 场景合同中的事实约束提示
        if fact_contract:
            fc_hints = self._summarize_fact_contract(fact_contract)
            if fc_hints:
                parts.append("事实约束提示：\n" + fc_hints)

        # 场景合同中的关键信息
        if scene_contract:
            sc_hint = self._summarize_scene_contract(scene_contract)
            if sc_hint:
                parts.append("场景合同摘要：\n" + sc_hint)

        # 正文
        parts.append("待抽取文本：\n" + generated_text)

        return "\n\n".join(parts)

    def parse_extraction_response(
        self, response: str, context: dict
    ) -> ExtractionResult:
        """解析 LLM 返回的 JSON 响应为 ExtractionResult。"""
        project_id = context.get("project_id", "")
        chapter_number = context.get("chapter_number", 0)
        scene_index = context.get("scene_index", 0)
        generated_text = context.get("generated_text", "")
        strict_source_text = bool(context.get("strict_source_text", False))

        try:
            parsed = parse_json_response(response)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning("[NarrativePropositionExtractor] JSON 解析失败: %s", e)
            recovered, salvage_failures = _salvage_proposition_payload(response)
            if not recovered:
                return ExtractionResult(
                    ambiguous_claims=salvage_failures,
                    extractor_warnings=["json_parse_failed"],
                )
            parsed = {"propositions": recovered}
            salvage_warnings = [f"json_salvage_{len(recovered)}_recovered"]
            if salvage_failures:
                salvage_warnings.append(
                    f"partial_parse_{len(salvage_failures)}_failed"
                )
        else:
            salvage_failures = []
            salvage_warnings = []

        if not isinstance(parsed, dict):
            logger.warning("[NarrativePropositionExtractor] LLM 返回非 dict 类型")
            return ExtractionResult(extractor_warnings=["json_parse_failed"])

        raw_propositions = parsed.get("propositions", [])
        if not isinstance(raw_propositions, list):
            logger.warning("[NarrativePropositionExtractor] propositions 字段非 list")
            return ExtractionResult(extractor_warnings=["json_parse_failed"])

        propositions: list[NarrativeProposition] = []
        entity_mentions: list[NarrativeEntity] = []
        ambiguous_claims: list[dict] = list(salvage_failures)
        warnings: list[str] = list(salvage_warnings)

        for idx, raw in enumerate(raw_propositions):
            if not isinstance(raw, dict):
                continue
            try:
                prop = self._build_proposition(
                    raw,
                    project_id,
                    chapter_number,
                    scene_index,
                    generated_text,
                    strict_source_text,
                    context.get("source_chunk_index"),
                )
                propositions.append(prop)

                # 收集实体提及
                self._collect_entity_mentions(
                    raw, entity_mentions
                )
            except Exception as e:
                logger.debug(
                    "[NarrativePropositionExtractor] 第 %d 条命题构建失败: %s",
                    idx, e,
                )
                ambiguous_claims.append({
                    "index": idx,
                    "raw": raw,
                    "error": str(e),
                    "scope": "proposition",
                })

        if ambiguous_claims and not any(
            warning.startswith("partial_parse_") for warning in warnings
        ):
            warnings.append(f"partial_parse_{len(ambiguous_claims)}_failed")

        return ExtractionResult(
            propositions=propositions,
            entity_mentions=entity_mentions,
            ambiguous_claims=ambiguous_claims,
            extractor_warnings=warnings,
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _get_llm_client(self) -> LLMClient | None:
        """通过 AgentConfigManager 获取 LLM 客户端。"""
        try:
            from app.services.agent_config import AgentConfigManager

            manager = AgentConfigManager()
            config = await manager.get_agent_config("core_generation")
            return LLMClient(
                api_format=config.api_format,
                api_key=config.api_key,
                base_url=config.base_url,
                model=config.model,
            )
        except Exception as e:
            logger.error("[NarrativePropositionExtractor] 创建 LLM 客户端失败: %s", e)
            return None

    async def _validator_retry(
        self,
        llm: LLMClient,
        raw_response: str,
        context: dict,
        parse_errors: list[dict] | None = None,
        valid_propositions: list[NarrativeProposition] | None = None,
    ) -> ExtractionResult | None:
        """用 LLM 修复 JSON 格式后重新解析。"""
        parse_errors = [
            item for item in (parse_errors or []) if isinstance(item, dict)
        ]
        document_retry = (
            not parse_errors
            or any(str(item.get("scope") or "proposition") == "document" for item in parse_errors)
        )
        full_source_text = str(context.get("generated_text") or "")[:6000]
        tail_retry = bool(document_retry and valid_propositions and full_source_text)
        if tail_retry:
            covered_end = 0
            search_cursor = 0
            for proposition in valid_propositions or []:
                source_span = str(proposition.source_text or "")
                start = full_source_text.find(source_span, search_cursor)
                if start < 0:
                    start = full_source_text.find(source_span)
                if start >= 0:
                    covered_end = max(covered_end, start + len(source_span))
                    search_cursor = start + len(source_span)
            source_start = max(0, covered_end - 240)
            source_text = full_source_text[source_start:]
            retry_max_tokens = min(
                4096,
                max(2048, len(source_text) * 2),
            )
            repair_mode = (
                "Valid propositions before this point are already preserved. "
                "Extract every missing proposition from SOURCE TEXT TAIL, including "
                "a corrected replacement for any malformed tail object. Do not repeat "
                "facts that occur only before this tail."
            )
            invalid_material = json.dumps(
                [
                    {
                        "index": item.get("index"),
                        "error": item.get("error", ""),
                        "raw": item.get("raw"),
                    }
                    for item in parse_errors[:8]
                ],
                ensure_ascii=False,
                default=str,
            )[:5000]
        elif document_retry:
            retry_max_tokens = 6144
            source_text = full_source_text
            repair_mode = (
                "Re-extract the complete proposition set from SOURCE TEXT. "
                "The previous document may be truncated; do not rely on it for completeness."
            )
            invalid_material = json.dumps(
                [{"error": item.get("error", "")} for item in parse_errors[:8]],
                ensure_ascii=False,
            )
        else:
            retry_max_tokens = min(
                6144,
                max(2048, len(parse_errors) * 512),
            )
            source_text = full_source_text
            repair_mode = (
                "Return corrected replacements only for the invalid proposition objects below. "
                "Do not repeat valid propositions; the caller will merge these replacements."
            )
            invalid_material = json.dumps(
                [
                    {
                        "index": item.get("index"),
                        "error": item.get("error", ""),
                        "raw": item.get("raw"),
                    }
                    for item in parse_errors[:12]
                ],
                ensure_ascii=False,
                default=str,
            )[:6000]
        retry_prompt = _COMPACT_VALIDATOR_RETRY_PROMPT.replace(
            "{repair_mode}", repair_mode
        ).replace("{invalid_material}", invalid_material)
        if source_text:
            retry_prompt += (
                "\n\nSOURCE TEXT (source_text in every proposition must be copied "
                "verbatim as an exact contiguous substring):\n"
                + source_text
            )
        try:
            repair_response = await llm.generate(
                system_prompt="你是 JSON 修复器。只输出合法 JSON。",
                user_prompt=retry_prompt,
                temperature=0.1,
                max_tokens=retry_max_tokens,
                response_format={"type": "json_object"},
                task_type=LLMTaskType.JSON_AUDIT,
            )
            if not repair_response or not repair_response.strip():
                return None
            result = self.parse_extraction_response(repair_response, context)
            minimum_repaired = max(
                1,
                sum(
                    1
                    for item in parse_errors
                    if str(item.get("scope") or "proposition") == "proposition"
                ),
            )
            if (
                result.propositions
                and not result.ambiguous_claims
                and "json_parse_failed" not in result.extractor_warnings
                and (
                    document_retry
                    or len(result.propositions) >= minimum_repaired
                )
            ):
                return result
            return None
        except Exception as e:
            logger.warning("[NarrativePropositionExtractor] validator_retry 失败: %s", e)
            return None

    @staticmethod
    def _merge_partial_repair(
        original: ExtractionResult,
        repaired: ExtractionResult,
    ) -> ExtractionResult:
        """Merge repaired invalid objects without regenerating valid propositions."""
        propositions = list(original.propositions)
        seen = {
            (
                proposition.subject.name,
                proposition.predicate.name,
                proposition.object.name if proposition.object else "",
                proposition.source_text,
            )
            for proposition in propositions
        }
        for proposition in repaired.propositions:
            marker = (
                proposition.subject.name,
                proposition.predicate.name,
                proposition.object.name if proposition.object else "",
                proposition.source_text,
            )
            if marker not in seen:
                propositions.append(proposition)
                seen.add(marker)

        mentions = list(original.entity_mentions)
        mention_names = {mention.name for mention in mentions}
        for mention in repaired.entity_mentions:
            if mention.name not in mention_names:
                mentions.append(mention)
                mention_names.add(mention.name)

        warnings = [
            warning
            for warning in original.extractor_warnings
            if warning != "json_parse_failed"
            and not warning.startswith("partial_parse_")
        ]
        warnings.append(
            f"partial_repair_{len(repaired.propositions)}_recovered"
        )
        return ExtractionResult(
            propositions=propositions,
            entity_mentions=mentions,
            ambiguous_claims=[],
            extractor_warnings=list(dict.fromkeys(warnings)),
        )

    def _build_proposition(
        self,
        raw: dict,
        project_id: str,
        chapter_number: int,
        scene_index: int,
        generated_text: str = "",
        strict_source_text: bool = False,
        source_chunk_index: int | None = None,
    ) -> NarrativeProposition:
        """从原始 dict 构建 NarrativeProposition。"""
        # subject
        raw_subject = raw.get("subject", {})
        if isinstance(raw_subject, str):
            raw_subject = {"name": raw_subject}
        subject = NarrativeEntity(
            name=str(raw_subject.get("name", "")),
            entity_type=self._clamp_enum(
                raw_subject.get("entity_type", "unknown"), _ENTITY_TYPES, "unknown"
            ),
            entity_id=raw_subject.get("entity_id"),
            aliases=raw_subject.get("aliases", []),
        )

        # predicate
        raw_predicate = raw.get("predicate", {})
        if isinstance(raw_predicate, str):
            raw_predicate = {"name": raw_predicate, "category": "action"}
        predicate = NarrativePredicate(
            name=str(raw_predicate.get("name", "")),
            category=self._clamp_enum(
                raw_predicate.get("category", "action"), _PREDICATE_CATEGORIES, "action"
            ),
        )

        # object
        raw_object = raw.get("object")
        obj: NarrativeEntity | None = None
        if raw_object is not None:
            if isinstance(raw_object, str):
                raw_object = {"name": raw_object}
            if isinstance(raw_object, dict) and raw_object.get("name"):
                obj = NarrativeEntity(
                    name=str(raw_object.get("name", "")),
                    entity_type=self._clamp_enum(
                        raw_object.get("entity_type", "unknown"),
                        _ENTITY_TYPES, "unknown",
                    ),
                    entity_id=raw_object.get("entity_id"),
                    aliases=raw_object.get("aliases", []),
                )

        # confidence
        try:
            confidence = float(raw.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.0

        source_text = str(raw.get("source_text", "")).strip()
        if not source_text:
            raise ValueError("source_text is required")
        if strict_source_text and generated_text and source_text not in generated_text:
            aligned_source_text = align_exact_source_span(
                source_text,
                generated_text,
                anchors=[
                    subject.name,
                    predicate.name,
                    obj.name if obj else "",
                ],
            )
            if not aligned_source_text:
                raise ValueError("source_text must be an exact substring of generated_text")
            source_text = aligned_source_text

        return NarrativeProposition(
            proposition_id=str(uuid.uuid4()),
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=scene_index,
            subject=subject,
            predicate=predicate,
            object=obj,
            truth_layer=self._clamp_enum(
                raw.get("truth_layer", "current"), _TRUTH_LAYERS, "current"
            ),
            certainty=self._clamp_enum(
                raw.get("certainty", "unknown"), _CERTAINTIES, "unknown"
            ),
            polarity=self._clamp_enum(
                raw.get("polarity", "affirmed"), _POLARITIES, "affirmed"
            ),
            responsibility=self._clamp_enum(
                raw.get("responsibility", "unknown"), _RESPONSIBILITIES, "unknown"
            ),
            time_scope=str(raw.get("time_scope", "")),
            location_scope=str(raw.get("location_scope", "")),
            source_text=source_text,
            confidence=confidence,
            lifecycle_status=self._infer_lifecycle(raw, predicate.category),
            valid_from_chapter=chapter_number if chapter_number else None,
            importance=self._clamp_float(raw.get("importance", confidence or 0.5), 0.5),
            last_confirmed_chapter=chapter_number if chapter_number else None,
            source_chunk_index=source_chunk_index,
        )

    @staticmethod
    def _infer_lifecycle(raw: dict, category: str) -> str:
        explicit = str(raw.get("lifecycle_status") or "")
        if explicit in {"current", "superseded", "historical_event", "unresolved", "planned", "retracted"}:
            return explicit
        truth = str(raw.get("truth_layer") or "current")
        if category == "clue":
            return "unresolved"
        if category == "intention" or truth in {"plan", "future_hint"}:
            return "planned"
        if category in {"action", "event"}:
            return "historical_event"
        return "current"

    @staticmethod
    def _clamp_float(value: Any, default: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _collect_entity_mentions(
        raw: dict, mentions: list[NarrativeEntity]
    ) -> None:
        """从原始命题中收集实体提及，去重追加。"""
        existing_names = {m.name for m in mentions}

        for key in ("subject", "object"):
            raw_ent = raw.get(key)
            if not raw_ent or not isinstance(raw_ent, dict):
                continue
            name = raw_ent.get("name", "")
            if not name or name in existing_names:
                continue
            mentions.append(NarrativeEntity(
                name=name,
                entity_type=raw_ent.get("entity_type", "unknown"),
                entity_id=raw_ent.get("entity_id"),
                aliases=raw_ent.get("aliases", []),
            ))
            existing_names.add(name)

    @staticmethod
    def _clamp_enum(value: Any, allowed: list[str], default: str) -> str:
        """将值限制在枚举合法范围内。"""
        if isinstance(value, str) and value in allowed:
            return value
        return default

    @staticmethod
    def _summarize_fact_contract(fact_contract: Any) -> str:
        """从 fact_contract 中提取对抽取有用的提示信息。"""
        if isinstance(fact_contract, dict):
            fc = fact_contract
        elif hasattr(fact_contract, "model_dump"):
            fc = fact_contract.model_dump()
        else:
            return ""

        lines: list[str] = []

        current_facts = fc.get("current_facts", [])
        if current_facts:
            lines.append("当前事实：" + "；".join(str(f) for f in current_facts[:10]))

        reference_facts = fc.get("reference_facts", [])
        if reference_facts:
            lines.append("参考事实：" + "；".join(str(f) for f in reference_facts[:10]))

        uncertain_facts = fc.get("uncertain_facts", [])
        if uncertain_facts:
            lines.append("不确定事实：" + "；".join(str(f) for f in uncertain_facts[:10]))

        forbidden = fc.get("forbidden_assertions", [])
        if forbidden:
            lines.append("禁止断言：" + "；".join(
                str(f.get("event_type", f) if isinstance(f, dict) else f)
                for f in forbidden[:5]
            ))

        return "\n".join(lines)

    @staticmethod
    def _summarize_scene_contract(scene_contract: Any) -> str:
        """从 scene_contract 中提取对抽取有用的提示信息。"""
        if isinstance(scene_contract, dict):
            sc = scene_contract
        elif hasattr(scene_contract, "model_dump"):
            sc = scene_contract.model_dump()
        else:
            return ""

        lines: list[str] = []

        for key in ("scene_goal", "conflict", "expected_outcome", "time_period"):
            val = sc.get(key, "")
            if val:
                label = {
                    "scene_goal": "场景目标",
                    "conflict": "核心冲突",
                    "expected_outcome": "预期结果",
                    "time_period": "时间背景",
                }.get(key, key)
                lines.append(f"{label}：{val}")

        return "\n".join(lines)
