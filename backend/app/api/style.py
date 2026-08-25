import io
import uuid
import asyncio
import zipfile
import xml.etree.ElementTree as ET
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from app.services.memory_core import CoreMemoryService
from app.agents.style_learner import StyleLearnerAgent, LEARNING_STEPS
from app.utils.word_count import count_words

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/{project_id}/profiles")
async def list_style_profiles(project_id: str):
    service = CoreMemoryService()
    profiles = await service.list_style_profiles(project_id)
    return profiles


@router.get("/{project_id}/profiles/{profile_id}")
async def get_style_profile(project_id: str, profile_id: str):
    service = CoreMemoryService()
    profile = await service.get_style_profile(project_id, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="风格画像不存在")
    return profile


@router.post("/{project_id}/learn")
async def learn_style_from_book(
    project_id: str,
    file: UploadFile = File(...),
    name: str = Form("未命名风格"),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="未选择文件")

    suffix = _get_suffix(file.filename)
    if suffix not in (".txt", ".epub"):
        raise HTTPException(status_code=400, detail="仅支持 .txt 和 .epub 格式")

    content = await file.read()

    try:
        book_text = _parse_book(content, suffix)
    except Exception as e:
        # P2-28 修复：脱敏 detail，错误详情仅记日志
        logger.error("文件解析失败: %s", e)
        raise HTTPException(status_code=400, detail="操作失败，请稍后重试")

    if len(book_text) < 500:
        raise HTTPException(status_code=400, detail="文本太短，至少需要500字")

    service = CoreMemoryService()
    profile = await service.create_style_profile(project_id, {
        "name": name,
        "source_books": [{
            "id": str(uuid.uuid4()),
            "filename": file.filename,
            "word_count": count_words(book_text),
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }],
        "status": "learning",
        "active": False,
        "learning_progress": {
            "current_step_index": 0,
            "current_step_key": "prepare",
            "message": "正在准备上传与解析",
            "total_steps": len(LEARNING_STEPS),
        },
    })

    async def _run_learning():
        pid = project_id
        profile_id = profile["id"]
        svc = CoreMemoryService()

        async def _progress_callback(step_index: int, message: str):
            step_key = LEARNING_STEPS[step_index]["key"] if step_index < len(LEARNING_STEPS) else "save"
            await svc.update_style_profile(pid, profile_id, {
                "learning_progress": {
                    "current_step_index": step_index,
                    "current_step_key": step_key,
                    "message": message,
                    "total_steps": len(LEARNING_STEPS),
                },
            })

        try:
            agent = StyleLearnerAgent(progress_callback=_progress_callback)
            result = await agent.execute({
                "book_text": book_text,
                "name": name,
            })

            if not result.get("success"):
                await svc.update_style_profile(pid, profile_id, {
                    "status": "failed",
                    "learning_progress": None,
                })
                return

            await svc.update_style_profile(pid, profile_id, {
                "style_features": result["style_features"],
                "style_embedding": result.get("style_embedding", {}),
                "persona_card": result.get("persona_card", {}),
                "style_prompt": result["style_prompt"],
                "sample_passages": result["sample_passages"],
                "style_statistics": result.get("style_statistics", {}),
                "evolution_report": result.get("evolution_report", {}),
                "confidence": result.get("confidence", 0.0),
                "status": "ready",
                "learning_progress": None,
            })

        except Exception as e:
            logger.error(f"Style learning failed for profile {profile_id}: {e}")
            try:
                await svc.update_style_profile(pid, profile_id, {
                    "status": "failed",
                    "learning_progress": None,
                })
            except Exception as exc:
                logger.warning("[style] update profile to failed failed: %s", exc)

    asyncio.create_task(_run_learning())

    return profile


@router.get("/{project_id}/learn-status/{profile_id}")
async def get_style_learning_status(project_id: str, profile_id: str):
    service = CoreMemoryService()
    profile = await service.get_style_profile(project_id, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="风格画像不存在")

    progress = profile.get("learning_progress")
    status = profile.get("status", "learning")

    return {
        "profile_id": profile_id,
        "status": status,
        "learning_progress": progress,
        "name": profile.get("name", ""),
    }


@router.put("/{project_id}/profiles/{profile_id}")
async def update_style_profile(project_id: str, profile_id: str, data: dict):
    service = CoreMemoryService()
    updated = await service.update_style_profile(project_id, profile_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="风格画像不存在")
    return updated


@router.post("/{project_id}/profiles/{profile_id}/activate")
async def activate_style_profile(project_id: str, profile_id: str):
    service = CoreMemoryService()
    result = await service.activate_style_profile(project_id, profile_id)
    if not result:
        raise HTTPException(status_code=404, detail="风格画像不存在")
    return result


@router.delete("/{project_id}/profiles/{profile_id}")
async def delete_style_profile(project_id: str, profile_id: str):
    service = CoreMemoryService()
    deleted = await service.delete_style_profile(project_id, profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="风格画像不存在")
    return {"message": "风格画像已删除"}


@router.get("/{project_id}/active")
async def get_active_style(project_id: str):
    service = CoreMemoryService()
    profile = await service.get_active_style_profile(project_id)
    if not profile:
        return {"active": False, "profile": None}
    return {"active": True, "profile": profile}


@router.get("/{project_id}/profiles/{profile_id}/statistics")
async def get_style_statistics(project_id: str, profile_id: str):
    service = CoreMemoryService()
    statistics = await service.get_style_statistics(project_id, profile_id)
    if statistics is None:
        raise HTTPException(status_code=404, detail="风格画像不存在")
    return statistics


@router.get("/{project_id}/profiles/{profile_id}/embedding")
async def get_style_embedding(project_id: str, profile_id: str):
    service = CoreMemoryService()
    embedding = await service.get_style_embedding(project_id, profile_id)
    if embedding is None:
        raise HTTPException(status_code=404, detail="风格画像不存在")
    return embedding


@router.get("/{project_id}/profiles/{profile_id}/evolution")
async def get_style_evolution(project_id: str, profile_id: str):
    service = CoreMemoryService()
    evolution = await service.get_style_evolution(project_id, profile_id)
    if evolution is None:
        raise HTTPException(status_code=404, detail="风格画像不存在")
    return evolution


def _get_suffix(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename[filename.rfind("."):].lower()


def _parse_book(content: bytes, suffix: str) -> str:
    if suffix == ".txt":
        for encoding in ("utf-8", "gbk", "gb2312", "big5"):
            try:
                return content.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        raise ValueError("无法识别文本编码")
    elif suffix == ".epub":
        try:
            text_parts = []
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for name in sorted(zf.namelist()):
                    if name.endswith(('.html', '.xhtml', '.htm')):
                        with zf.open(name) as f:
                            tree = ET.parse(f)
                            root = tree.getroot()
                            for elem in root.iter():
                                if elem.text and elem.text.strip():
                                    text_parts.append(elem.text.strip())
            return "\n".join(text_parts)
        except Exception as e:
            raise ValueError(f"EPUB解析失败：{str(e)}")
    else:
        raise ValueError(f"不支持的格式：{suffix}")


EMBEDDING_DIMENSION_LABELS = {
    "emotionality": "情感外显度",
    "sentence_complexity": "句式复杂度",
    "narrative_distance": "叙事距离",
    "info_density": "信息密度",
    "dialogue_ratio": "对话占比",
    "description_density": "描写密度",
    "rhythm_steepness": "节奏陡峭度",
    "narrator_intrusion": "叙事者介入度",
}

CONFLICT_THRESHOLDS = {
    "L1": 0.3,
    "L2": 0.6,
    "L3": 0.8,
    "L4": 1.01,
}


def _classify_conflict(delta: float) -> str:
    if delta <= CONFLICT_THRESHOLDS["L1"]:
        return "L1"
    elif delta <= CONFLICT_THRESHOLDS["L2"]:
        return "L2"
    elif delta <= CONFLICT_THRESHOLDS["L3"]:
        return "L3"
    else:
        return "L4"


@router.post("/{project_id}/profiles/compare")
async def compare_style_profiles(project_id: str, data: dict):
    profile_ids = data.get("profile_ids", [])
    if len(profile_ids) < 2:
        raise HTTPException(status_code=400, detail="至少需要两个风格画像进行比较")

    service = CoreMemoryService()
    profiles = []
    for pid in profile_ids:
        p = await service.get_style_profile(project_id, pid)
        if not p:
            raise HTTPException(status_code=404, detail=f"风格画像 {pid} 不存在")
        profiles.append(p)

    conflicts = []
    emb_a = profiles[0].get("style_embedding", {})
    emb_b = profiles[1].get("style_embedding", {})
    persona_a = profiles[0].get("persona_card", {})
    persona_b = profiles[1].get("persona_card", {})

    for dim_key, dim_label in EMBEDDING_DIMENSION_LABELS.items():
        val_a = emb_a.get(dim_key, 0.0)
        val_b = emb_b.get(dim_key, 0.0)
        delta = abs(val_a - val_b)
        if delta > CONFLICT_THRESHOLDS["L1"]:
            conflicts.append({
                "dimension": dim_key,
                "dimension_label": dim_label,
                "value_a": val_a,
                "value_b": val_b,
                "delta": round(delta, 3),
                "level": _classify_conflict(delta),
                "type": "numeric",
            })

    rules_a = persona_a.get("hard_rules", [])
    rules_b = persona_b.get("hard_rules", [])
    if rules_a and rules_b and set(rules_a) != set(rules_b):
        conflicts.append({
            "dimension": "hard_rules",
            "dimension_label": "硬规则",
            "value_a": len(rules_a),
            "value_b": len(rules_b),
            "delta": 0.0,
            "level": "L4" if (rules_a and rules_b) else "L1",
            "type": "rule",
            "rule_conflict": {"rule_a": rules_a, "rule_b": rules_b},
        })

    total_dims = len(EMBEDDING_DIMENSION_LABELS)
    conflict_count = len([c for c in conflicts if c["type"] == "numeric"])
    compatibility_score = round(max(0.0, 1.0 - conflict_count / total_dims), 2)

    return {
        "profile_a_id": profiles[0]["id"],
        "profile_a_name": profiles[0]["name"],
        "profile_b_id": profiles[1]["id"],
        "profile_b_name": profiles[1]["name"],
        "conflicts": conflicts,
        "compatibility_score": compatibility_score,
    }


@router.get("/{project_id}/profiles/{profile_id}/conflicts")
async def get_style_conflicts(project_id: str, profile_id: str):
    service = CoreMemoryService()
    target = await service.get_style_profile(project_id, profile_id)
    if not target:
        raise HTTPException(status_code=404, detail="风格画像不存在")

    all_profiles = await service.list_style_profiles(project_id)
    reports = []
    for other in all_profiles:
        if other["id"] == profile_id:
            continue
        emb_a = target.get("style_embedding", {})
        emb_b = other.get("style_embedding", {})
        conflicts = []
        for dim_key, dim_label in EMBEDDING_DIMENSION_LABELS.items():
            val_a = emb_a.get(dim_key, 0.0)
            val_b = emb_b.get(dim_key, 0.0)
            delta = abs(val_a - val_b)
            if delta > CONFLICT_THRESHOLDS["L1"]:
                conflicts.append({
                    "dimension": dim_key,
                    "dimension_label": dim_label,
                    "value_a": val_a,
                    "value_b": val_b,
                    "delta": round(delta, 3),
                    "level": _classify_conflict(delta),
                    "type": "numeric",
                })
        if conflicts:
            total_dims = len(EMBEDDING_DIMENSION_LABELS)
            compatibility_score = round(max(0.0, 1.0 - len(conflicts) / total_dims), 2)
            reports.append({
                "profile_a_id": target["id"],
                "profile_a_name": target["name"],
                "profile_b_id": other["id"],
                "profile_b_name": other["name"],
                "conflicts": conflicts,
                "compatibility_score": compatibility_score,
            })
    return reports


@router.post("/{project_id}/profiles/merge")
async def merge_style_profiles(project_id: str, data: dict):
    source_ids = data.get("source_profile_ids", [])
    weights = data.get("weights", [])
    mode = data.get("mode", "negotiation")
    dominant_id = data.get("dominant_profile_id")
    negotiation_targets = data.get("negation_targets", {})

    if len(source_ids) < 2:
        raise HTTPException(status_code=400, detail="至少需要两个风格画像进行融合")

    if len(weights) != len(source_ids):
        total = len(source_ids)
        weights = [1.0 / total] * total

    service = CoreMemoryService()
    profiles = []
    for sid in source_ids:
        p = await service.get_style_profile(project_id, sid)
        if not p:
            raise HTTPException(status_code=404, detail=f"风格画像 {sid} 不存在")
        profiles.append(p)

    merged_embedding = {}
    for dim_key in EMBEDDING_DIMENSION_LABELS:
        if negotiation_targets and dim_key in negotiation_targets:
            merged_embedding[dim_key] = negotiation_targets[dim_key]
        else:
            weighted_sum = sum(
                profiles[i].get("style_embedding", {}).get(dim_key, 0.0) * weights[i]
                for i in range(len(profiles))
            )
            merged_embedding[dim_key] = round(weighted_sum, 3)

    merged_features = {}
    for key in ["vocabulary", "sentence_structure", "tone", "pacing", "description_style", "dialogue_style", "narrative_voice"]:
        if mode == "arbitration" and dominant_id:
            dominant_profile = next((p for p in profiles if p["id"] == dominant_id), profiles[0])
            merged_features[key] = dominant_profile.get("style_features", {}).get(key, "")
        else:
            merged_features[key] = profiles[0].get("style_features", {}).get(key, "")

    all_signature_phrases = []
    all_avoid_patterns = []
    seen_sig = set()
    seen_avoid = set()
    for p in profiles:
        for phrase in p.get("style_features", {}).get("signature_phrases", []):
            if phrase not in seen_sig:
                all_signature_phrases.append(phrase)
                seen_sig.add(phrase)
        for pattern in p.get("style_features", {}).get("avoid_patterns", []):
            if pattern not in seen_avoid:
                all_avoid_patterns.append(pattern)
                seen_avoid.add(pattern)
    merged_features["signature_phrases"] = all_signature_phrases[:10]
    merged_features["avoid_patterns"] = all_avoid_patterns[:8]

    dominant_profile = None
    if dominant_id:
        dominant_profile = next((p for p in profiles if p["id"] == dominant_id), None)

    merged_persona = {}
    if dominant_profile:
        merged_persona = dominant_profile.get("persona_card", {})
    else:
        merged_persona = profiles[0].get("persona_card", {}) if profiles else {}

    source_names = [p["name"] for p in profiles]
    merged_name = f"融合风格（{' + '.join(source_names[:3])}）"
    merged_statistics = {
        "global": {
            "source_profile_count": len(profiles),
            "source_names": source_names,
            "mode": mode,
        },
        "chapter_curves": {},
        "scene_distribution": {},
        "evolution": {
            "change_points": [],
            "style_clusters": [],
            "is_multi_style": len(profiles) > 1,
            "sub_profiles": [],
            "explanation": "融合后画像由多个源风格组合而成",
            "recommended_usage": "适合作为统一输出风格，或作为后续精炼的基础",
        },
    }

    new_profile = await service.create_style_profile(project_id, {
        "name": merged_name,
        "source_books": [],
        "style_features": merged_features,
        "style_embedding": merged_embedding,
        "persona_card": merged_persona,
        "style_prompt": "",
        "style_statistics": merged_statistics,
        "evolution_report": {
            "change_points": [],
            "style_clusters": [],
            "is_multi_style": len(profiles) > 1,
            "sub_profiles": [],
            "explanation": "当前融合结果默认视作复合风格",
            "recommended_usage": "建议在创作台中检查章节适配范围",
        },
        "confidence": 0.5,
        "version": 1,
        "active": False,
        "status": "ready",
        "domains": [],
        "parent_profile_ids": source_ids,
    })

    return {
        "profile": new_profile,
        "sample_text": "",
        "similarity_description": f"融合了{'与'.join(source_names[:3])}的风格特征",
    }


@router.post("/{project_id}/profiles/{profile_id}/freeze")
async def freeze_style_profile(project_id: str, profile_id: str):
    service = CoreMemoryService()
    profile = await service.get_style_profile(project_id, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="风格画像不存在")
    updated = await service.freeze_style_profile(project_id, profile_id)
    return updated


@router.post("/{project_id}/profiles/{profile_id}/editor-influence")
async def set_style_profile_editor_influence(project_id: str, profile_id: str, data: dict):
    service = CoreMemoryService()
    profile = await service.get_style_profile(project_id, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="风格画像不存在")
    enabled = bool(data.get("enabled", True))
    updated = await service.set_style_profile_editor_influence(project_id, profile_id, enabled)
    return updated


@router.post("/{project_id}/profiles/{profile_id}/rollback")
async def rollback_style_profile(project_id: str, profile_id: str):
    service = CoreMemoryService()
    profile = await service.get_style_profile(project_id, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="风格画像不存在")
    current_version = profile.get("version", 1)
    if current_version <= 1:
        raise HTTPException(status_code=400, detail="已经是初始版本，无法回滚")
    updated = await service.update_style_profile(project_id, profile_id, {
        "version": current_version - 1,
    })
    return updated
