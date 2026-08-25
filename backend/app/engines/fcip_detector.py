import re
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Violation:
    rule_id: str
    severity: str
    description: str
    matched_text: str
    suggestion: str
    chapter_number: int = 0

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "description": self.description,
            "detail": self.description,
            "matched_text": self.matched_text[:200],
            "target_span": self.matched_text[:200] if self.matched_text else None,
            "suggestion": self.suggestion,
            "expected_behavior": self.suggestion,
            "fcip_category": "text_fixable",
            "chapter_number": self.chapter_number,
        }


def rule_fh01_l4_direct_access(
    generated_text: str,
    foreshadowing_name: str,
    secret_keywords: list[str],
    character_name: str,
    chapter_number: int = 0,
) -> list[Violation]:
    violations = []
    for keyword in secret_keywords:
        if keyword and keyword in generated_text:
            violations.append(Violation(
                rule_id="FH-01",
                severity="Critical",
                description=f"L4角色「{character_name}」的生成文本中出现了秘密关键词「{keyword}」",
                matched_text=f"...{keyword}...",
                suggestion=f"删除或改写该段文本，该角色不应知道关于「{foreshadowing_name}」的任何信息",
                chapter_number=chapter_number,
            ))
    return violations


def rule_fh02_l3_misattribution(
    generated_text: str,
    character_name: str,
    chapter_number: int = 0,
) -> list[Violation]:
    attribution_patterns = [
        (r"(?:觉得|感觉|怀疑)(?:这|那|它).*?(?:不对|有问题|奇怪)", "模糊感知被归因为具体判断"),
        (r"(?:一定是|肯定是|绝对是).*?(?:因为|由于)", "不确定感受被转化为因果推论"),
    ]
    violations = []
    for pattern, desc in attribution_patterns:
        for match in re.finditer(pattern, generated_text):
            violations.append(Violation(
                rule_id="FH-02",
                severity="High",
                description=f"L3角色「{character_name}」的模糊不安被归因为具体线索：{desc}",
                matched_text=match.group()[:100],
                suggestion="改为仅描写身体感受或模糊情绪，不做归因",
                chapter_number=chapter_number,
            ))
    return violations


def rule_fh03_l2_reasoning(
    generated_text: str,
    character_name: str,
    chapter_number: int = 0,
) -> list[Violation]:
    reasoning_keywords = [
        "难道", "莫非", "会不会", "该不会",
        "如果……那么", "因为……所以", "这意味着",
    ]
    violations = []
    for kw in reasoning_keywords:
        if kw in generated_text:
            violations.append(Violation(
                rule_id="FH-03",
                severity="High",
                description=f"L2角色「{character_name}」使用了推理关键词「{kw}」",
                matched_text=f"...{kw}...",
                suggestion="删除推理内容，该角色只能观察和记录线索，不能推理",
                chapter_number=chapter_number,
            ))
    return violations


def rule_fh05_l0_expository_monologue(
    generated_text: str,
    character_name: str,
    secret_statement: str,
    chapter_number: int = 0,
) -> list[Violation]:
    expository_patterns = [
        r"(?:原来如此|我知道了|我明白了|终于懂了)",
        r"(?:怪不得|难怪).*?(?:原来|是因为)",
    ]
    violations = []
    for pattern in expository_patterns:
        if re.search(pattern, generated_text):
            secret_words = [w for w in secret_statement[:8].split() if len(w) > 1]
            if any(w in generated_text for w in secret_words):
                violations.append(Violation(
                    rule_id="FH-05",
                    severity="Low",
                    description=f"L0角色「{character_name}」出现了解说式内心独白",
                    matched_text=generated_text[:100],
                    suggestion="将内心独白改为行动或对话中的自然表露",
                    chapter_number=chapter_number,
                ))
                break
    return violations


def rule_fh06_environment_over_salience(
    generated_text: str,
    chapter_number: int = 0,
) -> list[Violation]:
    over_emphasis_patterns = [
        r"(?:格外|异常|极其|惊人地)(?:显眼|引人注目|突出)",
        r"(?:不知为何|莫名其妙地).*?(?:注意|吸引)",
    ]
    violations = []
    for pattern in over_emphasis_patterns:
        for match in re.finditer(pattern, generated_text):
            violations.append(Violation(
                rule_id="FH-06",
                severity="Low",
                description="环境线索描写使用了过度强调的修饰语",
                matched_text=match.group()[:100],
                suggestion="改为中性描写，让读者自行注意而非作者强调",
                chapter_number=chapter_number,
            ))
    return violations


def rule_fh07_post_reveal_blindness(
    generated_text: str,
    character_name: str,
    character_cognitive_status: str,
    chapter_number: int = 0,
) -> list[Violation]:
    if character_cognitive_status not in ("informed", "internalized", "verified"):
        return []
    ignorance_patterns = [
        r"(?:不知道|不清楚|不明白|没听说过)",
        r"(?:毫无头绪|一头雾水|完全不懂)",
    ]
    violations = []
    for pattern in ignorance_patterns:
        for match in re.finditer(pattern, generated_text):
            violations.append(Violation(
                rule_id="FH-07",
                severity="High",
                description=f"已揭示伏笔但「{character_name}」仍表现为不知情",
                matched_text=match.group()[:100],
                suggestion="该角色应表现出已知情的状态，或添加说明为何看起来不知情",
                chapter_number=chapter_number,
            ))
    return violations


def rule_fh08_clue_repetition_too_close(
    generated_text: str,
    recent_clue_texts: list[str],
    min_chapter_distance: int = 2,
    chapter_number: int = 0,
) -> list[Violation]:
    violations = []
    for clue_text in recent_clue_texts:
        if not clue_text or len(clue_text) < 4:
            continue
        if clue_text[:6] in generated_text:
            violations.append(Violation(
                rule_id="FH-08",
                severity="Medium",
                description=f"线索「{clue_text[:20]}…」在间隔不足{min_chapter_distance}章时重复出现",
                matched_text=clue_text[:50],
                suggestion="增加线索重复间距，或换一种呈现方式",
                chapter_number=chapter_number,
            ))
    return violations


def rule_fh09_meta_term_leak(
    generated_text: str,
    chapter_number: int = 0,
) -> list[Violation]:
    meta_terms = ["伏笔", "埋设", "揭示任务", "叙事需求", "线索密度", "读者认知"]
    violations = []
    for term in meta_terms:
        if term in generated_text:
            violations.append(Violation(
                rule_id="FH-09",
                severity="Critical",
                description=f"生成文本中出现了元叙事术语「{term}」",
                matched_text=f"...{term}...",
                suggestion="删除该术语，使用场景内的自然表述替代",
                chapter_number=chapter_number,
            ))
    return violations


async def rule_fh04_l1_conclusion_deviation(
    generated_text: str,
    character_name: str,
    llm_client,
    chapter_number: int = 0,
) -> list[Violation]:
    prompt = f"""请判断以下文本中，角色「{character_name}」（L1高怀疑级别）的推理结论是否与实际真相偏差过大。

规则：L1角色可以推理，但结论必须有偏差，不能直接命中真相。

文本：
{generated_text[:2000]}

请以JSON格式回答：
{{"has_violation": true/false, "explanation": "简要说明", "conclusion_hit_truth": true/false}}"""
    try:
        response = await llm_client.generate(
            system_prompt="你是一位叙事一致性检测专家。只输出JSON，不要输出任何其他内容。",
            user_prompt=prompt,
        )
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        result = json.loads(cleaned)
    except (json.JSONDecodeError, Exception) as e:
        logger.debug("FH-04 LLM check failed: %s", e)
        return []

    if result.get("has_violation") or result.get("conclusion_hit_truth"):
        return [Violation(
            rule_id="FH-04",
            severity="Medium",
            description=f"L1角色「{character_name}」的推理结论与真相偏差不足",
            matched_text=generated_text[:200],
            suggestion="让角色得出接近但偏离真相的结论",
            chapter_number=chapter_number,
        )]
    return []


async def rule_fh10_reader_over_obscuring(
    generated_text: str,
    revealed_secrets: list[str],
    llm_client,
    chapter_number: int = 0,
) -> list[Violation]:
    if not revealed_secrets:
        return []

    prompt = f"""请判断以下文本是否存在「已揭示给读者的秘密被叙述者过度遮掩」的问题。

规则：如果之前章节已向读者揭示了某个秘密，当前章节不该再用神秘化的方式描述该秘密。

已揭示的秘密摘要：{"; ".join(revealed_secrets[:5])}

文本：
{generated_text[:2000]}

请以JSON格式回答：
{{"has_violation": true/false, "explanation": "简要说明", "over_obscured_element": "被过度遮掩的元素"}}"""
    try:
        response = await llm_client.generate(
            system_prompt="你是一位叙事一致性检测专家。只输出JSON，不要输出任何其他内容。",
            user_prompt=prompt,
        )
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        result = json.loads(cleaned)
    except (json.JSONDecodeError, Exception) as e:
        logger.debug("FH-10 LLM check failed: %s", e)
        return []

    if result.get("has_violation"):
        return [Violation(
            rule_id="FH-10",
            severity="Medium",
            description=f"第{chapter_number}章存在已揭示信息被过度遮掩的问题",
            matched_text=result.get("over_obscured_element", ""),
            suggestion="直接以已知事实为基础叙述，不需再做神秘化处理",
            chapter_number=chapter_number,
        )]
    return []


def run_deterministic_checks(
    generated_text: str,
    foreshadowing_items: list[dict],
    chapter_number: int = 0,
) -> list[Violation]:
    all_violations: list[Violation] = []

    all_violations.extend(rule_fh06_environment_over_salience(generated_text, chapter_number))
    all_violations.extend(rule_fh09_meta_term_leak(generated_text, chapter_number))

    for item in foreshadowing_items:
        char_states = item.get("character_states", [])
        secret = item.get("secret_canonical_statement", "")
        name = item.get("name", "")

        secret_keywords = [w for w in secret[:10].split() if len(w) > 1] if secret else []

        for cs in char_states:
            level = cs.get("cognitive_level", "fully_blind")
            status = cs.get("cognitive_status", "blind")
            char_name = cs.get("character_name", "")

            if level == "fully_blind":
                all_violations.extend(rule_fh01_l4_direct_access(
                    generated_text, name, secret_keywords, char_name, chapter_number,
                ))
            elif level == "vague_unease":
                all_violations.extend(rule_fh02_l3_misattribution(
                    generated_text, char_name, chapter_number,
                ))
            elif level == "partial_clue":
                all_violations.extend(rule_fh03_l2_reasoning(
                    generated_text, char_name, chapter_number,
                ))
            elif level in ("fully_aware",):
                all_violations.extend(rule_fh05_l0_expository_monologue(
                    generated_text, char_name, secret, chapter_number,
                ))

            all_violations.extend(rule_fh07_post_reveal_blindness(
                generated_text, char_name, status, chapter_number,
            ))

        recent_clues = item.get("recent_clue_texts", [])
        min_dist = item.get("repetition_distance", 2)
        all_violations.extend(rule_fh08_clue_repetition_too_close(
            generated_text, recent_clues, min_dist, chapter_number,
        ))

    return all_violations
