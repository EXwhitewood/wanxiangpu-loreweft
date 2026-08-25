import logging

from fastapi import APIRouter, HTTPException, UploadFile, File, Response
import yaml

from app.models.api_config import AgentOverride
from app.services.agent_config import (
    AgentConfigManager,
    AGENT_REGISTRY,
    SKILL_REGISTRY,
    _load_custom_skills,
    _save_custom_skills,
)

router = APIRouter()
logger = logging.getLogger(__name__)
MASK_MARKER = "****"


def _invalidate_skill_registry_cache() -> None:
    """Make custom Skill mutations visible to the next registry read."""
    from app.services.agent_skill_registry import get_skill_registry

    get_skill_registry().invalidate_cache()


def _mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) < 8:
        return MASK_MARKER
    return key[:4] + MASK_MARKER + key[-4:]


def _is_masked_api_key(key: object) -> bool:
    return isinstance(key, str) and MASK_MARKER in key


def _mask_agent_detail(detail: dict | None) -> dict | None:
    """Return an agent detail payload without exposing the effective API key."""
    if detail is None:
        return None
    result = dict(detail)
    config = dict(result.get("config") or {})
    config["api_key"] = _mask_api_key(str(config.get("api_key") or ""))
    result["config"] = config
    return result


@router.get(
    "",
    summary="获取所有智能体",
    description="获取所有智能体的详细信息列表，包括名称、描述、图标、关联技能和当前配置。",
)
async def list_agents():
    manager = AgentConfigManager()
    settings = await manager.load_settings()
    result = []
    for name in AGENT_REGISTRY:
        detail = await manager.get_agent_detail(name, _cached_settings=settings)
        if detail:
            result.append(_mask_agent_detail(detail))
    return result


@router.get(
    "/skills",
    summary="获取所有技能",
    description="获取所有可用技能的列表，包括内置技能和自定义技能。",
)
async def list_skills():
    from app.services.agent_skill_registry import get_skill_registry

    registry = get_skill_registry()
    manifests = await registry.list_manifests()
    # Load bodies for custom skills to include in detail
    from app.services.agent_skill_loader import AgentSkillLoader
    loader = AgentSkillLoader()
    skills = []
    for manifest in manifests:
        # For custom skills, try to load body from custom skills store
        detail = manifest.description
        if manifest.source == "custom":
            try:
                skill_obj = await loader.load(manifest)
                if skill_obj.body:
                    detail = skill_obj.body
            except Exception:
                pass
        skills.append({
            "name": manifest.id,
            "display_name": manifest.display_name or manifest.name,
            "description": manifest.description,
            "detail": detail,
            "category": manifest.category,
            "agent": manifest.agents[0] if manifest.agents else None,
            "agents": manifest.agents,
            "kind": manifest.kind,
            "domain": manifest.domain,
            "source": manifest.source,
            "format_version": manifest.format_version,
            "source_format": manifest.source_format,
            "enabled_by_default": manifest.enabled_by_default,
            "validators": manifest.validators,
            "has_validators": bool(manifest.validators),
            "has_constraints": bool(manifest.constraints),
            "has_repair_hooks": bool(manifest.repair_hooks),
            "has_repair_strategies": bool(manifest.repair_strategies),
        })
    return skills


@router.post(
    "/skills",
    summary="创建自定义技能",
    description="创建一个新的自定义技能。可指定 category 为 utility（通用）或 agent（专属），专属技能需指定 agent 字段。",
)
async def create_skill(skill_data: dict):
    name = skill_data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="技能名称不能为空")
    if name in SKILL_REGISTRY:
        raise HTTPException(status_code=409, detail="该名称已被内置技能占用")
    custom = await _load_custom_skills()
    if name in custom:
        raise HTTPException(status_code=409, detail="该名称已存在")
    skill = {
        "name": name,
        "display_name": skill_data.get("display_name", name),
        "description": skill_data.get("description", ""),
        "category": skill_data.get("category", "utility"),
        "manifest": {
            "name": name,
            "display_name": skill_data.get("display_name", name),
            "description": skill_data.get("description", ""),
            "version": str(skill_data.get("version") or "1"),
            "format_version": "open_skill_v1",
            "source_format": "x-loreweft",
            "kind": "prompt",
            "domain": skill_data.get("domain", "general"),
            "category": skill_data.get("category", "utility"),
            "agents": [skill_data.get("agent")] if skill_data.get("agent") else [],
            "priority": int(skill_data.get("priority") or 60),
            "enabled_by_default": False,
            "runtime": {"kind": "prompt", "min_permission": "readonly"},
            "constraints": skill_data.get("constraints") or {},
            "validators": skill_data.get("validators") or [],
            "repair_hooks": skill_data.get("repair_hooks") or [],
        },
    }
    if skill["category"] == "agent":
        skill["agent"] = skill_data.get("agent", "")
    body = skill_data.get("body") or skill_data.get("persona") or ""
    if body:
        skill["body"] = body
    custom[name] = skill
    await _save_custom_skills(custom)
    _invalidate_skill_registry_cache()
    return skill


@router.delete(
    "/skills/{skill_name}",
    summary="删除自定义技能",
    description="删除指定的自定义技能。内置技能不可删除。",
)
async def delete_skill(skill_name: str):
    if skill_name in SKILL_REGISTRY:
        raise HTTPException(status_code=409, detail="内置技能不可删除")
    custom = await _load_custom_skills()
    if skill_name not in custom:
        raise HTTPException(status_code=404, detail="技能不存在")
    del custom[skill_name]
    await _save_custom_skills(custom)
    _invalidate_skill_registry_cache()
    return {"message": "已删除"}


def _parse_skill_md(content: str) -> dict | None:
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    body = parts[2].strip()
    try:
        meta = yaml.safe_load(parts[1].strip()) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    extension = meta.get("x-loreweft") or {}
    if not isinstance(extension, dict):
        extension = {}
    name = extension.get("id") or meta.get("id") or meta.get("name", "")
    if not name:
        return None
    requested_kind = str(extension.get("kind") or meta.get("kind") or "prompt")
    runtime = extension.get("runtime") if isinstance(extension.get("runtime"), dict) else {}
    import_warnings: list[str] = []
    if requested_kind != "prompt" or runtime.get("service_class") or runtime.get("tool_names"):
        import_warnings.append("External executable runtime was disabled; imported as prompt-only.")
        requested_kind = "prompt"
        runtime = {
            "kind": "prompt",
            "min_permission": "readonly",
            "execution_phase": "pre_generation",
        }
    agents = extension.get("agents") if isinstance(extension.get("agents"), list) else []
    manifest = {
        **extension,
        "name": meta.get("name") or name,
        "display_name": extension.get("display_name") or meta.get("display_name") or meta.get("name", name),
        "description": meta.get("description", ""),
        "version": str(meta.get("version") or extension.get("version") or "1"),
        "format_version": "open_skill_v1",
        "source_format": "x-loreweft",
        "kind": requested_kind,
        "agents": agents,
        "runtime": runtime or {"kind": requested_kind, "min_permission": "readonly"},
    }
    return {
        "name": name,
        "display_name": manifest["display_name"],
        "description": meta.get("description", ""),
        "category": extension.get("category") or meta.get("category", "utility"),
        "body": body,
        "manifest": manifest,
        "import_warnings": import_warnings,
    }


@router.post(
    "/skills/import",
    summary="从文件导入技能",
    description="上传 SKILL.md 或 JSON 文件导入技能。支持单个或多个文件。",
)
async def import_skills(
    files: list[UploadFile] = File(...),
    category: str = "utility",
    agent: str = "",
):
    results = []
    custom = await _load_custom_skills()
    for f in files:
        filename = f.filename or ""
        raw = await f.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            results.append({"filename": filename, "error": "文件编码错误，请使用 UTF-8"})
            continue

        if filename.endswith(".md"):
            parsed = _parse_skill_md(text)
            if not parsed:
                results.append({"filename": filename, "error": "SKILL.md 格式无效，需要 YAML frontmatter"})
                continue
            skill_data = parsed
        elif filename.endswith(".json"):
            try:
                import json
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    for item in parsed:
                        results.append(_import_single_skill(item, category, agent, custom))
                    await _save_custom_skills(custom)
                    _invalidate_skill_registry_cache()
                    return results
                skill_data = parsed
            except Exception as e:
                results.append({"filename": filename, "error": f"JSON 解析失败: {str(e)}"})
                continue
        else:
            results.append({"filename": filename, "error": "不支持的文件格式，请使用 .md 或 .json"})
            continue

        skill_data["category"] = category
        if category == "agent":
            skill_data["agent"] = agent
        result = _import_single_skill(skill_data, category, agent, custom)
        result["filename"] = filename
        results.append(result)

    await _save_custom_skills(custom)
    _invalidate_skill_registry_cache()
    return results


def _import_single_skill(skill_data: dict, category: str, agent: str, custom: dict) -> dict:
    name = skill_data.get("name", "").strip()
    if not name:
        return {"error": "技能名称不能为空", "name": ""}
    if name in SKILL_REGISTRY:
        return {"error": "该名称已被内置技能占用", "name": name}
    if name in custom:
        return {"error": "该名称已存在", "name": name}
    skill = {
        "name": name,
        "display_name": skill_data.get("display_name", name),
        "description": skill_data.get("description", ""),
        "category": category,
    }
    manifest = skill_data.get("manifest")
    if isinstance(manifest, dict):
        manifest = dict(manifest)
        manifest["category"] = category
        if agent:
            manifest["agents"] = list(dict.fromkeys([*(manifest.get("agents") or []), agent]))
        skill["manifest"] = manifest
    else:
        skill["manifest"] = {
            "name": name,
            "display_name": skill["display_name"],
            "description": skill["description"],
            "version": "1",
            "format_version": "open_skill_v1",
            "source_format": "x-loreweft",
            "kind": "prompt",
            "domain": "general",
            "category": category,
            "agents": [agent] if agent else [],
            "priority": 60,
            "enabled_by_default": False,
            "runtime": {"kind": "prompt", "min_permission": "readonly"},
        }
    if category == "agent":
        skill["agent"] = agent
    if skill_data.get("persona"):
        skill["body"] = skill_data["persona"]
    elif skill_data.get("body"):
        skill["body"] = skill_data["body"]
    if skill_data.get("import_warnings"):
        skill["import_warnings"] = skill_data["import_warnings"]
    custom[name] = skill
    return skill


@router.get(
    "/{agent_name}",
    summary="获取智能体详情",
    description="获取指定智能体的详细信息，包括配置、关联技能等。",
)
async def get_agent(agent_name: str):
    manager = AgentConfigManager()
    detail = await manager.get_agent_detail(agent_name)
    if not detail:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _mask_agent_detail(detail)


@router.put(
    "/{agent_name}",
    summary="更新智能体配置",
    description="更新指定智能体的覆盖配置，包括 API 配置和启用的技能列表。",
)
async def update_agent(agent_name: str, override_data: dict):
    manager = AgentConfigManager()
    settings = await manager.load_settings()

    override_data = dict(override_data)
    if _is_masked_api_key(override_data.get("api_key")):
        existing_override = settings.agent_overrides.get(agent_name)
        override_data["api_key"] = (
            existing_override.api_key
            if existing_override is not None and existing_override.api_key
            else None
        )

    override = AgentOverride(**override_data)
    settings.agent_overrides[agent_name] = override
    await manager.save_settings(settings)

    detail = await manager.get_agent_detail(agent_name)
    return _mask_agent_detail(detail)


@router.delete(
    "/{agent_name}",
    summary="重置智能体配置",
    description="删除指定智能体的覆盖配置，恢复为默认值。",
)
async def reset_agent(agent_name: str):
    manager = AgentConfigManager()
    settings = await manager.load_settings()

    if agent_name in settings.agent_overrides:
        del settings.agent_overrides[agent_name]
        await manager.save_settings(settings)

    detail = await manager.get_agent_detail(agent_name)
    return _mask_agent_detail(detail)


@router.get("/skills/{skill_id}")
async def get_skill_detail(skill_id: str):
    """Get full skill detail including body by skill ID."""
    from app.services.agent_skill_registry import get_skill_registry
    from app.services.agent_skill_loader import AgentSkillLoader

    registry = get_skill_registry()
    manifest = await registry.get_manifest(skill_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    loader = AgentSkillLoader()
    skill = await loader.load(manifest)

    return {
        "manifest": manifest.model_dump(),
        "body": skill.body,
        "prompt_sections": skill.prompt_sections,
        "references": skill.references,
        "resource_packs": skill.resource_packs,
        "package_files": _list_skill_package_files(manifest.path),
        "format_version": manifest.format_version,
        "source_format": manifest.source_format,
        "source": manifest.source,
    }


@router.put("/skills/{skill_id}")
async def update_skill_detail(skill_id: str, payload: dict):
    """Update the editable body of a skill."""
    from pathlib import Path

    from app.services.agent_skill_registry import get_skill_registry
    from app.services.agent_skill_loader import AgentSkillLoader

    body = str(payload.get("body") or "")
    registry = get_skill_registry()
    manifest = await registry.get_manifest(skill_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    if manifest.source == "custom":
        custom = await _load_custom_skills()
        item = custom.get(skill_id)
        if not isinstance(item, dict):
            raise HTTPException(status_code=404, detail=f"Custom skill not found: {skill_id}")
        item["body"] = body
        custom[skill_id] = item
        await _save_custom_skills(custom)
    elif manifest.path:
        skill_md = Path(manifest.path) / "SKILL.md"
        if not skill_md.exists():
            raise HTTPException(status_code=404, detail=f"SKILL.md not found: {skill_id}")
        current = skill_md.read_text(encoding="utf-8")
        if current.startswith("---"):
            parts = current.split("---", 2)
            if len(parts) >= 3:
                content = f"---{parts[1]}---\n{body.strip()}\n"
            else:
                content = body
        else:
            content = body
        from app.utils.atomic_file import atomic_write_text

        atomic_write_text(skill_md, content)
    else:
        raise HTTPException(status_code=400, detail="Skill source is not editable")

    registry.invalidate_cache()
    manifest = await registry.get_manifest(skill_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Skill not found after update: {skill_id}")
    skill = await AgentSkillLoader().load(manifest)
    return {
        "manifest": manifest.model_dump(),
        "body": skill.body,
        "prompt_sections": skill.prompt_sections,
        "references": skill.references,
        "resource_packs": skill.resource_packs,
        "package_files": _list_skill_package_files(manifest.path),
        "format_version": manifest.format_version,
        "source_format": manifest.source_format,
        "source": manifest.source,
    }


@router.get("/skills/{skill_id}/export")
async def export_skill(skill_id: str):
    """Export a skill as a portable SKILL.md file."""
    from pathlib import Path

    from app.services.agent_skill_loader import AgentSkillLoader
    from app.services.agent_skill_registry import get_skill_registry

    registry = get_skill_registry()
    manifest = await registry.get_manifest(skill_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    if manifest.path:
        skill_md = Path(manifest.path) / "SKILL.md"
        if skill_md.exists():
            content = skill_md.read_text(encoding="utf-8")
        else:
            content = ""
    else:
        content = ""

    if not content:
        skill = await AgentSkillLoader().load(manifest)
        extension = {
            "id": manifest.id,
            "display_name": manifest.display_name or manifest.name,
            "format_version": "open_skill_v1",
            "kind": manifest.kind,
            "domain": manifest.domain,
            "category": manifest.category,
            "agents": manifest.agents,
            "priority": manifest.priority,
            "enabled_by_default": manifest.enabled_by_default,
            "triggers": manifest.triggers.model_dump(),
            "runtime": manifest.runtime.model_dump(),
            "constraints": manifest.constraints,
            "validators": manifest.validators,
            "repair_strategies": manifest.repair_strategies,
            "repair_hooks": manifest.repair_hooks,
            "references": manifest.reference_files,
            "resource_packs": manifest.resource_pack_files,
        }
        frontmatter = {
            "name": manifest.id.replace("_", "-"),
            "description": manifest.description,
            "version": manifest.version,
            "x-loreweft": extension,
        }
        content = (
            "---\n"
            f"{yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()}\n"
            "---\n\n"
            f"{skill.body.strip()}\n"
        )

    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{skill_id}-SKILL.md"',
        },
    )


def _list_skill_package_files(path: str) -> list[dict]:
    if not path:
        return []
    try:
        from pathlib import Path

        root = Path(path)
        if not root.exists() or not root.is_dir():
            return []
        files: list[dict] = []
        for item in root.rglob("*"):
            if not item.is_file():
                continue
            try:
                rel = item.relative_to(root).as_posix()
            except ValueError:
                rel = item.name
            files.append({
                "path": rel,
                "size": item.stat().st_size,
            })
        return sorted(files, key=lambda item: item["path"])
    except OSError:
        return []


@router.post("/{agent_name}/skills/preview")
async def preview_agent_skills(agent_name: str, context: dict | None = None):
    """Preview which skills would be activated for an agent with given context."""
    from app.models.agent_skill import SkillSelectionContext
    from app.services.agent_skill_runtime import get_skill_runtime

    # P3-4 修复：避免可变默认参数 dict = {} 的状态污染风险
    if context is None:
        context = {}

    if isinstance(context.get("context"), dict):
        context = context["context"]

    runtime = get_skill_runtime()
    manager = AgentConfigManager()
    detail = await manager.get_agent_detail(agent_name)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_name}")

    selection_ctx = SkillSelectionContext(
        agent_name=agent_name,
        enabled_skills=context.get("enabled_skills", detail.get("enabled_skills")),
        enabled_agent_skills=context.get("enabled_agent_skills", detail.get("enabled_agent_skills")),
        **{
            k: v
            for k, v in context.items()
            if k in SkillSelectionContext.model_fields
            and k not in {"agent_name", "enabled_skills", "enabled_agent_skills"}
        },
    )

    packet = await runtime.prepare(
        agent_name=agent_name,
        context=selection_ctx,
    )

    return {
        "active_skills": packet.active_skills,
        "activation_reasons": packet.activation_reasons,
        "rendered_system_preview": packet.render_system(),
        "rendered_user_preview": packet.render_user(),
        "execution_plan": packet.execution_plan,
        "tool_permissions": packet.tool_permissions,
        "validators": packet.validators,
        "validation_contracts": packet.validation_contracts,
        "repair_hooks": packet.repair_hooks,
        "must_avoid": packet.must_avoid,
        "trace": packet.trace,
    }
