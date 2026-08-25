import json
import re
import uuid
from collections import Counter, defaultdict
from statistics import mean, median, pstdev

from app.agents.base import BaseAgent
from app.services.llm_task_profiles import LLMTaskType

import logging

logger = logging.getLogger(__name__)

LEARNING_STEPS = [
    {"key": "prepare", "label": "准备", "desc": "确认名称与上传文件"},
    {"key": "upload", "label": "上传", "desc": "接收并解析原始文本"},
    {"key": "scan", "label": "扫描", "desc": "提取句式、节奏与场景分布"},
    {"key": "distill", "label": "蒸馏", "desc": "挑选代表性片段"},
    {"key": "refine", "label": "精炼", "desc": "生成风格特征与人格画像"},
    {"key": "evolve", "label": "演变", "desc": "检测多风格簇与变化点"},
    {"key": "save", "label": "入库", "desc": "写入风格画像与激活信息"},
]

try:
    import numpy as np
except Exception:  # pragma: no cover - optional dependency
    np = None

try:
    import jieba
except Exception:  # pragma: no cover - optional dependency
    jieba = None

try:
    from sklearn.cluster import KMeans
except Exception:  # pragma: no cover - optional dependency
    KMeans = None

try:
    import ruptures as rpt
except Exception:  # pragma: no cover - optional dependency
    rpt = None


class StyleLearnerAgent(BaseAgent):
    name = "style_learner"

    def __init__(self, progress_callback=None):
        super().__init__()
        self._progress_callback = progress_callback

    async def _report_progress(self, step_index: int, message: str):
        if self._progress_callback:
            try:
                await self._progress_callback(step_index, message)
            except Exception as e:
                logger.warning(f"Progress callback error: {e}")

    async def execute(self, context: dict) -> dict:
        book_text = context.get("book_text", "")
        profile_name = context.get("name", "未命名风格")

        if not book_text or len(book_text) < 500:
            return {
                "success": False,
                "error": "文本太短，至少需要500字才能分析风格",
            }

        await self._report_progress(0, "正在准备上传与解析")

        scan_result = self._scan_text(book_text)

        await self._report_progress(2, "正在扫描全文统计特征")

        distill_result = self._distill_passages(scan_result)

        await self._report_progress(3, "正在蒸馏代表性片段")

        evolution_report = scan_result.get("evolution_report", {})

        try:
            llm = await self.get_llm_client()
        except Exception:
            llm = None

        if llm:
            refined = await self._refine_with_experts(
                llm=llm,
                scan_result=scan_result,
                distill_result=distill_result,
                profile_name=profile_name,
            )
        else:
            refined = self._fallback_refine(scan_result, distill_result, profile_name)

        await self._report_progress(4, "正在精炼风格特征与人格画像")

        style_features = refined["style_features"]
        style_embedding = scan_result["style_embedding"]
        persona_card = refined["persona_card"]
        style_prompt = refined["style_prompt"]
        selected_passages = distill_result["sample_passages"]

        await self._report_progress(5, "正在检测风格演变")

        await self._report_progress(6, "正在写入风格画像")

        return {
            "success": True,
            "style_features": style_features,
            "style_embedding": style_embedding,
            "persona_card": persona_card,
            "style_prompt": style_prompt,
            "sample_passages": selected_passages,
            "style_statistics": scan_result["style_statistics"],
            "evolution_report": evolution_report,
            "confidence": self._compute_confidence(
                refined["dimension_results"],
                refined["signature_phrases"],
                refined["avoid_patterns"],
                scan_result["style_statistics"],
                evolution_report,
                len(selected_passages),
            ),
        }

    def _sample_passages(self, text: str) -> dict[str, list[str]]:
        paragraphs = [p.strip() for p in text.split("\n") if p.strip() and len(p.strip()) > 50]
        if not paragraphs:
            return {"all": []}

        samples = {
            "opening": paragraphs[:3],
            "ending": paragraphs[-3:],
            "all": [],
            "action": [],
            "dialogue": [],
            "description": [],
            "emotion": [],
        }

        step = max(1, len(paragraphs) // 18)
        sampled = []
        for i in range(0, len(paragraphs), step):
            p = paragraphs[i]
            if len(p) > 100:
                sampled.append(p[:500])

        samples["all"] = sampled[:18]

        for p in paragraphs:
            dialogue_count = p.count('"') + p.count('\u201c') + p.count('\u300c') + p.count('\u201d')
            if dialogue_count >= 2 and len(samples["dialogue"]) < 3:
                samples["dialogue"].append(p[:500])
            elif any(kw in p for kw in ["剑", "拳", "打", "杀", "冲", "战", "攻", "闪"]) and len(samples["action"]) < 3:
                samples["action"].append(p[:500])
            elif any(kw in p for kw in ["山", "水", "风", "月", "花", "树", "云", "雨", "雪"]) and len(samples["description"]) < 3:
                samples["description"].append(p[:500])

        return samples

    def _scan_text(self, text: str) -> dict:
        paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
        if not paragraphs:
            return {
                "global": {
                    "total_chars": 0,
                    "paragraph_count": 0,
                    "chapter_count": 0,
                    "word_freq_top": [],
                    "sentence_length": {"mean": 0.0, "median": 0.0, "stdev": 0.0},
                    "paragraph_length": {"mean": 0.0, "median": 0.0, "stdev": 0.0},
                    "punctuation_profile": {},
                },
                "paragraph_features": [],
                "chapter_curves": {},
                "scene_distribution": {},
                "style_embedding": self._compute_style_embedding(text, {"all": []}),
                "evolution_report": {
                    "change_points": [],
                    "style_clusters": [],
                    "is_multi_style": False,
                    "sub_profiles": [],
                    "explanation": "文本过短，暂未检测到稳定的演变结构",
                    "recommended_usage": "可先继续积累文本，再进行演变建模",
                },
                "style_statistics": {},
            }

        chapter_segments = self._segment_chapters(paragraphs)
        paragraph_features: list[dict] = []
        scene_distribution = Counter()
        chapter_buckets: dict[int, list[dict]] = defaultdict(list)

        for chapter_index, segment in enumerate(chapter_segments, start=1):
            for local_index, paragraph in enumerate(segment["paragraphs"]):
                category, metrics = self._label_paragraph(paragraph)
                feature = {
                    "chapter_index": chapter_index,
                    "chapter_title": segment["title"],
                    "paragraph_index": len(paragraph_features) + 1,
                    "local_index": local_index + 1,
                    "category": category,
                    "text": paragraph[:500],
                    **metrics,
                }
                paragraph_features.append(feature)
                chapter_buckets[chapter_index].append(feature)
                scene_distribution[category] += 1

        chapter_curves: dict[str, dict] = {}
        for chapter_index, items in chapter_buckets.items():
            chapter_curves[str(chapter_index)] = {
                "chapter_index": chapter_index,
                "chapter_title": items[0].get("chapter_title", f"第{chapter_index}章"),
                "paragraph_count": len(items),
                "dominant_scene": Counter(item["category"] for item in items).most_common(1)[0][0] if items else "narrative",
                "style_embedding": self._average_embedding(
                    [{k: item.get(k, 0.0) for k in self._embedding_keys()} for item in items]
                ),
            }

        style_embedding = self._compute_style_embedding(text, self._sample_passages(text))
        evolution_report = self._detect_evolution(chapter_curves)

        change_points = set(evolution_report.get("change_points", []))
        for feature in paragraph_features:
            chapter_index = feature["chapter_index"]
            if chapter_index in change_points or (chapter_index - 1) in change_points or (chapter_index + 1) in change_points:
                feature["evolution_importance"] = 1.0
            else:
                feature["evolution_importance"] = 0.55

        total_chars = len(text)
        sentence_lengths = []
        punctuation_profile = {}
        for mark in ["，", "。", "！", "？", "；", "：", "…", "—", "“", "”", "\"", "、"]:
            punctuation_profile[mark] = text.count(mark)

        for paragraph in paragraphs:
            for sent in re.split(r"[。！？；!?]", paragraph):
                sent = sent.strip()
                if len(sent) > 1:
                    sentence_lengths.append(len(sent))

        para_lengths = [len(p) for p in paragraphs]
        word_freq_top = self._top_keywords(text, 20)

        style_statistics = {
            "global": {
                "total_chars": total_chars,
                "paragraph_count": len(paragraphs),
                "chapter_count": len(chapter_segments),
                "word_freq_top": word_freq_top,
                "sentence_length": self._describe_distribution(sentence_lengths),
                "paragraph_length": self._describe_distribution(para_lengths),
                "punctuation_profile": punctuation_profile,
            },
            "chapter_curves": chapter_curves,
            "scene_distribution": dict(scene_distribution),
            "evolution": evolution_report,
        }

        return {
            "global": style_statistics["global"],
            "paragraph_features": paragraph_features,
            "chapter_curves": chapter_curves,
            "scene_distribution": dict(scene_distribution),
            "style_embedding": style_embedding,
            "evolution_report": evolution_report,
            "style_statistics": style_statistics,
        }

    def _segment_chapters(self, paragraphs: list[str]) -> list[dict]:
        heading_pattern = re.compile(
            r"^(第[一二三四五六七八九十百千0-9]+[章节回卷篇].*|[0-9]+[\.、].*)$"
        )
        segments: list[dict] = []
        current: list[str] = []
        current_title = "开篇"

        has_heading = False
        for paragraph in paragraphs:
            if heading_pattern.match(paragraph) and len(current) >= 2:
                has_heading = True
                segments.append({
                    "title": current_title,
                    "paragraphs": current,
                })
                current = []
                current_title = paragraph[:40]
                continue
            if heading_pattern.match(paragraph) and not current:
                has_heading = True
                current_title = paragraph[:40]
                continue
            current.append(paragraph)

        if current:
            segments.append({
                "title": current_title,
                "paragraphs": current,
            })

        if has_heading and segments:
            return [seg for seg in segments if seg["paragraphs"]]

        window = max(8, len(paragraphs) // 12 or 8)
        fallback_segments: list[dict] = []
        for start in range(0, len(paragraphs), window):
            bucket = paragraphs[start:start + window]
            if not bucket:
                continue
            fallback_segments.append({
                "title": f"章节片段{len(fallback_segments) + 1}",
                "paragraphs": bucket,
            })
        return fallback_segments or [{"title": "全文", "paragraphs": paragraphs}]

    def _label_paragraph(self, paragraph: str) -> tuple[str, dict]:
        text = paragraph.strip()
        tokens = self._tokenize(text)
        token_count = len(tokens)
        unique_ratio = len(set(tokens)) / max(token_count, 1)
        emotion_keywords = ["悲", "喜", "怒", "哀", "爱", "恨", "痛", "哭", "笑", "泪", "怕", "惊", "愁", "怨", "思"]
        action_keywords = ["走", "跑", "打", "杀", "冲", "追", "推", "拉", "冲", "握", "拔", "闪", "砍", "刺"]
        description_keywords = ["如", "像", "仿佛", "宛如", "似", "苍", "暗", "静", "微", "深", "冷", "亮", "风", "雨", "雪"]
        narrator_keywords = ["显然", "事实上", "总之", "也许", "大概", "无疑", "看来"]

        dialogue_ratio = (text.count("“") + text.count("”") + text.count("\"") + text.count("「") + text.count("」")) / max(len(text), 1)
        emotion_density = sum(text.count(kw) for kw in emotion_keywords) / max(len(text), 1)
        action_density = sum(text.count(kw) for kw in action_keywords) / max(len(text), 1)
        description_density = sum(text.count(kw) for kw in description_keywords) / max(len(text), 1)
        narrator_intrusion = sum(text.count(kw) for kw in narrator_keywords) / max(len(text), 1)
        punctuation_density = sum(text.count(p) for p in "，。！？；：") / max(len(text), 1)

        sentence_parts = [s.strip() for s in re.split(r"[。！？；]", text) if s.strip()]
        sentence_complexity = min(1.0, (sum(len(s) for s in sentence_parts) / max(len(sentence_parts), 1)) / 60.0)
        info_density = min(1.0, len(set(re.findall(r"[\u4e00-\u9fff]{2,4}", text))) / max(len(text) / 8, 1))

        if dialogue_ratio > 0.015:
            category = "dialogue"
        elif action_density > 0.01:
            category = "action"
        elif emotion_density > 0.01:
            category = "emotion"
        elif description_density > 0.01:
            category = "description"
        elif len(text) < 90 or punctuation_density < 0.01:
            category = "transition"
        else:
            category = "narrative"

        return category, {
            "length": len(text),
            "token_count": token_count,
            "unique_ratio": round(unique_ratio, 3),
            "dialogue_ratio": round(dialogue_ratio, 3),
            "emotion_density": round(emotion_density, 4),
            "action_density": round(action_density, 4),
            "description_density": round(description_density, 4),
            "narrator_intrusion": round(narrator_intrusion, 4),
            "punctuation_density": round(punctuation_density, 4),
            "sentence_complexity": round(sentence_complexity, 3),
            "info_density": round(info_density, 3),
            "figurative_density": round((description_density + punctuation_density) / 2, 4),
        }

    def _tokenize(self, text: str) -> list[str]:
        if jieba:
            try:
                return [token.strip() for token in jieba.lcut(text) if token.strip()]
            except Exception:
                pass
        return re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z]+|\d+", text)

    def _top_keywords(self, text: str, limit: int = 20) -> list[dict]:
        tokens = self._tokenize(text)
        counter = Counter(token for token in tokens if len(token) > 1)
        return [
            {"word": word, "count": count}
            for word, count in counter.most_common(limit)
        ]

    def _describe_distribution(self, values: list[int | float]) -> dict:
        if not values:
            return {"mean": 0.0, "median": 0.0, "stdev": 0.0}
        if len(values) == 1:
            return {"mean": round(float(values[0]), 2), "median": round(float(values[0]), 2), "stdev": 0.0}
        return {
            "mean": round(float(mean(values)), 2),
            "median": round(float(median(values)), 2),
            "stdev": round(float(pstdev(values)), 2),
        }

    def _embedding_keys(self) -> list[str]:
        return [
            "emotionality",
            "sentence_complexity",
            "narrative_distance",
            "info_density",
            "dialogue_ratio",
            "description_density",
            "rhythm_steepness",
            "narrator_intrusion",
        ]

    def _average_embedding(self, embeddings: list[dict]) -> dict:
        if not embeddings:
            return {key: 0.0 for key in self._embedding_keys()}
        averaged = {}
        for key in self._embedding_keys():
            averaged[key] = round(sum(float(item.get(key, 0.0)) for item in embeddings) / len(embeddings), 3)
        return averaged

    def _detect_evolution(self, chapter_curves: dict[str, dict]) -> dict:
        ordered = sorted(chapter_curves.values(), key=lambda item: item.get("chapter_index", 0))
        if len(ordered) < 2:
            return {
                "change_points": [],
                "style_clusters": [],
                "is_multi_style": False,
                "sub_profiles": [],
                "explanation": "章节不足，暂未检测到明确风格演变",
                "recommended_usage": "可继续积累章节后再观察风格演变",
            }

        vectors = [[curve["style_embedding"].get(key, 0.0) for key in self._embedding_keys()] for curve in ordered]
        change_points: list[int] = []

        if rpt and np is not None and len(vectors) >= 3:
            try:
                algo = rpt.Pelt(model="l2").fit(np.array(vectors))
                bkps = algo.predict(pen=2)
                change_points = [ordered[idx - 1]["chapter_index"] for idx in bkps[:-1] if 0 < idx <= len(ordered)]
            except Exception:
                change_points = []

        if not change_points:
            for idx in range(1, len(vectors)):
                prev = vectors[idx - 1]
                curr = vectors[idx]
                delta = sum(abs(curr[i] - prev[i]) for i in range(len(curr))) / len(curr)
                if delta >= 0.18:
                    change_points.append(ordered[idx]["chapter_index"])

        style_clusters = []
        sub_profiles = []
        if KMeans and np is not None and len(vectors) >= 3:
            try:
                cluster_count = min(3, max(1, len(vectors) // 3 + 1))
                cluster_count = min(cluster_count, len(vectors))
                model = KMeans(n_clusters=cluster_count, n_init=10, random_state=42)
                labels = model.fit_predict(np.array(vectors))
                for cluster_id in range(cluster_count):
                    cluster_items = [ordered[i] for i, label in enumerate(labels) if label == cluster_id]
                    if not cluster_items:
                        continue
                    cluster_embedding = self._average_embedding([item["style_embedding"] for item in cluster_items])
                    chapter_indexes = [item["chapter_index"] for item in cluster_items]
                    title = cluster_items[0].get("chapter_title", f"子风格{cluster_id + 1}")
                    dominant_scene = Counter(item.get("dominant_scene", "narrative") for item in cluster_items).most_common(1)[0][0]
                    chapter_range = [min(chapter_indexes), max(chapter_indexes)]
                    style_clusters.append({
                        "cluster_id": cluster_id,
                        "chapter_range": chapter_range,
                        "dominant_scene": dominant_scene,
                        "style_embedding": cluster_embedding,
                        "chapter_indexes": chapter_indexes,
                    })
                    sub_profiles.append({
                        "id": str(uuid.uuid4()),
                        "name": title,
                        "chapter_range": chapter_range,
                        "style_embedding": cluster_embedding,
                        "style_prompt": f"该子风格主要出现在第{chapter_range[0]}至第{chapter_range[1]}章，叙事重心偏向{dominant_scene}。",
                    })
            except Exception:
                style_clusters = []
                sub_profiles = []

        is_multi_style = bool(style_clusters) or len(change_points) >= 2
        if is_multi_style:
            explanation = "文本中存在多个相对稳定的风格簇，建议按章节范围拆分画像。"
            recommended_usage = "推荐在创作台中按章节区间或叙事阶段启用子风格画像。"
        else:
            explanation = "当前文本整体风格较稳定，适合收敛为单一风格画像。"
            recommended_usage = "可直接作为全局风格画像使用。"

        return {
            "change_points": change_points,
            "style_clusters": style_clusters,
            "is_multi_style": is_multi_style,
            "sub_profiles": sub_profiles,
            "explanation": explanation,
            "recommended_usage": recommended_usage,
        }

    def _distill_passages(self, scan_result: dict) -> dict:
        features = scan_result.get("paragraph_features", [])
        if not features:
            return {
                "sample_passages": [],
                "prompt_samples": {"all": [], "action": [], "dialogue": [], "description": [], "emotion": [], "opening": [], "ending": []},
                "report": {"selected_count": 0, "category_counts": {}},
            }

        category_counts = Counter(feature.get("category", "narrative") for feature in features)
        lengths_by_category: dict[str, list[int]] = defaultdict(list)
        for feature in features:
            lengths_by_category[feature.get("category", "narrative")].append(int(feature.get("length", 0)))

        scored = []
        for feature in features:
            category = feature.get("category", "narrative")
            lengths = lengths_by_category.get(category, []) or [feature.get("length", 0)]
            target_length = median(lengths)
            scene_scarcity = 1.0 - (category_counts[category] / max(len(features), 1))
            typicality = 1.0 - min(1.0, abs(feature.get("length", 0) - target_length) / max(target_length, 1))
            rhetoric_density = float(feature.get("figurative_density", 0.0))
            emotion_intensity = float(feature.get("emotion_density", 0.0))
            evolution_importance = float(feature.get("evolution_importance", 0.55))
            uniqueness = float(feature.get("unique_ratio", 0.0))
            score = (
                0.25 * uniqueness
                + 0.20 * scene_scarcity
                + 0.20 * typicality
                + 0.15 * evolution_importance
                + 0.10 * rhetoric_density
                + 0.10 * emotion_intensity
            )
            scored.append((score, feature))

        scored.sort(key=lambda item: (item[0], item[1].get("chapter_index", 0)), reverse=True)

        max_total = min(24, max(12, len(features) // 25 + 12))
        selected: list[dict] = []
        per_category_limit = {
            "dialogue": 4,
            "action": 4,
            "description": 4,
            "emotion": 4,
            "narrative": 4,
            "transition": 2,
        }
        per_chapter_count: Counter[int] = Counter()
        for score, feature in scored:
            if len(selected) >= max_total:
                break
            category = feature.get("category", "narrative")
            chapter_index = int(feature.get("chapter_index", 1))
            if per_chapter_count[chapter_index] >= 2:
                continue
            if sum(1 for item in selected if item.get("category") == category) >= per_category_limit.get(category, 3):
                continue
            selected.append({
                "id": str(uuid.uuid4()),
                "text": feature.get("text", ""),
                "category": category,
                "source_book": "",
                "score": round(score, 3),
                "chapter_index": chapter_index,
            })
            per_chapter_count[chapter_index] += 1

        if len(selected) < min(12, len(features)):
            seen_ids = {item["text"] for item in selected}
            for score, feature in scored:
                if len(selected) >= max_total:
                    break
                text_key = feature.get("text", "")
                if text_key in seen_ids:
                    continue
                selected.append({
                    "id": str(uuid.uuid4()),
                    "text": text_key,
                    "category": feature.get("category", "narrative"),
                    "source_book": "",
                    "score": round(score, 3),
                    "chapter_index": int(feature.get("chapter_index", 1)),
                })
                seen_ids.add(text_key)

        prompt_samples = {
            "all": [item["text"] for item in selected[:12]],
            "action": [item["text"] for item in selected if item["category"] == "action"][:4],
            "dialogue": [item["text"] for item in selected if item["category"] == "dialogue"][:4],
            "description": [item["text"] for item in selected if item["category"] == "description"][:4],
            "emotion": [item["text"] for item in selected if item["category"] == "emotion"][:4],
            "opening": [item["text"] for item in selected if item.get("chapter_index", 1) <= 2][:3],
            "ending": [item["text"] for item in selected[-3:]],
        }

        return {
            "sample_passages": selected,
            "prompt_samples": prompt_samples,
            "report": {
                "selected_count": len(selected),
                "category_counts": dict(category_counts),
            },
        }

    async def _refine_with_experts(self, llm, scan_result: dict, distill_result: dict, profile_name: str) -> dict:
        prompt_samples = distill_result.get("prompt_samples", {})
        dimension_inputs = [
            ("vocabulary", "词汇偏好", prompt_samples.get("all", [])),
            ("sentence_structure", "句式特征", prompt_samples.get("all", [])),
            ("tone", "语气基调", prompt_samples.get("all", [])),
            ("pacing", "节奏特征", prompt_samples.get("action", []) + prompt_samples.get("dialogue", [])),
            ("description_style", "描写风格", prompt_samples.get("description", [])),
            ("dialogue_style", "对话风格", prompt_samples.get("dialogue", [])),
            ("narrative_voice", "叙事视角", prompt_samples.get("opening", []) + prompt_samples.get("all", [])),
        ]

        dimension_results = {}
        for dim_key, dim_name, dim_samples in dimension_inputs:
            if not dim_samples:
                continue
            result = await self._analyze_dimension(llm, dim_name, dim_samples)
            if result:
                dimension_results[dim_key] = result

        signature_phrases = await self._extract_signature_phrases(llm, prompt_samples.get("all", []))
        avoid_patterns = await self._extract_avoid_patterns(llm, prompt_samples.get("all", []))
        style_prompt = await self._generate_style_prompt(llm, dimension_results, signature_phrases, avoid_patterns)
        persona_card = await self._extract_persona_card(llm, prompt_samples.get("all", []), {
            "vocabulary": dimension_results.get("vocabulary", ""),
            "sentence_structure": dimension_results.get("sentence_structure", ""),
            "tone": dimension_results.get("tone", ""),
            "pacing": dimension_results.get("pacing", ""),
            "description_style": dimension_results.get("description_style", ""),
            "dialogue_style": dimension_results.get("dialogue_style", ""),
            "narrative_voice": dimension_results.get("narrative_voice", ""),
            "signature_phrases": signature_phrases,
            "avoid_patterns": avoid_patterns,
        })

        if not style_prompt:
            style_prompt = self._fallback_style_prompt(dimension_results, signature_phrases, avoid_patterns, profile_name)

        return {
            "dimension_results": dimension_results,
            "signature_phrases": signature_phrases,
            "avoid_patterns": avoid_patterns,
            "style_prompt": style_prompt,
            "persona_card": persona_card,
            "style_features": {
                "vocabulary": dimension_results.get("vocabulary", ""),
                "sentence_structure": dimension_results.get("sentence_structure", ""),
                "tone": dimension_results.get("tone", ""),
                "pacing": dimension_results.get("pacing", ""),
                "description_style": dimension_results.get("description_style", ""),
                "dialogue_style": dimension_results.get("dialogue_style", ""),
                "narrative_voice": dimension_results.get("narrative_voice", ""),
                "signature_phrases": signature_phrases,
                "avoid_patterns": avoid_patterns,
            },
        }

    def _fallback_refine(self, scan_result: dict, distill_result: dict, profile_name: str) -> dict:
        prompt_samples = distill_result.get("prompt_samples", {})
        signature_phrases = [item["text"][:24] for item in distill_result.get("sample_passages", [])[:5]]
        avoid_patterns = []
        dimension_results = {
            "vocabulary": "偏向文本中的高频词和场景词，保持稳定的叙述口径",
            "sentence_structure": "句式以文本平均句长为基线，适度保留作者惯用节奏",
            "tone": "整体语气与原文保持一致",
            "pacing": "节奏随对话、动作、描写分布自然推进",
            "description_style": "描写密度与原文观感一致",
            "dialogue_style": "对话需要保留人物口吻和潜台词",
            "narrative_voice": "叙述视角与原文保持同一叙事距离",
        }
        style_prompt = self._fallback_style_prompt(dimension_results, signature_phrases, avoid_patterns, profile_name)
        persona_card = {
            "identity": f"{profile_name}的稳定叙述者",
            "decision_pattern": "先观察场景的关键变化，再控制信息释放节奏",
            "expression_style": "兼顾原文的节奏与语言习惯，避免明显跳脱",
            "interpersonal_behavior": "与角色保持一致的叙事距离，必要时低调介入",
            "hard_rules": [],
        }
        return {
            "dimension_results": dimension_results,
            "signature_phrases": signature_phrases,
            "avoid_patterns": avoid_patterns,
            "style_prompt": style_prompt,
            "persona_card": persona_card,
            "style_features": {
                "vocabulary": dimension_results["vocabulary"],
                "sentence_structure": dimension_results["sentence_structure"],
                "tone": dimension_results["tone"],
                "pacing": dimension_results["pacing"],
                "description_style": dimension_results["description_style"],
                "dialogue_style": dimension_results["dialogue_style"],
                "narrative_voice": dimension_results["narrative_voice"],
                "signature_phrases": signature_phrases,
                "avoid_patterns": avoid_patterns,
            },
        }

    def _fallback_style_prompt(
        self,
        dimension_results: dict,
        signature_phrases: list[str],
        avoid_patterns: list[str],
        profile_name: str,
    ) -> str:
        parts = [f"风格画像：{profile_name}"]
        for key in ["vocabulary", "sentence_structure", "tone", "pacing", "description_style", "dialogue_style", "narrative_voice"]:
            value = dimension_results.get(key, "")
            if value:
                parts.append(f"- {value}")
        if signature_phrases:
            parts.append(f"- 标志性表达：{'、'.join(signature_phrases[:5])}")
        if avoid_patterns:
            parts.append(f"- 避免写法：{'、'.join(avoid_patterns[:3])}")
        return "\n".join(parts)

    async def _analyze_dimension(self, llm, dimension_name: str, samples: list[str]) -> str:
        if not samples:
            return ""
        sample_text = "\n---\n".join(samples[:3])
        system_prompt = f"你是一位文学风格分析专家。请分析以下文本片段的{dimension_name}，用简洁精准的中文描述（50字以内）。只输出描述文本，不要任何解释。"
        user_prompt = f"分析以下文本的{dimension_name}：\n\n{sample_text}"
        try:
            result = await llm.generate(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.3, task_type=LLMTaskType.SHORT_EXTRACTION)
            return result.strip()
        except Exception:
            return ""

    async def _extract_signature_phrases(self, llm, samples: list[str]) -> list[str]:
        if not samples:
            return []
        sample_text = "\n---\n".join(samples[:6])
        system_prompt = "你是一位文学风格分析专家。请从以下文本中提取5-8个最具代表性的标志性表达或短语，这些表达能体现作者独特的文风。以JSON数组格式返回，如[\"表达1\",\"表达2\"]。只输出JSON数组。"
        user_prompt = f"提取标志性表达：\n\n{sample_text}"
        try:
            result = await llm.generate(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.3, task_type=LLMTaskType.SHORT_EXTRACTION)
            match = re.search(r'\[.*\]', result, re.DOTALL)
            if match:
                return json.loads(match.group())[:8]
            return []
        except Exception:
            return []

    async def _extract_avoid_patterns(self, llm, samples: list[str]) -> list[str]:
        if not samples:
            return []
        sample_text = "\n---\n".join(samples[:6])
        system_prompt = "你是一位文学风格分析专家。基于以下文本的风格特征，列出3-5个与该风格相反的写作模式（即该作者会避免的写法）。以JSON数组格式返回，如[\"模式1\",\"模式2\"]。只输出JSON数组。"
        user_prompt = f"分析该风格应避免的写作模式：\n\n{sample_text}"
        try:
            result = await llm.generate(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.3, task_type=LLMTaskType.SHORT_EXTRACTION)
            match = re.search(r'\[.*\]', result, re.DOTALL)
            if match:
                return json.loads(match.group())[:5]
            return []
        except Exception:
            return []

    async def _generate_style_prompt(self, llm, dimensions: dict, signature_phrases: list[str], avoid_patterns: list[str]) -> str:
        dim_text = "\n".join(f"- {k}: {v}" for k, v in dimensions.items() if v)
        sig_text = "\u3001".join(signature_phrases[:5]) if signature_phrases else "无"
        avoid_text = "\u3001".join(avoid_patterns[:3]) if avoid_patterns else "无"

        system_prompt = "你是一位文学风格专家。请根据以下风格分析结果，生成一段200字以内的紧凑风格指令，用于指导AI生成文本时模仿该风格。指令应具体、可操作，包含词汇、句式、节奏、语气等关键约束。"
        user_prompt = f"""风格分析结果：
{dim_text}

标志性表达：{sig_text}
应避免模式：{avoid_text}

请生成紧凑的风格指令："""

        try:
            result = await llm.generate(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.3, task_type=LLMTaskType.SHORT_EXTRACTION)
            return result.strip()
        except Exception:
            return ""

    async def _select_representative_passages(self, llm, samples: dict[str, list[str]]) -> list[dict]:
        passages = []
        categories = ["opening", "action", "dialogue", "description", "emotion", "ending"]
        for cat in categories:
            cat_samples = samples.get(cat, [])
            for sample in cat_samples[:2]:
                if len(sample) > 100:
                    passages.append({
                        "id": str(uuid.uuid4()),
                        "text": sample[:500],
                        "category": cat,
                        "source_book": "",
                    })
        return passages[:10]

    def _compute_style_embedding(self, text: str, samples: dict[str, list[str]]) -> dict:
        paragraphs = [p.strip() for p in text.split("\n") if p.strip() and len(p.strip()) > 30]
        if not paragraphs:
            return {k: 0.0 for k in ["emotionality", "sentence_complexity", "narrative_distance", "info_density", "dialogue_ratio", "description_density", "rhythm_steepness", "narrator_intrusion"]}

        emotion_keywords = ["悲", "喜", "怒", "哀", "乐", "痛", "恨", "爱", "惧", "惊", "愁", "怨", "叹", "哭", "笑", "泪", "苦", "甜", "酸", "辣"]
        first_person_words = ["我", "我的", "我们", "自己", "内心", "感觉", "觉得", "认为", "想到", "想起"]
        action_words = ["走", "跑", "打", "杀", "冲", "站", "坐", "拿", "放", "推", "拉", "举", "挥", "砍", "刺"]
        narrator_words = ["显然", "当然", "无疑", "众所周知", "事实上", "实际上", "不得不说", "总而言之"]

        total_chars = len(text)
        emotion_count = sum(text.count(kw) for kw in emotion_keywords)
        emotionality = min(1.0, emotion_count / max(total_chars / 100, 1)) if total_chars > 0 else 0.0

        sentence_lengths = []
        for p in paragraphs:
            sentences = re.split(r'[。！？；]', p)
            for s in sentences:
                s = s.strip()
                if len(s) > 1:
                    sentence_lengths.append(len(s))
        avg_sent_len = sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0
        sentence_complexity = min(1.0, avg_sent_len / 40.0)

        fp_count = sum(text.count(w) for w in first_person_words)
        act_count = sum(text.count(w) for w in action_words)
        total_ref = fp_count + act_count
        narrative_distance = fp_count / total_ref if total_ref > 0 else 0.5

        dialogue_chars = 0
        for p in paragraphs:
            dialogue_count = p.count('"') + p.count('\u201c') + p.count('\u300c') + p.count('\u201d')
            if dialogue_count >= 2:
                dialogue_chars += len(p)
        dialogue_ratio = min(1.0, dialogue_chars / max(total_chars, 1))

        desc_keywords = ["般", "似的", "一样", "如同", "仿佛", "像", "犹如", "恰似", "宛如"]
        adj_count = sum(text.count(kw) for kw in desc_keywords)
        description_density = min(1.0, adj_count / max(total_chars / 200, 1)) if total_chars > 0 else 0.0

        para_lengths = [len(p) for p in paragraphs]
        if len(para_lengths) > 1:
            avg_len = sum(para_lengths) / len(para_lengths)
            variance = sum((l - avg_len) ** 2 for l in para_lengths) / len(para_lengths)
            rhythm_steepness = min(1.0, (variance ** 0.5) / 200.0)
        else:
            rhythm_steepness = 0.0

        narrator_count = sum(text.count(w) for w in narrator_words)
        narrator_intrusion = min(1.0, narrator_count / max(total_chars / 500, 1)) if total_chars > 0 else 0.0

        info_density = min(1.0, len(set(re.findall(r'[\u4e00-\u9fff]{2,4}', text))) / max(total_chars / 10, 1)) if total_chars > 0 else 0.0

        return {
            "emotionality": round(emotionality, 3),
            "sentence_complexity": round(sentence_complexity, 3),
            "narrative_distance": round(narrative_distance, 3),
            "info_density": round(info_density, 3),
            "dialogue_ratio": round(dialogue_ratio, 3),
            "description_density": round(description_density, 3),
            "rhythm_steepness": round(rhythm_steepness, 3),
            "narrator_intrusion": round(narrator_intrusion, 3),
        }

    async def _extract_persona_card(self, llm, samples: list[str], style_features: dict) -> dict:
        if not samples:
            return {"identity": "", "decision_pattern": "", "expression_style": "", "interpersonal_behavior": "", "hard_rules": []}
        sample_text = "\n---\n".join(samples[:4])
        features_text = "\n".join(f"- {k}: {v}" for k, v in style_features.items() if v and not isinstance(v, list))
        system_prompt = "你是一位文学叙事学专家。请根据以下文本片段和风格特征，提取叙事者的人格画像。以JSON格式返回，包含以下字段：identity（叙述者身份定位，20字以内）、decision_pattern（叙事决策模式，30字以内）、expression_style（表达风格偏好，30字以内）、interpersonal_behavior（对角色的态度与距离，30字以内）、hard_rules（叙述者绝不做的3-5条规则，JSON数组）。只输出JSON对象。"
        user_prompt = f"文本片段：\n{sample_text}\n\n风格特征：\n{features_text}\n\n请提取叙事者人格画像："
        try:
            result = await llm.generate(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.3, task_type=LLMTaskType.SHORT_EXTRACTION)
            match = re.search(r'\{.*\}', result, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                return {
                    "identity": parsed.get("identity", ""),
                    "decision_pattern": parsed.get("decision_pattern", ""),
                    "expression_style": parsed.get("expression_style", ""),
                    "interpersonal_behavior": parsed.get("interpersonal_behavior", ""),
                    "hard_rules": parsed.get("hard_rules", []),
                }
            return {"identity": "", "decision_pattern": "", "expression_style": "", "interpersonal_behavior": "", "hard_rules": []}
        except Exception:
            return {"identity": "", "decision_pattern": "", "expression_style": "", "interpersonal_behavior": "", "hard_rules": []}

    def _compute_confidence(
        self,
        dimension_results: dict,
        signature_phrases: list[str],
        avoid_patterns: list[str],
        style_statistics: dict | None = None,
        evolution_report: dict | None = None,
        sample_count: int = 0,
    ) -> float:
        dim_count = len([v for v in dimension_results.values() if v])
        dim_score = dim_count / 7.0
        sig_score = min(1.0, len(signature_phrases) / 5.0)
        avoid_score = min(1.0, len(avoid_patterns) / 3.0)
        global_stats = (style_statistics or {}).get("global", {})
        sentence_stats = global_stats.get("sentence_length", {}) if isinstance(global_stats, dict) else {}
        stability_score = 0.55
        if isinstance(sentence_stats, dict):
            stability_score = max(0.35, 1.0 - min(1.0, float(sentence_stats.get("stdev", 0.0)) / 80.0))
        evolution_score = 0.65
        if evolution_report:
            evolution_score = 0.8 if not evolution_report.get("is_multi_style") else 0.68
            if evolution_report.get("change_points"):
                evolution_score = min(1.0, evolution_score + 0.08)
        sample_score = min(1.0, sample_count / 20.0)
        confidence = (
            dim_score * 0.35
            + sig_score * 0.20
            + avoid_score * 0.15
            + stability_score * 0.15
            + evolution_score * 0.10
            + sample_score * 0.05
        )
        return round(min(1.0, confidence), 2)
