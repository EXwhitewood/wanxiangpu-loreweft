import json
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from app.config import settings
from app.utils.json_safety import to_json_safe

SQLITE_PATH = getattr(settings, "sqlite_path", "data/loreweft.db")
_SQLITE_BUSY_TIMEOUT_SECONDS = 30
_SQLITE_BUSY_TIMEOUT_MS = _SQLITE_BUSY_TIMEOUT_SECONDS * 1000


def _ensure_data_dir():
    db_dir = os.path.dirname(SQLITE_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


def json_serialize(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if hasattr(obj, "model_dump") or hasattr(obj, "dict"):
        return to_json_safe(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def row_to_dict(row):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if isinstance(row, dict):
        return row
    return dict(row)


_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_key TEXT,
    is_active INTEGER DEFAULT 1,
    is_admin INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    genre TEXT DEFAULT '',
    word_count_target INTEGER DEFAULT 100000,
    current_chapter INTEGER DEFAULT 1,
    total_words INTEGER DEFAULT 0,
    owner_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    core_data TEXT DEFAULT '{}',
    outline_data TEXT DEFAULT '{}',
    outline_index TEXT DEFAULT '{}',
    draft_outline TEXT DEFAULT '{}',
    outline_version INTEGER DEFAULT 0,
    core_version INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    title TEXT DEFAULT '',
    content TEXT DEFAULT '',
    status TEXT DEFAULT 'draft',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS detail_seeds (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    scene_number INTEGER DEFAULT 1,
    entity_id TEXT NOT NULL,
    fact TEXT NOT NULL,
    embedding TEXT,
    narrative_time TEXT,
    tier TEXT DEFAULT 'T2',
    source_text TEXT DEFAULT '',
    entity_type TEXT DEFAULT '',
    source_kind TEXT DEFAULT 'harvester',
    source_observation_id TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workflow_executions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'running',
    trigger_type TEXT DEFAULT '',
    input_context TEXT DEFAULT '{}',
    current_layer INTEGER DEFAULT 0,
    total_layers INTEGER DEFAULT 0,
    result_context TEXT DEFAULT '{}',
    error_message TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workflow_steps (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES workflow_executions(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    layer INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    input_snapshot TEXT DEFAULT '{}',
    output_snapshot TEXT DEFAULT '{}',
    error_message TEXT DEFAULT '',
    started_at TEXT,
    completed_at TEXT,
    duration_ms INTEGER DEFAULT 0,
    skip_reason TEXT DEFAULT '',
    phase_trace TEXT DEFAULT '[]',
    inputs_from TEXT DEFAULT '{}',
    output_to TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS edit_memory (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    timestamp TEXT DEFAULT (datetime('now')),
    change_type TEXT DEFAULT '',
    target TEXT DEFAULT '{}',
    before TEXT DEFAULT '{}',
    after TEXT DEFAULT '{}',
    reason TEXT DEFAULT '',
    impact TEXT DEFAULT '{}',
    user_feedback TEXT DEFAULT '',
    status TEXT DEFAULT 'confirmed'
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_type TEXT NOT NULL,
    title TEXT DEFAULT '',
    chapter_number INTEGER,
    is_pinned INTEGER DEFAULT 0,
    summary TEXT DEFAULT '',
    message_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_message_records (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS detail_seeds_fts USING fts5(
    fact,
    entity_id,
    source_text,
    content=detail_seeds,
    content_rowid=rowid
);

CREATE TRIGGER IF NOT EXISTS detail_seeds_ai AFTER INSERT ON detail_seeds BEGIN
    INSERT INTO detail_seeds_fts(rowid, fact, entity_id, source_text)
    VALUES (new.rowid, new.fact, new.entity_id, new.source_text);
END;

CREATE TRIGGER IF NOT EXISTS detail_seeds_ad AFTER DELETE ON detail_seeds BEGIN
    INSERT INTO detail_seeds_fts(detail_seeds_fts, rowid, fact, entity_id, source_text)
    VALUES ('delete', old.rowid, old.fact, old.entity_id, old.source_text);
END;

CREATE TRIGGER IF NOT EXISTS detail_seeds_au AFTER UPDATE ON detail_seeds BEGIN
    INSERT INTO detail_seeds_fts(detail_seeds_fts, rowid, fact, entity_id, source_text)
    VALUES ('delete', old.rowid, old.fact, old.entity_id, old.source_text);
    INSERT INTO detail_seeds_fts(rowid, fact, entity_id, source_text)
    VALUES (new.rowid, new.fact, new.entity_id, new.source_text);
END;

CREATE TABLE IF NOT EXISTS foreshadowing_lines (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'planned',
    priority TEXT DEFAULT 'moderate',
    secret_canonical_statement TEXT NOT NULL,
    secret_truth_type TEXT DEFAULT 'past_event',
    secret_impact_level TEXT DEFAULT 'moderate',
    secret_spoiler_scope TEXT DEFAULT '{}',
    bury_window_start INTEGER,
    bury_window_end INTEGER,
    maintenance_window_start INTEGER,
    maintenance_window_end INTEGER,
    reveal_window_start INTEGER,
    reveal_window_end INTEGER,
    latest_safe_reveal_chapter INTEGER,
    bury_rhythm TEXT DEFAULT 'gradual',
    total_clues_planned INTEGER DEFAULT 0,
    clues_placed INTEGER DEFAULT 0,
    complexity_score REAL DEFAULT 0.0,
    max_active_foreshadowing_count INTEGER DEFAULT 3,
    clue_density_min REAL DEFAULT 0.02,
    clue_density_max REAL DEFAULT 0.12,
    inference_distance_min INTEGER DEFAULT 2,
    inference_distance_max INTEGER DEFAULT 5,
    salience TEXT DEFAULT 'moderate',
    repetition_distance INTEGER DEFAULT 2,
    reader_intended_state TEXT DEFAULT 'misdirected',
    reader_allowed_interpretations TEXT DEFAULT '[]',
    reader_forbidden_interpretations TEXT DEFAULT '[]',
    reader_fairness_level TEXT DEFAULT 'fair_but_hidden',
    reader_surprise_target REAL DEFAULT 0.75,
    reader_reread_reward_target REAL DEFAULT 0.9,
    reveal_readiness_score REAL DEFAULT 0.0,
    reveal_readiness_components TEXT DEFAULT '{}',
    legacy_payload TEXT DEFAULT '{}',
    created_by_agent TEXT DEFAULT 'outline_architect',
    owner_agent TEXT DEFAULT 'outline_architect',
    revision_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS foreshadowing_clues (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    foreshadowing_line_id TEXT NOT NULL REFERENCES foreshadowing_lines(id) ON DELETE CASCADE,
    clue_type TEXT DEFAULT 'supportive',
    chapter_number INTEGER NOT NULL,
    scene_index INTEGER DEFAULT 0,
    clue_text TEXT NOT NULL,
    clue_text_embedding TEXT,
    pov_character TEXT DEFAULT '',
    salience_at_time TEXT DEFAULT 'subtle',
    is_revealed_clue INTEGER DEFAULT 0,
    revealed_in_chapter INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS foreshadowing_causal_edges (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES foreshadowing_lines(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES foreshadowing_lines(id) ON DELETE CASCADE,
    edge_type TEXT DEFAULT 'depends_on',
    weight REAL DEFAULT 1.0,
    narrative_justification TEXT DEFAULT '',
    active_from_chapter INTEGER,
    active_until_chapter INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS foreshadowing_revision_log (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    foreshadowing_line_id TEXT NOT NULL REFERENCES foreshadowing_lines(id) ON DELETE CASCADE,
    revision_type TEXT DEFAULT 'update',
    changed_by TEXT DEFAULT 'system',
    change_summary TEXT DEFAULT '',
    before_snapshot TEXT DEFAULT '{}',
    after_snapshot TEXT DEFAULT '{}',
    affected_chapters TEXT DEFAULT '[]',
    affected_characters TEXT DEFAULT '[]',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outline_chunks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    chunk_type TEXT DEFAULT 'chapter_summary',
    content TEXT NOT NULL,
    embedding TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS character_cognitive_states (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    foreshadowing_line_id TEXT NOT NULL REFERENCES foreshadowing_lines(id) ON DELETE CASCADE,
    character_name TEXT NOT NULL,
    cognitive_level TEXT DEFAULT 'fully_blind',
    cognitive_status TEXT DEFAULT 'blind',
    known_clues TEXT DEFAULT '[]',
    last_knowledge_update_chapter INTEGER,
    last_knowledge_update_event TEXT DEFAULT '',
    valid_from_chapter INTEGER DEFAULT 1,
    valid_to_chapter INTEGER,
    is_intentionally_misled INTEGER DEFAULT 0,
    misled_by_character TEXT DEFAULT '',
    misled_narrative TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chapter_baselines (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    outline_version INTEGER DEFAULT 0,
    baseline_story_state TEXT DEFAULT '{}',
    baseline_chapter_state TEXT DEFAULT '{}',
    snapshot_source TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, chapter_number)
);

CREATE TABLE IF NOT EXISTS chapter_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    story_state TEXT DEFAULT '{}',
    chapter_state TEXT DEFAULT '{}',
    execution_id TEXT DEFAULT '',
    lineage_version INTEGER DEFAULT 1,
    stale INTEGER DEFAULT 0,
    committed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, chapter_number)
);

CREATE TABLE IF NOT EXISTS chapter_effect_outbox (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    scene_index INTEGER NOT NULL,
    effect_type TEXT NOT NULL,
    effect_version INTEGER DEFAULT 1,
    payload TEXT DEFAULT '{}',
    applied INTEGER DEFAULT 0,
    idempotency_key TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    error_message TEXT DEFAULT '',
    next_retry_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    applied_at TEXT,
    UNIQUE(idempotency_key),
    UNIQUE(project_id, chapter_number, scene_index, effect_type, effect_version)
);

CREATE TABLE IF NOT EXISTS cross_system_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sequence_number INTEGER,
    event_type TEXT,
    event_schema_version TEXT DEFAULT 'v1',
    source_system TEXT,
    priority TEXT DEFAULT 'normal',
    payload TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, sequence_number)
);

CREATE INDEX IF NOT EXISTS ix_cross_system_events_project_type
ON cross_system_events(project_id, event_type);

CREATE TABLE IF NOT EXISTS cross_system_event_deliveries (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES cross_system_events(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    target_system TEXT,
    status TEXT DEFAULT 'pending',
    priority_override TEXT,
    require_ack INTEGER DEFAULT 0,
    relevant_chapter_range TEXT,
    claimed_by TEXT,
    claim_batch_id TEXT,
    claimed_at TEXT,
    lease_ttl_seconds INTEGER DEFAULT 300,
    delivered_at TEXT,
    seen_at TEXT,
    acknowledged_at TEXT,
    resolved_at TEXT,
    resolved_by TEXT,
    snoozed_until TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_cross_system_event_deliveries_project_target_status
ON cross_system_event_deliveries(project_id, target_system, status);

CREATE INDEX IF NOT EXISTS ix_cross_system_event_deliveries_event_id
ON cross_system_event_deliveries(event_id);

CREATE TABLE IF NOT EXISTS cross_system_proposals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_system TEXT,
    target_domain TEXT,
    target_entity_type TEXT,
    idempotency_key TEXT,
    base_version TEXT,
    proposal_type TEXT,
    title TEXT,
    description TEXT DEFAULT '',
    proposal_data TEXT DEFAULT '{}',
    risk_level TEXT DEFAULT 'normal',
    affected_chapters TEXT DEFAULT '[]',
    conflict_refs TEXT DEFAULT '[]',
    consistency_result TEXT DEFAULT '{}',
    auto_action TEXT DEFAULT 'review',
    status TEXT DEFAULT 'pending',
    executing_until TEXT,
    heartbeat_at TEXT,
    reviewed_by TEXT,
    review_notes TEXT DEFAULT '',
    executed_by_service TEXT,
    executed_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    resolved_at TEXT,
    UNIQUE(project_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_cross_system_proposals_project_domain_status
ON cross_system_proposals(project_id, target_domain, status);

CREATE INDEX IF NOT EXISTS ix_cross_system_proposals_project_source_status
ON cross_system_proposals(project_id, source_system, status);

CREATE TABLE IF NOT EXISTS coordinator_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    trigger_type TEXT,
    trigger_source TEXT DEFAULT '',
    trigger_event TEXT DEFAULT '',
    input_versions TEXT DEFAULT '{}',
    status TEXT DEFAULT 'running',
    summary TEXT DEFAULT '',
    findings TEXT DEFAULT '[]',
    actions_taken TEXT DEFAULT '[]',
    proposals_created INTEGER DEFAULT 0,
    events_published INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    llm_calls INTEGER DEFAULT 0,
    tokens_used INTEGER DEFAULT 0,
    error_message TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_coordinator_runs_project_created
ON coordinator_runs(project_id, created_at);

CREATE TABLE IF NOT EXISTS worldview_observations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    scene_index INTEGER,
    chapter_id TEXT REFERENCES chapters(id) ON DELETE SET NULL,
    entity_type TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    entity_name_normalized TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload TEXT DEFAULT '{}',
    evidence_text TEXT DEFAULT '',
    fingerprint TEXT NOT NULL,
    confidence REAL DEFAULT 0.0,
    status TEXT DEFAULT 'active',
    core_entity_id TEXT,
    auto_promoted INTEGER DEFAULT 0,
    confirmed_by TEXT,
    confirmed_at TEXT,
    extraction_version INTEGER DEFAULT 1,
    extraction_source TEXT DEFAULT 'chapter_commit',
    generation_revision INTEGER DEFAULT 1,
    retracted_at TEXT,
    archived_at TEXT,
    orphan_warning INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(fingerprint)
);

CREATE INDEX IF NOT EXISTS ix_wv_observation_project_chapter
ON worldview_observations(project_id, chapter_number);

CREATE INDEX IF NOT EXISTS ix_wv_observation_project_entity
ON worldview_observations(project_id, entity_type, entity_name_normalized);

CREATE INDEX IF NOT EXISTS ix_wv_observation_project_status
ON worldview_observations(project_id, status);

CREATE TABLE IF NOT EXISTS generation_traces (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    scene_index INTEGER DEFAULT 0,
    trace_version INTEGER DEFAULT 1,
    mode TEXT DEFAULT 'metadata',
    status TEXT DEFAULT 'completed',
    context_manifest TEXT DEFAULT '{}',
    prompt_preview TEXT DEFAULT '{}',
    quality_summary TEXT DEFAULT '{}',
    recovery_summary TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_generation_traces_project_chapter
ON generation_traces(project_id, chapter_number);

CREATE INDEX IF NOT EXISTS ix_generation_traces_project_created
ON generation_traces(project_id, created_at);

CREATE TABLE IF NOT EXISTS entity_progressions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    effective_chapter INTEGER NOT NULL,
    effective_scene INTEGER DEFAULT 0,
    change_type TEXT DEFAULT 'mention',
    before_value TEXT DEFAULT '{}',
    after_value TEXT DEFAULT '{}',
    evidence_text TEXT DEFAULT '',
    source TEXT DEFAULT 'mention_detector',
    status TEXT DEFAULT 'candidate',
    schema_version INTEGER DEFAULT 1,
    fingerprint TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_entity_progressions_project_entity
ON entity_progressions(project_id, entity_type, entity_id);

CREATE INDEX IF NOT EXISTS ix_entity_progressions_project_chapter
ON entity_progressions(project_id, effective_chapter);

CREATE INDEX IF NOT EXISTS ix_entity_progressions_project_status
ON entity_progressions(project_id, status);

CREATE TABLE IF NOT EXISTS scene_experience_contracts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    scene_index INTEGER NOT NULL,
    generation_revision INTEGER DEFAULT 1,
    writing_mode_id TEXT DEFAULT 'general',
    contract TEXT NOT NULL,
    compiler_warnings TEXT DEFAULT '[]',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_scene_experience_contracts_project_chapter_scene
ON scene_experience_contracts(project_id, chapter_number, scene_index);

CREATE INDEX IF NOT EXISTS ix_scene_experience_contracts_project_chapter_revision
ON scene_experience_contracts(project_id, chapter_number, generation_revision);

CREATE TABLE IF NOT EXISTS scene_literary_quality_contracts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    scene_index INTEGER NOT NULL,
    generation_revision INTEGER DEFAULT 1,
    writing_mode_id TEXT DEFAULT 'general',
    contract TEXT NOT NULL,
    style_profile_id TEXT DEFAULT '',
    conflict_summary TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_scene_literary_quality_contracts_project_chapter_scene
ON scene_literary_quality_contracts(project_id, chapter_number, scene_index);

CREATE INDEX IF NOT EXISTS ix_scene_literary_quality_contracts_project_chapter_revision
ON scene_literary_quality_contracts(project_id, chapter_number, generation_revision);

CREATE TABLE IF NOT EXISTS experience_quality_reports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    scene_index INTEGER NOT NULL,
    generation_revision INTEGER DEFAULT 1,
    writing_mode_id TEXT DEFAULT 'general',
    scores TEXT DEFAULT '{}',
    advisories TEXT DEFAULT '[]',
    mode_fit TEXT DEFAULT '{}',
    style_conflicts TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_experience_quality_reports_project_chapter_scene
ON experience_quality_reports(project_id, chapter_number, scene_index);

CREATE INDEX IF NOT EXISTS ix_experience_quality_reports_project_chapter_revision
ON experience_quality_reports(project_id, chapter_number, generation_revision);

CREATE INDEX IF NOT EXISTS ix_experience_quality_reports_project_mode
ON experience_quality_reports(project_id, writing_mode_id);

CREATE TABLE IF NOT EXISTS quality_revision_audits (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    scene_index INTEGER NOT NULL,
    generation_revision INTEGER DEFAULT 1,
    revision_type TEXT DEFAULT '',
    before_hash TEXT DEFAULT '',
    after_hash TEXT DEFAULT '',
    applied_patches TEXT DEFAULT '[]',
    locked_contract_hash TEXT DEFAULT '',
    locked_style_hash TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_quality_revision_audits_project_chapter_scene
ON quality_revision_audits(project_id, chapter_number, scene_index);

CREATE INDEX IF NOT EXISTS ix_quality_revision_audits_project_revision
ON quality_revision_audits(project_id, chapter_number, generation_revision);

CREATE TABLE IF NOT EXISTS writing_mode_profile_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    writing_mode_id TEXT DEFAULT 'general',
    profile TEXT NOT NULL,
    source TEXT DEFAULT 'system',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_writing_mode_profile_snapshots_project_mode
ON writing_mode_profile_snapshots(project_id, writing_mode_id);

-- ===== T1.2: 自迭代归一化规则表（讨论稿 §5.1.7）=====
CREATE TABLE IF NOT EXISTS normalization_rules (
    rule_id TEXT PRIMARY KEY,
    if_condition TEXT NOT NULL,   -- JSON: RuleCondition
    then_action TEXT NOT NULL,    -- JSON: RuleAction
    evidence TEXT DEFAULT '',
    examples TEXT DEFAULT '[]',   -- JSON: list[dict]
    status TEXT DEFAULT 'candidate',  -- candidate/shadow/active/retired/suspect
    shadow_hits INTEGER DEFAULT 0,
    shadow_strong_hits INTEGER DEFAULT 0,
    shadow_misses INTEGER DEFAULT 0,
    shadow_strong_misses INTEGER DEFAULT 0,
    consecutive_corrections INTEGER DEFAULT 0,
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT '',
    source_rule_id TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS normalization_cases (
    case_id TEXT PRIMARY KEY,
    rule_id TEXT DEFAULT '',
    violation_json TEXT DEFAULT '{}',  -- JSON: dict
    llm_family TEXT DEFAULT '',
    rule_family TEXT DEFAULT '',
    case_type TEXT DEFAULT 'miss',  -- miss/correction
    llm_action TEXT DEFAULT 'agree',  -- agree/correct/supplement
    created_at TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_norm_rules_status ON normalization_rules(status);
CREATE INDEX IF NOT EXISTS idx_norm_cases_rule_id ON normalization_cases(rule_id);
CREATE INDEX IF NOT EXISTS idx_norm_cases_type ON normalization_cases(case_type);

"""

_CREATE_VSS_SQL = "CREATE VIRTUAL TABLE IF NOT EXISTS detail_seeds_vss USING vss0(embedding(1536));"

_ensure_data_dir()
_sync_conn = sqlite3.connect(SQLITE_PATH, timeout=_SQLITE_BUSY_TIMEOUT_SECONDS)
_sync_conn.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
_sync_conn.executescript(_CREATE_TABLES_SQL)
try:
    _sync_conn.execute(_CREATE_VSS_SQL)
    _sync_conn.commit()
except sqlite3.OperationalError:
    pass
_sync_conn.close()


class ColumnRef:
    def __init__(self, name, model_class=None):
        self.name = name
        self.model_class = model_class

    def desc(self):
        return _OrderBy(self.name, "DESC")

    def asc(self):
        return _OrderBy(self.name, "ASC")

    def __eq__(self, other):
        return _Filter(self.name, "=", other)

    def __ne__(self, other):
        return _Filter(self.name, "!=", other)

    def __hash__(self):
        return hash(self.name)


class _Filter:
    def __init__(self, column, op, value):
        self.column = column
        self.op = op
        self.value = value


class _OrderBy:
    def __init__(self, column, direction):
        self.column = column
        self.direction = direction


class ColumnDescriptor:
    def __init__(self, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return ColumnRef(self.name, objtype)
        return obj._data.get(self.name)

    def __set__(self, obj, value):
        obj._data[self.name] = value
        obj._dirty = True


class _ModelBase:
    __tablename__ = ""
    __columns__ = []
    __json_columns__ = []
    __datetime_columns__ = []
    __pk__ = "id"

    def __init__(self, **kwargs):
        self._data = {}
        self._dirty = False
        defaults = self._defaults()
        defaults.update(kwargs)
        for col in self.__columns__:
            self._data[col] = defaults.get(col)

    def _defaults(self):
        return {}

    @classmethod
    def _from_row(cls, row):
        obj = cls.__new__(cls)
        obj._data = dict(row)
        obj._dirty = False
        for col in cls.__json_columns__:
            val = obj._data.get(col)
            if isinstance(val, str):
                try:
                    obj._data[col] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        for col in cls.__datetime_columns__:
            val = obj._data.get(col)
            if isinstance(val, str):
                try:
                    obj._data[col] = datetime.fromisoformat(val)
                except (ValueError, TypeError):
                    pass
        return obj


class Project(_ModelBase):
    __tablename__ = "projects"
    __columns__ = [
        "id", "name", "description", "genre", "word_count_target",
        "current_chapter", "total_words", "core_data", "outline_data",
        "created_at", "updated_at",
    ]
    __json_columns__ = ["core_data", "outline_data"]
    __datetime_columns__ = ["created_at", "updated_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    name = ColumnDescriptor("name")
    description = ColumnDescriptor("description")
    genre = ColumnDescriptor("genre")
    word_count_target = ColumnDescriptor("word_count_target")
    current_chapter = ColumnDescriptor("current_chapter")
    total_words = ColumnDescriptor("total_words")
    core_data = ColumnDescriptor("core_data")
    outline_data = ColumnDescriptor("outline_data")
    created_at = ColumnDescriptor("created_at")
    updated_at = ColumnDescriptor("updated_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "name": "",
            "description": "",
            "genre": "",
            "word_count_target": 100000,
            "current_chapter": 1,
            "total_words": 0,
            "core_data": {},
            "outline_data": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


class Chapter(_ModelBase):
    __tablename__ = "chapters"
    __columns__ = [
        "id", "project_id", "chapter_number", "title", "content",
        "status", "created_at", "updated_at",
    ]
    __json_columns__ = []
    __datetime_columns__ = ["created_at", "updated_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    chapter_number = ColumnDescriptor("chapter_number")
    title = ColumnDescriptor("title")
    content = ColumnDescriptor("content")
    status = ColumnDescriptor("status")
    created_at = ColumnDescriptor("created_at")
    updated_at = ColumnDescriptor("updated_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "chapter_number": 0,
            "title": "",
            "content": "",
            "status": "draft",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


class DetailSeed(_ModelBase):
    __tablename__ = "detail_seeds"
    __columns__ = [
        "id", "project_id", "chapter_id", "scene_number", "entity_id",
        "fact", "embedding", "narrative_time", "tier", "source_text",
        "entity_type", "source_kind", "source_observation_id", "metadata",
        "created_at",
    ]
    __json_columns__ = ["metadata"]
    __datetime_columns__ = ["created_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    chapter_id = ColumnDescriptor("chapter_id")
    scene_number = ColumnDescriptor("scene_number")
    entity_id = ColumnDescriptor("entity_id")
    fact = ColumnDescriptor("fact")
    embedding = ColumnDescriptor("embedding")
    narrative_time = ColumnDescriptor("narrative_time")
    tier = ColumnDescriptor("tier")
    source_text = ColumnDescriptor("source_text")
    entity_type = ColumnDescriptor("entity_type")
    source_kind = ColumnDescriptor("source_kind")
    source_observation_id = ColumnDescriptor("source_observation_id")
    metadata_json = ColumnDescriptor("metadata")
    created_at = ColumnDescriptor("created_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "chapter_id": "",
            "scene_number": 1,
            "entity_id": "",
            "fact": "",
            "embedding": None,
            "narrative_time": None,
            "tier": "T2",
            "source_text": "",
            "entity_type": "",
            "source_kind": "harvester",
            "source_observation_id": None,
            "metadata": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


class WorkflowExecution(_ModelBase):
    __tablename__ = "workflow_executions"
    __columns__ = [
        "id", "project_id", "status", "trigger_type", "input_context",
        "current_layer", "total_layers", "result_context", "error_message",
        "created_at", "updated_at",
    ]
    __json_columns__ = ["input_context", "result_context"]
    __datetime_columns__ = ["created_at", "updated_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    status = ColumnDescriptor("status")
    trigger_type = ColumnDescriptor("trigger_type")
    input_context = ColumnDescriptor("input_context")
    current_layer = ColumnDescriptor("current_layer")
    total_layers = ColumnDescriptor("total_layers")
    result_context = ColumnDescriptor("result_context")
    error_message = ColumnDescriptor("error_message")
    created_at = ColumnDescriptor("created_at")
    updated_at = ColumnDescriptor("updated_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "status": "running",
            "trigger_type": "",
            "input_context": {},
            "current_layer": 0,
            "total_layers": 0,
            "result_context": {},
            "error_message": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


class WorkflowStep(_ModelBase):
    __tablename__ = "workflow_steps"
    __columns__ = [
        "id", "execution_id", "agent_name", "layer", "status",
        "input_snapshot", "output_snapshot", "error_message",
        "started_at", "completed_at", "duration_ms",
        # 方案16：DAG 可观测性补全字段
        "skip_reason", "phase_trace", "inputs_from", "output_to",
    ]
    __json_columns__ = ["input_snapshot", "output_snapshot", "phase_trace", "inputs_from", "output_to"]
    __datetime_columns__ = ["started_at", "completed_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    execution_id = ColumnDescriptor("execution_id")
    agent_name = ColumnDescriptor("agent_name")
    layer = ColumnDescriptor("layer")
    status = ColumnDescriptor("status")
    input_snapshot = ColumnDescriptor("input_snapshot")
    output_snapshot = ColumnDescriptor("output_snapshot")
    error_message = ColumnDescriptor("error_message")
    started_at = ColumnDescriptor("started_at")
    completed_at = ColumnDescriptor("completed_at")
    duration_ms = ColumnDescriptor("duration_ms")
    # 方案16：skip 原因 / phase trace / 数据血缘
    skip_reason = ColumnDescriptor("skip_reason")
    phase_trace = ColumnDescriptor("phase_trace")
    inputs_from = ColumnDescriptor("inputs_from")
    output_to = ColumnDescriptor("output_to")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "execution_id": "",
            "agent_name": "",
            "layer": 0,
            "status": "pending",
            "input_snapshot": {},
            "output_snapshot": {},
            "error_message": "",
            "started_at": None,
            "completed_at": None,
            "duration_ms": 0,
            "skip_reason": "",
            "phase_trace": [],
            "inputs_from": {},
            "output_to": [],
        }


class ChapterBaseline(_ModelBase):
    __tablename__ = "chapter_baselines"
    __columns__ = [
        "id", "project_id", "chapter_number", "outline_version",
        "baseline_story_state", "baseline_chapter_state", "snapshot_source",
        "created_at",
    ]
    __json_columns__ = ["baseline_story_state", "baseline_chapter_state"]
    __datetime_columns__ = ["created_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    chapter_number = ColumnDescriptor("chapter_number")
    outline_version = ColumnDescriptor("outline_version")
    baseline_story_state = ColumnDescriptor("baseline_story_state")
    baseline_chapter_state = ColumnDescriptor("baseline_chapter_state")
    snapshot_source = ColumnDescriptor("snapshot_source")
    created_at = ColumnDescriptor("created_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "chapter_number": 0,
            "outline_version": 0,
            "baseline_story_state": {},
            "baseline_chapter_state": {},
            "snapshot_source": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


class ChapterSnapshot(_ModelBase):
    __tablename__ = "chapter_snapshots"
    __columns__ = [
        "id", "project_id", "chapter_number", "story_state",
        "chapter_state", "execution_id", "lineage_version", "stale",
        "committed_at",
    ]
    __json_columns__ = ["story_state", "chapter_state"]
    __datetime_columns__ = ["committed_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    chapter_number = ColumnDescriptor("chapter_number")
    story_state = ColumnDescriptor("story_state")
    chapter_state = ColumnDescriptor("chapter_state")
    execution_id = ColumnDescriptor("execution_id")
    lineage_version = ColumnDescriptor("lineage_version")
    stale = ColumnDescriptor("stale")
    committed_at = ColumnDescriptor("committed_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "chapter_number": 0,
            "story_state": {},
            "chapter_state": {},
            "execution_id": "",
            "lineage_version": 1,
            "stale": 0,
            "committed_at": datetime.now(timezone.utc).isoformat(),
        }


class ChapterEffectOutbox(_ModelBase):
    __tablename__ = "chapter_effect_outbox"
    __columns__ = [
        "id", "project_id", "chapter_number", "scene_index",
        "effect_type", "effect_version", "payload", "applied",
        "idempotency_key", "status", "attempts", "error_message",
        "next_retry_at", "created_at", "applied_at",
    ]
    __json_columns__ = ["payload"]
    __datetime_columns__ = ["created_at", "applied_at", "next_retry_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    chapter_number = ColumnDescriptor("chapter_number")
    scene_index = ColumnDescriptor("scene_index")
    effect_type = ColumnDescriptor("effect_type")
    effect_version = ColumnDescriptor("effect_version")
    payload = ColumnDescriptor("payload")
    applied = ColumnDescriptor("applied")
    idempotency_key = ColumnDescriptor("idempotency_key")
    status = ColumnDescriptor("status")
    attempts = ColumnDescriptor("attempts")
    error_message = ColumnDescriptor("error_message")
    next_retry_at = ColumnDescriptor("next_retry_at")
    created_at = ColumnDescriptor("created_at")
    applied_at = ColumnDescriptor("applied_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "chapter_number": 0,
            "scene_index": 0,
            "effect_type": "",
            "effect_version": 1,
            "payload": {},
            "applied": 0,
            "idempotency_key": "",
            "status": "pending",
            "attempts": 0,
            "error_message": "",
            "next_retry_at": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "applied_at": None,
        }


class WorldviewObservation(_ModelBase):
    __tablename__ = "worldview_observations"
    __columns__ = [
        "id", "project_id", "chapter_number", "scene_index", "chapter_id",
        "entity_type", "entity_name", "entity_name_normalized", "operation",
        "payload", "evidence_text", "fingerprint", "confidence", "status",
        "core_entity_id", "auto_promoted", "confirmed_by", "confirmed_at",
        "extraction_version", "extraction_source", "generation_revision",
        "retracted_at", "archived_at", "orphan_warning",
        "created_at", "updated_at",
    ]
    __json_columns__ = ["payload"]
    __datetime_columns__ = ["confirmed_at", "retracted_at", "archived_at", "created_at", "updated_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    chapter_number = ColumnDescriptor("chapter_number")
    scene_index = ColumnDescriptor("scene_index")
    chapter_id = ColumnDescriptor("chapter_id")
    entity_type = ColumnDescriptor("entity_type")
    entity_name = ColumnDescriptor("entity_name")
    entity_name_normalized = ColumnDescriptor("entity_name_normalized")
    operation = ColumnDescriptor("operation")
    payload = ColumnDescriptor("payload")
    evidence_text = ColumnDescriptor("evidence_text")
    fingerprint = ColumnDescriptor("fingerprint")
    confidence = ColumnDescriptor("confidence")
    status = ColumnDescriptor("status")
    core_entity_id = ColumnDescriptor("core_entity_id")
    auto_promoted = ColumnDescriptor("auto_promoted")
    confirmed_by = ColumnDescriptor("confirmed_by")
    confirmed_at = ColumnDescriptor("confirmed_at")
    extraction_version = ColumnDescriptor("extraction_version")
    extraction_source = ColumnDescriptor("extraction_source")
    generation_revision = ColumnDescriptor("generation_revision")
    retracted_at = ColumnDescriptor("retracted_at")
    archived_at = ColumnDescriptor("archived_at")
    orphan_warning = ColumnDescriptor("orphan_warning")
    created_at = ColumnDescriptor("created_at")
    updated_at = ColumnDescriptor("updated_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "chapter_number": 0,
            "scene_index": None,
            "chapter_id": None,
            "entity_type": "",
            "entity_name": "",
            "entity_name_normalized": "",
            "operation": "new",
            "payload": {},
            "evidence_text": "",
            "fingerprint": "",
            "confidence": 0.0,
            "status": "active",
            "core_entity_id": None,
            "auto_promoted": 0,
            "confirmed_by": None,
            "confirmed_at": None,
            "extraction_version": 1,
            "extraction_source": "chapter_commit",
            "generation_revision": 1,
            "retracted_at": None,
            "archived_at": None,
            "orphan_warning": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


class GenerationTrace(_ModelBase):
    __tablename__ = "generation_traces"
    __columns__ = [
        "id", "project_id", "chapter_number", "scene_index",
        "trace_version", "mode", "status", "context_manifest",
        "prompt_preview", "quality_summary", "recovery_summary",
        "created_at", "expires_at",
    ]
    __json_columns__ = ["context_manifest", "prompt_preview", "quality_summary", "recovery_summary"]
    __datetime_columns__ = ["created_at", "expires_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    chapter_number = ColumnDescriptor("chapter_number")
    scene_index = ColumnDescriptor("scene_index")
    trace_version = ColumnDescriptor("trace_version")
    mode = ColumnDescriptor("mode")
    status = ColumnDescriptor("status")
    context_manifest = ColumnDescriptor("context_manifest")
    prompt_preview = ColumnDescriptor("prompt_preview")
    quality_summary = ColumnDescriptor("quality_summary")
    recovery_summary = ColumnDescriptor("recovery_summary")
    created_at = ColumnDescriptor("created_at")
    expires_at = ColumnDescriptor("expires_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "chapter_number": 0,
            "scene_index": 0,
            "trace_version": 1,
            "mode": "metadata",
            "status": "completed",
            "context_manifest": {},
            "prompt_preview": {},
            "quality_summary": {},
            "recovery_summary": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": None,
        }


class EntityProgression(_ModelBase):
    __tablename__ = "entity_progressions"
    __columns__ = [
        "id", "project_id", "entity_type", "entity_id",
        "effective_chapter", "effective_scene", "change_type",
        "before_value", "after_value", "evidence_text", "source",
        "status", "schema_version", "fingerprint", "created_at",
    ]
    __json_columns__ = ["before_value", "after_value"]
    __datetime_columns__ = ["created_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    entity_type = ColumnDescriptor("entity_type")
    entity_id = ColumnDescriptor("entity_id")
    effective_chapter = ColumnDescriptor("effective_chapter")
    effective_scene = ColumnDescriptor("effective_scene")
    change_type = ColumnDescriptor("change_type")
    before_value = ColumnDescriptor("before_value")
    after_value = ColumnDescriptor("after_value")
    evidence_text = ColumnDescriptor("evidence_text")
    source = ColumnDescriptor("source")
    status = ColumnDescriptor("status")
    schema_version = ColumnDescriptor("schema_version")
    fingerprint = ColumnDescriptor("fingerprint")
    created_at = ColumnDescriptor("created_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "entity_type": "",
            "entity_id": "",
            "effective_chapter": 0,
            "effective_scene": 0,
            "change_type": "mention",
            "before_value": {},
            "after_value": {},
            "evidence_text": "",
            "source": "mention_detector",
            "status": "candidate",
            "schema_version": 1,
            "fingerprint": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


class SceneExperienceContract(_ModelBase):
    __tablename__ = "scene_experience_contracts"
    __columns__ = [
        "id", "project_id", "chapter_number", "scene_index",
        "generation_revision", "writing_mode_id", "contract",
        "compiler_warnings", "created_at",
    ]
    __json_columns__ = ["contract", "compiler_warnings"]
    __datetime_columns__ = ["created_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    chapter_number = ColumnDescriptor("chapter_number")
    scene_index = ColumnDescriptor("scene_index")
    generation_revision = ColumnDescriptor("generation_revision")
    writing_mode_id = ColumnDescriptor("writing_mode_id")
    contract = ColumnDescriptor("contract")
    compiler_warnings = ColumnDescriptor("compiler_warnings")
    created_at = ColumnDescriptor("created_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "chapter_number": 0,
            "scene_index": 0,
            "generation_revision": 1,
            "writing_mode_id": "general",
            "contract": {},
            "compiler_warnings": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


class SceneLiteraryQualityContract(_ModelBase):
    __tablename__ = "scene_literary_quality_contracts"
    __columns__ = [
        "id", "project_id", "chapter_number", "scene_index",
        "generation_revision", "writing_mode_id", "contract",
        "style_profile_id", "conflict_summary", "created_at",
    ]
    __json_columns__ = ["contract", "conflict_summary"]
    __datetime_columns__ = ["created_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    chapter_number = ColumnDescriptor("chapter_number")
    scene_index = ColumnDescriptor("scene_index")
    generation_revision = ColumnDescriptor("generation_revision")
    writing_mode_id = ColumnDescriptor("writing_mode_id")
    contract = ColumnDescriptor("contract")
    style_profile_id = ColumnDescriptor("style_profile_id")
    conflict_summary = ColumnDescriptor("conflict_summary")
    created_at = ColumnDescriptor("created_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "chapter_number": 0,
            "scene_index": 0,
            "generation_revision": 1,
            "writing_mode_id": "general",
            "contract": {},
            "style_profile_id": "",
            "conflict_summary": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


class ExperienceQualityReport(_ModelBase):
    __tablename__ = "experience_quality_reports"
    __columns__ = [
        "id", "project_id", "chapter_number", "scene_index",
        "generation_revision", "writing_mode_id", "scores",
        "advisories", "mode_fit", "style_conflicts", "created_at",
    ]
    __json_columns__ = ["scores", "advisories", "mode_fit", "style_conflicts"]
    __datetime_columns__ = ["created_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    chapter_number = ColumnDescriptor("chapter_number")
    scene_index = ColumnDescriptor("scene_index")
    generation_revision = ColumnDescriptor("generation_revision")
    writing_mode_id = ColumnDescriptor("writing_mode_id")
    scores = ColumnDescriptor("scores")
    advisories = ColumnDescriptor("advisories")
    mode_fit = ColumnDescriptor("mode_fit")
    style_conflicts = ColumnDescriptor("style_conflicts")
    created_at = ColumnDescriptor("created_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "chapter_number": 0,
            "scene_index": 0,
            "generation_revision": 1,
            "writing_mode_id": "general",
            "scores": {},
            "advisories": [],
            "mode_fit": {},
            "style_conflicts": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


class QualityRevisionAudit(_ModelBase):
    __tablename__ = "quality_revision_audits"
    __columns__ = [
        "id", "project_id", "chapter_number", "scene_index",
        "generation_revision", "revision_type", "before_hash",
        "after_hash", "applied_patches", "locked_contract_hash",
        "locked_style_hash", "created_at",
    ]
    __json_columns__ = ["applied_patches"]
    __datetime_columns__ = ["created_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    chapter_number = ColumnDescriptor("chapter_number")
    scene_index = ColumnDescriptor("scene_index")
    generation_revision = ColumnDescriptor("generation_revision")
    revision_type = ColumnDescriptor("revision_type")
    before_hash = ColumnDescriptor("before_hash")
    after_hash = ColumnDescriptor("after_hash")
    applied_patches = ColumnDescriptor("applied_patches")
    locked_contract_hash = ColumnDescriptor("locked_contract_hash")
    locked_style_hash = ColumnDescriptor("locked_style_hash")
    created_at = ColumnDescriptor("created_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "chapter_number": 0,
            "scene_index": 0,
            "generation_revision": 1,
            "revision_type": "",
            "before_hash": "",
            "after_hash": "",
            "applied_patches": [],
            "locked_contract_hash": "",
            "locked_style_hash": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


class WritingModeProfileSnapshot(_ModelBase):
    __tablename__ = "writing_mode_profile_snapshots"
    __columns__ = ["id", "project_id", "writing_mode_id", "profile", "source", "created_at"]
    __json_columns__ = ["profile"]
    __datetime_columns__ = ["created_at"]
    __pk__ = "id"

    id = ColumnDescriptor("id")
    project_id = ColumnDescriptor("project_id")
    writing_mode_id = ColumnDescriptor("writing_mode_id")
    profile = ColumnDescriptor("profile")
    source = ColumnDescriptor("source")
    created_at = ColumnDescriptor("created_at")

    def _defaults(self):
        return {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "writing_mode_id": "general",
            "profile": {},
            "source": "system",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


class _SelectQuery:
    def __init__(self, model_class, columns=None):
        self._model = model_class
        self._columns = columns
        self._filters = []
        self._orderings = []
        self._limit_val = None
        self._offset_val = None

    def where(self, *filters):
        self._filters.extend(filters)
        return self

    def order_by(self, *orderings):
        for o in orderings:
            if isinstance(o, ColumnRef):
                self._orderings.append(f"{o.name} ASC")
            elif isinstance(o, _OrderBy):
                self._orderings.append(f"{o.column} {o.direction}")
        return self

    def limit(self, n):
        self._limit_val = n
        return self

    def offset(self, n):
        self._offset_val = n
        return self

    def _build(self):
        if self._columns:
            col_str = ", ".join(self._columns)
        else:
            col_str = "*"
        sql = f"SELECT {col_str} FROM {self._model.__tablename__}"
        params = []
        if self._filters:
            clauses = []
            for f in self._filters:
                fval = f.value
                if isinstance(fval, uuid.UUID):
                    fval = str(fval)
                elif isinstance(fval, datetime):
                    fval = fval.isoformat()
                clauses.append(f"{f.column} {f.op} ?")
                params.append(fval)
            sql += " WHERE " + " AND ".join(clauses)
        if self._orderings:
            sql += " ORDER BY " + ", ".join(self._orderings)
        if self._limit_val is not None:
            sql += f" LIMIT {self._limit_val}"
        if self._offset_val is not None:
            sql += f" OFFSET {self._offset_val}"
        return sql, tuple(params)


class _UpdateQuery:
    def __init__(self, model_class):
        self._model = model_class
        self._filters = []
        self._values = {}

    def where(self, *filters):
        self._filters.extend(filters)
        return self

    def values(self, **kwargs):
        self._values.update(kwargs)
        return self

    def _build(self):
        set_parts = []
        params = []
        for col, val in self._values.items():
            if col in self._model.__json_columns__ and val is not None:
                if not isinstance(val, str):
                    val = json.dumps(val, default=json_serialize)
            elif isinstance(val, datetime):
                val = val.isoformat()
            elif isinstance(val, uuid.UUID):
                val = str(val)
            set_parts.append(f"{col} = ?")
            params.append(val)
        sql = f"UPDATE {self._model.__tablename__} SET {', '.join(set_parts)}"
        if self._filters:
            clauses = []
            for f in self._filters:
                fval = f.value
                if isinstance(fval, uuid.UUID):
                    fval = str(fval)
                elif isinstance(fval, datetime):
                    fval = fval.isoformat()
                clauses.append(f"{f.column} {f.op} ?")
                params.append(fval)
            sql += " WHERE " + " AND ".join(clauses)
        return sql, tuple(params)


def select(*entities):
    if len(entities) == 1:
        entity = entities[0]
        if isinstance(entity, type) and issubclass(entity, _ModelBase):
            return _SelectQuery(entity)
        if isinstance(entity, ColumnRef):
            return _SelectQuery(entity.model_class, columns=[entity.name])
    model_class = None
    columns = []
    for e in entities:
        if isinstance(e, ColumnRef):
            if model_class is None:
                model_class = e.model_class
            columns.append(e.name)
        elif isinstance(e, type) and issubclass(e, _ModelBase):
            model_class = e
    return _SelectQuery(model_class, columns=columns or None)


def update(model_class):
    return _UpdateQuery(model_class)


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        return self._rows[0]

    def scalars(self):
        return _ScalarResult(self._rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        if not self._rows:
            return None
        return self._rows[0]


class Session:
    def __init__(self, conn):
        self._conn = conn
        self._pending_adds = []
        self._pending_deletes = []
        self._tracked = {}

    def _track(self, obj):
        pk = obj._data.get(obj.__pk__)
        if pk is not None:
            self._tracked[(obj.__tablename__, pk)] = obj

    async def execute(self, query, params=None):
        if isinstance(query, _SelectQuery):
            sql, qparams = query._build()
            cursor = await self._conn.execute(sql, qparams)
            rows = await cursor.fetchall()
            if query._columns:
                if len(query._columns) == 1:
                    scalars = [row[0] for row in rows]
                else:
                    scalars = [tuple(row[i] for i in range(len(query._columns))) for row in rows]
                return _Result(scalars)
            objects = [query._model._from_row(row) for row in rows]
            for obj in objects:
                self._track(obj)
            return _Result(objects)

        if isinstance(query, _UpdateQuery):
            sql, qparams = query._build()
            await self._conn.execute(sql, qparams)
            return _Result([])

        sql = query
        cursor = await self._conn.execute(sql, params or ())
        if sql.strip().upper().startswith("SELECT"):
            rows = await cursor.fetchall()
            return _Result([dict(row) for row in rows])
        return _Result([])

    async def add(self, obj):
        self._pending_adds.append(obj)
        self._track(obj)

    async def delete(self, obj):
        self._pending_deletes.append(obj)

    async def get(self, model_class, pk):
        sql = f"SELECT * FROM {model_class.__tablename__} WHERE {model_class.__pk__} = ?"
        cursor = await self._conn.execute(sql, (str(pk),))
        row = await cursor.fetchone()
        if row is None:
            return None
        obj = model_class._from_row(row)
        self._track(obj)
        return obj

    async def commit(self):
        effective_adds = [obj for obj in self._pending_adds if obj not in self._pending_deletes]
        effective_deletes = [obj for obj in self._pending_deletes if obj not in self._pending_adds]

        for obj in effective_adds:
            data = {}
            for col in obj.__columns__:
                val = obj._data.get(col)
                if col in obj.__json_columns__ and val is not None:
                    if not isinstance(val, str):
                        val = json.dumps(val, default=json_serialize)
                elif isinstance(val, datetime):
                    val = val.isoformat()
                elif isinstance(val, uuid.UUID):
                    val = str(val)
                data[col] = val
            col_names = ", ".join(obj.__columns__)
            placeholders = ", ".join(["?"] * len(obj.__columns__))
            values = tuple(data[col] for col in obj.__columns__)
            sql = f"INSERT OR REPLACE INTO {obj.__tablename__} ({col_names}) VALUES ({placeholders})"
            await self._conn.execute(sql, values)
            obj._dirty = False

        for key, obj in list(self._tracked.items()):
            if obj in effective_adds or obj in effective_deletes:
                continue
            if getattr(obj, "_dirty", False):
                obj._data["updated_at"] = datetime.now(timezone.utc).isoformat()
                data = {}
                for col in obj.__columns__:
                    val = obj._data.get(col)
                    if col in obj.__json_columns__ and val is not None:
                        if not isinstance(val, str):
                            val = json.dumps(val, default=json_serialize)
                    elif isinstance(val, datetime):
                        val = val.isoformat()
                    elif isinstance(val, uuid.UUID):
                        val = str(val)
                    data[col] = val
                set_clause = ", ".join(
                    f"{col} = ?" for col in obj.__columns__ if col != obj.__pk__
                )
                values = tuple(data[col] for col in obj.__columns__ if col != obj.__pk__)
                values = values + (data[obj.__pk__],)
                sql = f"UPDATE {obj.__tablename__} SET {set_clause} WHERE {obj.__pk__} = ?"
                await self._conn.execute(sql, values)
                obj._dirty = False

        for obj in effective_deletes:
            pk_val = obj._data.get(obj.__pk__)
            if isinstance(pk_val, uuid.UUID):
                pk_val = str(pk_val)
            sql = f"DELETE FROM {obj.__tablename__} WHERE {obj.__pk__} = ?"
            await self._conn.execute(sql, (pk_val,))
            key = (obj.__tablename__, pk_val)
            self._tracked.pop(key, None)

        await self._conn.commit()
        self._pending_adds.clear()
        self._pending_deletes.clear()

    async def refresh(self, obj):
        model_class = type(obj)
        pk = obj._data.get(model_class.__pk__)
        refreshed = await self.get(model_class, pk)
        if refreshed:
            obj._data = refreshed._data
            obj._dirty = False

    async def close(self):
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


class async_session:
    async def __aenter__(self):
        _ensure_data_dir()
        self._conn = await aiosqlite.connect(
            SQLITE_PATH,
            timeout=_SQLITE_BUSY_TIMEOUT_SECONDS,
        )
        self._conn.row_factory = sqlite3.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        self._session = Session(self._conn)
        return self._session

    async def __aexit__(self, exc_type, _exc_val, _exc_tb):
        if exc_type is not None and self._session._conn is not None:
            try:
                await self._session._conn.rollback()
            except Exception:
                pass
        if self._session._conn is not None:
            try:
                await self._session._conn.close()
            except Exception:
                pass
        return False


async def create_tables():
    _ensure_data_dir()
    async with aiosqlite.connect(SQLITE_PATH, timeout=_SQLITE_BUSY_TIMEOUT_SECONDS) as conn:
        await conn.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        await conn.executescript(_CREATE_TABLES_SQL)
        for table, columns in {
            "projects": {
                "outline_index": "outline_index TEXT DEFAULT '{}'",
                "draft_outline": "draft_outline TEXT DEFAULT '{}'",
                "outline_version": "outline_version INTEGER DEFAULT 0",
                "core_version": "core_version INTEGER DEFAULT 0",
                "owner_id": "owner_id TEXT",
            },
            "chat_sessions": {
                "is_pinned": "is_pinned INTEGER DEFAULT 0",
                "summary": "summary TEXT DEFAULT ''",
                "message_count": "message_count INTEGER DEFAULT 0",
            },
            "chapter_baselines": {
                "snapshot_source": "snapshot_source TEXT DEFAULT ''",
            },
            "chapter_snapshots": {
                "lineage_version": "lineage_version INTEGER DEFAULT 1",
                "stale": "stale INTEGER DEFAULT 0",
            },
            "detail_seeds": {
                "entity_type": "entity_type TEXT DEFAULT ''",
                "source_kind": "source_kind TEXT DEFAULT 'harvester'",
                "source_observation_id": "source_observation_id TEXT",
                "metadata": "metadata TEXT DEFAULT '{}'",
            },
            "chapter_effect_outbox": {
                "status": "status TEXT DEFAULT 'pending'",
                "attempts": "attempts INTEGER DEFAULT 0",
                "error_message": "error_message TEXT DEFAULT ''",
                "next_retry_at": "next_retry_at TEXT",
            },
            "foreshadowing_clues": {
                "clue_text_embedding": "clue_text_embedding TEXT",
            },
            "foreshadowing_revision_log": {
                "affected_chapters": "affected_chapters TEXT DEFAULT '[]'",
                "affected_characters": "affected_characters TEXT DEFAULT '[]'",
            },
            "workflow_steps": {
                "skip_reason": "skip_reason TEXT DEFAULT ''",
                "phase_trace": "phase_trace TEXT DEFAULT '[]'",
                "inputs_from": "inputs_from TEXT DEFAULT '{}'",
                "output_to": "output_to TEXT DEFAULT '[]'",
            },
            "entity_progressions": {
                "fingerprint": "fingerprint TEXT",
            },
        }.items():
            result = await conn.execute(f"PRAGMA table_info({table})")
            rows = await result.fetchall()
            existing = {row[1] for row in rows}
            for column_name, ddl in columns.items():
                if column_name not in existing:
                    await conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        await conn.execute("""
            DELETE FROM chapter_effect_outbox
            WHERE EXISTS (
                SELECT 1
                FROM chapter_effect_outbox AS keep
                WHERE keep.idempotency_key = chapter_effect_outbox.idempotency_key
                  AND (
                    keep.applied > chapter_effect_outbox.applied
                    OR (
                        keep.applied = chapter_effect_outbox.applied
                        AND keep.rowid < chapter_effect_outbox.rowid
                    )
                  )
            )
        """)
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_chapter_effect_outbox_idempotency_key "
            "ON chapter_effect_outbox(idempotency_key)"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_entity_progressions_fingerprint "
            "ON entity_progressions(fingerprint)"
        )
        # P2-W5 修复：WorkflowStep 唯一约束（与 db_models.py 的 UniqueConstraint 对齐）
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_workflow_step_exec_agent "
            "ON workflow_steps(execution_id, agent_name)"
        )
        try:
            await conn.execute(_CREATE_VSS_SQL)
        except sqlite3.OperationalError:
            pass
        await conn.commit()


@asynccontextmanager
async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
