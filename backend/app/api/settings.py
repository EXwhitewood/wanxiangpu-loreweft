"""
API 配置模块 (Settings API)
============================

本模块是「万象谱」系统的 API 密钥与 Agent 配置的唯⼀管理入口。
其他模块（大纲、生成、世界观等）通过 AgentConfigManager 获取配置，
不直接操作本模块的内部逻辑。

架构原则
--------
1. **明文存储，脱敏传输**: API Key 在本地文件中明文存储，仅在返回给前端时脱敏。
   脱敏格式为 `sk-ab****123`（前4后4）。

2. **脱敏 Key 自动解析**: 前端发送的 API Key 可能是脱敏格式（因为页面刷新后
   前端只持有脱敏值）。后端所有接收 api_key 的端点都必须处理这种情况：
   - `update_settings`: 脱敏 key → 保留存储中的原值不变
   - `test_connection`: 脱敏 key → 从存储读取真实 key 进行测试
   - `update_agent_config`: 脱敏 key → 保留原值不变

3. **前端无需持有真实 Key**: 前端永远只展示脱敏 key。测试连接时直接发送
   脱敏 key，后端自动解析。唯一的例外是「显示完整密钥」功能，
   通过 /api-key 端点单独获取。

4. **模块解耦**: 本模块不依赖任何加密或其他模块的内部实现。
   其他模块通过以下公共接口访问配置：
   - `AgentConfigManager.load_settings()` → 获取完整设置
   - `AgentConfigManager.get_agent_config(agent_name)` → 获取 Agent 的最终生效配置
   - `AgentConfigManager.save_settings(settings)` → 保存设置

数据流
------
保存流程:
  前端(localSettings) → PUT /settings → update_settings()
    → 检测脱敏 key → 保留原值 → 存储明文 → 返回脱敏

读取流程:
  GET /settings → load_settings() → 脱敏 → 返回前端

测试连接流程:
  前端(可能带脱敏key) → POST /test-connection → test_connection()
    → 检测脱敏 key → 从存储读取真实 key → 发起 HTTP 测试 → 返回结果

Agent 配置获取流程:
  其他模块 → AgentConfigManager.get_agent_config(name)
    → 读取本地文件 → 合并全局+覆盖 → 返回 APIConfig（明文 api_key）

存储位置
--------
- `data/agent_settings.json`: 存储完整的 AppSettings JSON（明文）
"""

import copy
import json
import logging
from pathlib import Path

from fastapi import APIRouter

from app.config import settings as app_settings
from app.models.api_config import AppSettings, AgentOverride, APIConfig
from app.models.entertainment import EntertainmentSettings, get_default_settings
from app.services.agent_config import AgentConfigManager
from app.services.llm_client import LLMClient
from app.utils.atomic_file import atomic_write_text

router = APIRouter()
logger = logging.getLogger(__name__)

# 娱乐设置存储路径（独立于 agent_settings.json）
_ENTERTAINMENT_SETTINGS_FILE = Path(app_settings.data_dir) / "entertainment_settings.json"

MASK_MARKER = "****"


def _system_info_payload() -> dict:
    """获取系统信息（标准模式下的简化版本，高级模式已弃用）。"""
    return {
        "app_version": app_settings.app_version,
        "app_mode": "standard",
        "configured_app_mode": "standard",
        "is_standard_mode": True,
        "core_backend": "sqlite",
        "shell_backend": "sqlite_vss",
        "workflow_backend": "persistent",
        "configured_core_backend": "sqlite",
        "configured_shell_backend": "sqlite_vss",
        "configured_workflow_backend": "persistent",
        "database_url": app_settings.database_url,
        "configured_database_url": app_settings.database_url,
        "restart_required": False,
        "is_tauri": app_settings.app_runtime == "tauri",
    }


@router.get(
    "/system-info",
    summary="获取系统信息",
    description="获取当前运行模式、后端配置等系统信息。",
)
async def get_system_info():
    return _system_info_payload()


def _mask_api_key(key: str) -> str:
    if not key or len(key) < 8:
        return MASK_MARKER if key else ""
    return key[:4] + MASK_MARKER + key[-4:]


def _is_masked(key: str) -> bool:
    return bool(key) and MASK_MARKER in key


def _mask_settings(data: dict) -> dict:
    result = copy.deepcopy(data)
    global_settings = result.get("global", {})
    for fmt in ("openai_compatible", "anthropic_compatible"):
        cfg = global_settings.get(fmt)
        if cfg and cfg.get("api_key"):
            cfg["api_key"] = _mask_api_key(cfg["api_key"])
    for _name, override in result.get("agent_overrides", {}).items():
        if override and override.get("api_key"):
            override["api_key"] = _mask_api_key(override["api_key"])
    return result


async def _resolve_stored_settings() -> dict:
    manager = AgentConfigManager()
    settings = await manager.load_settings()
    return settings.model_dump(by_alias=True)


@router.get(
    "",
    summary="获取全局设置",
    description="获取应用程序的全局设置。API 密钥已脱敏处理（sk-ab****123 格式）。",
)
async def get_settings():
    raw = await _resolve_stored_settings()
    return _mask_settings(raw)


@router.put(
    "",
    summary="更新全局设置",
    description="更新全局设置。如果 API 密钥为脱敏格式（包含 ****），则保留存储中的原值不变。",
)
async def update_settings(settings_data: dict):
    existing_raw = await _resolve_stored_settings()

    global_settings = settings_data.get("global", {})
    for fmt in ("openai_compatible", "anthropic_compatible"):
        cfg = global_settings.get(fmt)
        if cfg and cfg.get("api_key"):
            if _is_masked(cfg["api_key"]):
                existing_key = existing_raw.get("global", {}).get(fmt, {}).get("api_key", "")
                cfg["api_key"] = existing_key

    for name, override in settings_data.get("agent_overrides", {}).items():
        if override and override.get("api_key"):
            if _is_masked(override["api_key"]):
                existing_key = existing_raw.get("agent_overrides", {}).get(name, {}).get("api_key", "")
                override["api_key"] = existing_key

    settings = AppSettings(**settings_data)
    await AgentConfigManager().save_settings(settings)

    saved_raw = settings.model_dump(by_alias=True)
    return _mask_settings(saved_raw)


@router.get(
    "/agents",
    summary="获取所有 Agent 配置",
    description="获取所有智能体的 API 配置列表（密钥已脱敏）。",
)
async def list_agents():
    manager = AgentConfigManager()
    agent_names = await manager.list_agents()
    result = []
    for name in agent_names:
        config = await manager.get_agent_config(name)
        config_dict = config.model_dump()
        config_dict["api_key"] = _mask_api_key(config_dict["api_key"])
        result.append({
            "name": name,
            "config": config_dict,
        })
    return result


@router.put(
    "/agents/{agent_name}",
    summary="更新 Agent 配置",
    description="为指定智能体设置 API 配置覆盖。如果 API 密钥为脱敏格式，则保留原值不变。",
)
async def update_agent_config(agent_name: str, override_data: dict):
    manager = AgentConfigManager()
    settings = await manager.load_settings()

    if override_data.get("api_key") and _is_masked(override_data["api_key"]):
        existing_override = settings.agent_overrides.get(agent_name)
        if existing_override and existing_override.api_key:
            override_data["api_key"] = existing_override.api_key
        else:
            override_data["api_key"] = ""

    override = AgentOverride(**override_data)
    settings.agent_overrides[agent_name] = override
    await manager.save_settings(settings)

    config = await manager.get_agent_config(agent_name)
    config_dict = config.model_dump()
    config_dict["api_key"] = _mask_api_key(config_dict["api_key"])
    return {
        "agent_name": agent_name,
        "config": config_dict,
    }


@router.post(
    "/test-connection",
    summary="测试 API 连接",
    description=(
        "测试指定的 API 配置是否可用。"
        "如果 API 密钥为脱敏格式（包含 ****），将自动从存储中读取真实密钥进行测试。"
        "前端无需在测试前获取真实密钥。"
    ),
)
async def test_connection(config_data: dict):
    try:
        api_format = config_data.get("api_format", "openai_compatible")
        api_key = config_data.get("api_key", "")
        base_url = config_data.get("base_url", "").rstrip("/")
        model = config_data.get("model", "")

        if _is_masked(api_key):
            stored = await _resolve_stored_settings()
            stored_key = stored.get("global", {}).get(api_format, {}).get("api_key", "")
            if not stored_key:
                for _name, override in stored.get("agent_overrides", {}).items():
                    if override and isinstance(override, dict) and override.get("api_key") and not _is_masked(override["api_key"]):
                        stored_key = override["api_key"]
                        break
            if stored_key:
                api_key = stored_key
            else:
                return {"success": False, "message": "无法解析脱敏密钥，请重新输入 API 密钥"}

        if not api_key:
            return {"success": False, "message": "请输入 API 密钥"}

        if not base_url:
            return {"success": False, "message": "请输入 Base URL"}

        if not model:
            return {"success": False, "message": "请输入模型名称"}

        llm_client = LLMClient(
            api_format=api_format,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        await llm_client.generate(
            system_prompt="你是一个连接测试助手。",
            user_prompt="Hi",
            temperature=0,
            max_tokens=5,
            timeout=30.0,
        )
        return {"success": True, "message": "连接成功"}
    except Exception as e:
        detail = str(e).strip() or repr(e)
        return {
            "success": False,
            "message": f"连接失败: {type(e).__name__}: {detail}",
        }


# ====================================================================
# 娱乐设置 API（等待娱乐功能：跳转外部平台 + 经典小游戏）
# ====================================================================
# 独立存储于 data/entertainment_settings.json，与 API 密钥配置解耦。
# 端点挂载在已注册的 /api/settings 前缀下，无需修改 main.py。
# --------------------------------------------------------------------

async def _load_entertainment_settings() -> EntertainmentSettings:
    """从本地文件加载娱乐设置；文件不存在时返回默认值"""
    try:
        if _ENTERTAINMENT_SETTINGS_FILE.exists():
            raw = _ENTERTAINMENT_SETTINGS_FILE.read_text(encoding="utf-8")
            data = json.loads(raw)
            return EntertainmentSettings(**data)
    except Exception as e:
        logger.warning(f"加载娱乐设置失败，回退到默认值: {e}")
    return get_default_settings()


async def _save_entertainment_settings(settings: EntertainmentSettings) -> None:
    """持久化娱乐设置到本地文件"""
    atomic_write_text(
        _ENTERTAINMENT_SETTINGS_FILE,
        settings.model_dump_json(indent=2),
    )


@router.get(
    "/entertainment",
    summary="获取娱乐设置",
    description="获取等待娱乐功能的配置，包括启用开关、跳转链接列表和已启用的小游戏。",
)
async def get_entertainment_settings():
    settings = await _load_entertainment_settings()
    return settings.model_dump()


@router.put(
    "/entertainment",
    summary="更新娱乐设置",
    description="更新等待娱乐功能的完整配置。前端发送完整的 EntertainmentSettings 对象覆盖存储。",
)
async def update_entertainment_settings(settings_data: dict):
    settings = EntertainmentSettings(**settings_data)
    await _save_entertainment_settings(settings)
    return settings.model_dump()


@router.post(
    "/entertainment/reset",
    summary="重置娱乐设置",
    description="重置娱乐设置为默认值（恢复内置链接列表和默认开关状态）。",
)
async def reset_entertainment_settings():
    settings = get_default_settings()
    await _save_entertainment_settings(settings)
    return settings.model_dump()
