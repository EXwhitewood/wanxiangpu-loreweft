from typing import Literal
from pydantic import BaseModel, Field

SkillKind = Literal["prompt", "service", "tool", "workflow", "validator"]
SkillSource = Literal["builtin", "custom", "project_file", "worldbuilder_sub_agent"]


class SkillTrigger(BaseModel):
    agents: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    scene_roles: list[str] = Field(default_factory=list)
    issue_types: list[str] = Field(default_factory=list)
    writing_modes: list[str] = Field(default_factory=list)
    tabs: list[str] = Field(default_factory=list)
    feature_flags: list[str] = Field(default_factory=list)


class SkillRuntimeBinding(BaseModel):
    kind: SkillKind = "prompt"
    service_class: str | None = None
    service_method: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    optional_context: list[str] = Field(default_factory=list)
    min_permission: str = "readonly"
    execution_phase: str = "pre_generation"
    timeout_seconds: float = 30.0
    output_char_budget: int = 4000
    cache_enabled: bool = False


class AgentSkillManifest(BaseModel):
    id: str
    name: str
    display_name: str = ""
    description: str = ""
    version: str = "1"
    format_version: str = "legacy"
    source_format: str = "legacy_root"
    source: SkillSource = "builtin"
    kind: SkillKind = "prompt"
    domain: str = "general"
    category: str = "utility"
    agents: list[str] = Field(default_factory=list)
    priority: int = 50
    enabled_by_default: bool = False
    triggers: SkillTrigger = Field(default_factory=SkillTrigger)
    runtime: SkillRuntimeBinding = Field(default_factory=SkillRuntimeBinding)
    constraints: dict[str, dict] = Field(default_factory=dict)
    validators: list[str] = Field(default_factory=list)
    validation_contracts: dict[str, dict] = Field(default_factory=dict)
    repair_strategies: list[str] = Field(default_factory=list)
    repair_hooks: list[str] = Field(default_factory=list)
    reference_files: list[str] = Field(default_factory=list)
    resource_pack_files: list[str] = Field(default_factory=list)
    path: str = ""


class AgentSkill(BaseModel):
    manifest: AgentSkillManifest
    body: str = ""
    prompt_sections: dict[str, str] = Field(default_factory=dict)
    examples: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    resource_packs: dict[str, dict] = Field(default_factory=dict)


class CompiledSkillPacket(BaseModel):
    agent_name: str
    active_skills: list[str] = Field(default_factory=list)
    activation_reasons: dict[str, str] = Field(default_factory=dict)
    system_sections: list[str] = Field(default_factory=list)
    user_sections: list[str] = Field(default_factory=list)
    execution_plan: list[dict] = Field(default_factory=list)
    tool_permissions: dict[str, str] = Field(default_factory=dict)
    validators: list[str] = Field(default_factory=list)
    validation_contracts: dict[str, dict] = Field(default_factory=dict)
    repair_hooks: dict[str, list[str]] = Field(default_factory=dict)
    must_avoid: list[str] = Field(default_factory=list)
    required_context: dict[str, list[str]] = Field(default_factory=dict)
    optional_context: dict[str, list[str]] = Field(default_factory=dict)
    resource_packs: dict[str, dict[str, dict]] = Field(default_factory=dict)
    trace: dict = Field(default_factory=dict)

    def render_system(self) -> str:
        if not self.system_sections:
            return ""
        return "\n\n## Agent Skills\n" + "\n\n".join(self.system_sections)

    def render_user(self) -> str:
        if not self.user_sections and not self.must_avoid:
            return ""
        parts = []
        if self.user_sections:
            parts.append("## 本轮启用技能要求")
            parts.extend(self.user_sections)
        if self.must_avoid:
            parts.append("## 本轮禁用倾向")
            parts.extend(f"- {item}" for item in self.must_avoid[:20])
        return "\n\n" + "\n".join(parts)


class SkillExecutionTrace(BaseModel):
    agent_name: str
    active_skills: list[str] = Field(default_factory=list)
    skipped_skills: dict[str, str] = Field(default_factory=dict)
    activation_reasons: dict[str, str] = Field(default_factory=dict)
    loaded_skill_count: int = 0
    injected_system_chars: int = 0
    injected_user_chars: int = 0
    service_outputs: dict[str, dict] = Field(default_factory=dict)
    validators_requested: list[str] = Field(default_factory=list)
    permission_decisions: dict[str, dict] = Field(default_factory=dict)


class SkillSelectionContext(BaseModel):
    agent_name: str
    enabled_skills: list[str] | None = None
    enabled_agent_skills: list[str] | None = None
    project_id: str | None = None
    active_tab: str | None = None
    scene_contract: dict = {}
    scene_context_package: dict = {}
    writing_mode_profile: dict = {}
    quality_extensions: dict = {}
    previous_quality_reports: dict = {}
    feature_policy: dict = {}


class SkillSelectionResult(BaseModel):
    selected: list[AgentSkillManifest]
    skipped: dict[str, str]
    activation_reasons: dict[str, str]
