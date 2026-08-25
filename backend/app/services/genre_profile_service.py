"""Genre Profile 服务 —— 题材配置的统一读取与降级。

职责：
1. 从项目 genre_profile_id 定位 Profile。
2. 缓存内置 YAML，避免每次生成都读文件。
3. 校验 Profile schema，输出 warning 而不是让生成链路崩溃。
4. 合并项目级 override。
5. 为所有调用方提供同一份 normalized profile。

降级策略：Profile 系统不能成为生成链路的新单点故障。
"""

from __future__ import annotations

import logging
from pathlib import Path
from functools import lru_cache

import yaml

_logger = logging.getLogger(__name__)

_PROFILES_DIR = Path(__file__).resolve().parent.parent / "config" / "genre_profiles"

# 通用 fallback 禁忌（当 Profile 缺失或 activation_prohibitions 为空时使用）
_FALLBACK_PROHIBITIONS = [
    "不得违反已确立的世界规则",
    "不得提前揭示后续保留信息",
    "不得移除后续章节需要出场的角色",
]


class GenreProfileService:
    """题材配置的统一读取与降级服务。"""

    @lru_cache(maxsize=16)
    def _load_yaml(self, profile_id: str) -> dict | None:
        """加载内置 YAML 文件，带缓存。"""
        path = _PROFILES_DIR / f"{profile_id}.yaml"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                _logger.warning("Genre Profile %s 解析结果不是 dict，跳过", profile_id)
                return None
            return data
        except Exception as e:
            _logger.warning("Genre Profile %s 解析失败: %s", profile_id, e)
            return None

    def get_default_profile(self) -> dict:
        """返回通用默认 Profile。"""
        return self._load_yaml("general") or self._make_minimal_profile()

    async def get_project_genre_profile(
        self, project_id: str, db=None, core_data: dict | None = None,
    ) -> dict:
        """获取项目对应的 Genre Profile，带降级。

        优先级：
        1. core_data.genre_profile_id 指定的 Profile
        2. 项目 genre 字段匹配的 Profile
        3. general 默认 Profile
        """
        profile_id = None

        # 从 core_data 读取
        if core_data and isinstance(core_data, dict):
            profile_id = core_data.get("genre_profile_id")

        # 尝试从项目模型读取
        if not profile_id and db:
            try:
                from uuid import UUID

                from app.db.db_models import Project

                pid = UUID(project_id) if isinstance(project_id, str) else project_id
                project = await db.get(Project, pid)
                if project:
                    cd = project.core_data or {}
                    profile_id = cd.get("genre_profile_id")
                    if not profile_id and project.genre:
                        profile_id = self._match_genre_to_profile(project.genre)
            except Exception as e:
                _logger.warning("GenreProfileService: 读取项目信息失败: %s", e)

        # 加载 Profile
        if profile_id:
            profile = self._load_yaml(profile_id)
            if profile:
                _logger.debug("GenreProfileService: 项目 %s 使用 Profile %s", project_id, profile_id)
                return profile
            else:
                _logger.warning(
                    "GenreProfileService: Profile '%s' 不存在，降级为 general", profile_id,
                )

        return self.get_default_profile()

    def get_activation_prohibitions(self, profile: dict | None = None) -> list[str]:
        """获取激活禁忌列表，带 fallback。"""
        if not profile:
            return list(_FALLBACK_PROHIBITIONS)
        prohibitions = profile.get("activation_prohibitions", [])
        if isinstance(prohibitions, list) and len(prohibitions) > 0:
            return prohibitions
        return list(_FALLBACK_PROHIBITIONS)

    def get_category_keywords(self, profile: dict | None = None) -> dict[str, list[str]]:
        """获取规则分类关键词映射，带 fallback。"""
        if not profile:
            return {}
        keywords = profile.get("category_keywords", {})
        return keywords if isinstance(keywords, dict) else {}

    def get_location_schema(self, profile: dict | None = None) -> dict:
        """获取地点提取模式，带 fallback。"""
        if not profile:
            return {"type": "single", "fields": ["location"]}
        schema = profile.get("location_schema", {})
        if isinstance(schema, dict) and schema.get("type"):
            return schema
        return {"type": "single", "fields": ["location"]}

    def validate_profile_schema(self, profile: dict) -> list[dict]:
        """校验 Profile schema，返回 warning 列表。"""
        warnings: list[dict] = []
        if not isinstance(profile, dict):
            return [{"level": "error", "message": "Profile 不是 dict 类型"}]

        required_fields = ["id", "display_name", "activation_prohibitions"]
        for field in required_fields:
            if field not in profile:
                warnings.append({"level": "warning", "message": f"Profile 缺少字段: {field}"})

        # 校验 contract_extensions
        extensions = profile.get("contract_extensions", [])
        if isinstance(extensions, list):
            for i, ext in enumerate(extensions):
                if not isinstance(ext, dict):
                    warnings.append({"level": "warning", "message": f"contract_extensions[{i}] 不是 dict"})
                elif "field" not in ext:
                    warnings.append({"level": "warning", "message": f"contract_extensions[{i}] 缺少 field"})

        return warnings

    def merge_project_overrides(self, profile: dict, project_core_data: dict) -> dict:
        """合并项目级 override 到 Profile。"""
        if not project_core_data:
            return profile

        merged = dict(profile)

        # 项目可以追加 activation_prohibitions
        extra_prohibitions = project_core_data.get("extra_activation_prohibitions", [])
        if isinstance(extra_prohibitions, list) and extra_prohibitions:
            current = merged.get("activation_prohibitions", [])
            merged["activation_prohibitions"] = list(set(current + extra_prohibitions))

        # 项目可以追加 category_keywords
        extra_keywords = project_core_data.get("extra_category_keywords", {})
        if isinstance(extra_keywords, dict) and extra_keywords:
            current_kw = merged.get("category_keywords", {})
            for cat, words in extra_keywords.items():
                if cat in current_kw:
                    current_kw[cat] = list(set(current_kw[cat] + words))
                else:
                    current_kw[cat] = words
            merged["category_keywords"] = current_kw

        return merged

    @staticmethod
    def _match_genre_to_profile(genre: str) -> str | None:
        """根据项目 genre 字段匹配 Profile ID。"""
        if not genre:
            return None
        genre_lower = genre.lower()
        mapping = {
            "修仙": "xianxia", "仙侠": "xianxia", "奇幻": "xianxia",
            "玄幻": "xianxia", "武侠": "xianxia",
            "xianxia": "xianxia", "fantasy": "xianxia",
            "悬疑": "mystery", "推理": "mystery", "侦探": "mystery",
            "mystery": "mystery",
            "言情": "romance", "爱情": "romance", "romance": "romance",
            "科幻": "scifi", "scifi": "scifi", "science fiction": "scifi",
            "历史": "historical", "historical": "historical",
        }
        return mapping.get(genre_lower) or mapping.get(genre)

    @staticmethod
    def _make_minimal_profile() -> dict:
        """当 YAML 文件全部不可用时的硬编码兜底。"""
        return {
            "id": "general",
            "display_name": "通用小说（兜底）",
            "rule_categories": ["general", "society", "timeline", "character", "relationship"],
            "scene_types": ["dialogue", "action", "discovery", "reflection", "transition"],
            "beat_roles": ["setup", "pressure", "decision", "turning_point", "hook"],
            "contract_extensions": [],
            "extension_validation": [],
            "category_keywords": {},
            "location_schema": {"type": "single", "fields": ["location"]},
            "activation_prohibitions": list(_FALLBACK_PROHIBITIONS),
            "style_policy": {"default_strength": "medium", "allow_scene_override": True, "enforce_dialogue_voice": True},
        }
