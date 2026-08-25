import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, Integer, Boolean, DateTime, ForeignKey, JSON, Float, TypeDecorator, text, BigInteger, UniqueConstraint, Index, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.utils.json_safety import to_json_safe

try:
    from pgvector.sqlalchemy import Vector
    PGVECTOR_AVAILABLE = True
except ImportError:
    PGVECTOR_AVAILABLE = False

_IS_SQLITE = "sqlite" in settings.database_url

if _IS_SQLITE:
    class _SQLiteUUID(TypeDecorator):
        impl = String(36)
        cache_ok = True

        def process_bind_param(self, value, dialect):
            if value is not None and isinstance(value, uuid.UUID):
                return str(value)
            return value

        def process_result_value(self, value, dialect):
            if value is not None and not isinstance(value, uuid.UUID):
                try:
                    return uuid.UUID(str(value))
                except (ValueError, AttributeError):
                    return value
            return value

    _UUIDType = _SQLiteUUID
else:
    from sqlalchemy.dialects.postgresql import UUID as PGUUID
    from sqlalchemy.dialects.postgresql import JSONB
    _UUIDType = PGUUID(as_uuid=True)

class _SafeJSON(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            try:
                from sqlalchemy.dialects.postgresql import JSONB
                return dialect.type_descriptor(JSONB())
            except ImportError:
                return dialect.type_descriptor(JSON())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return to_json_safe(value)


_JSONType = _SafeJSON


def _embedding_type(dim: int):
    if PGVECTOR_AVAILABLE:
        return JSON().with_variant(Vector(dim), "postgresql")
    return JSON

_SQLITE_BUSY_TIMEOUT_MS = 30_000

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args={"timeout": _SQLITE_BUSY_TIMEOUT_MS / 1000} if _IS_SQLITE else {},
)
if _IS_SQLITE:
    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _uuid_default():
    return str(uuid.uuid4()) if _IS_SQLITE else uuid.uuid4()


class User(Base):
    """P0-1 修复：用户模型，支持 API Key 认证与项目所有权."""
    __tablename__ = "users"
    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    name = Column(String(255), nullable=False)
    api_key = Column(String(128), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Project(Base):
    __tablename__ = "projects"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    genre = Column(String(100), default="")
    word_count_target = Column(Integer, default=100000)
    current_chapter = Column(Integer, default=1)
    total_words = Column(Integer, default=0)
    owner_id = Column(_UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    core_data = Column(_JSONType, default=dict)
    outline_data = Column(_JSONType, default=dict)
    outline_index = Column(_JSONType, default=dict)
    draft_outline = Column(_JSONType, default=dict)
    outline_version = Column(Integer, default=0)
    core_version = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # SQLite stores the large narrative JSON documents in the same table row.
    # A column-only SELECT can therefore still touch overflow pages when it
    # reaches the timestamps at the end of the record. These covering indexes
    # keep the lobby list on a compact, JSON-free access path.
    __table_args__ = (
        Index(
            "ix_projects_summary_created",
            "created_at",
            "id",
            "name",
            "description",
            "genre",
            "word_count_target",
            "current_chapter",
            "total_words",
            "updated_at",
        ).ddl_if(dialect="sqlite"),
        Index(
            "ix_projects_summary_owner_created",
            "owner_id",
            "created_at",
            "id",
            "name",
            "description",
            "genre",
            "word_count_target",
            "current_chapter",
            "total_words",
            "updated_at",
        ).ddl_if(dialect="sqlite"),
    )


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    title = Column(String(255), default="")
    content = Column(Text, default="")
    status = Column(String(50), default="draft")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DetailSeed(Base):
    __tablename__ = "detail_seeds"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(_UUIDType, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    scene_number = Column(Integer, default=1)
    entity_id = Column(String(255), nullable=False)
    fact = Column(Text, nullable=False)
    embedding = Column(String)
    narrative_time = Column(String(255))
    tier = Column(String(10), default="T2")
    source_text = Column(Text, default="")
    entity_type = Column(String(32), default="")
    source_kind = Column(String(32), default="harvester")
    source_observation_id = Column(_UUIDType, nullable=True)
    metadata_json = Column("metadata", _JSONType, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class EditMemory(Base):
    __tablename__ = "edit_memory"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(_UUIDType, nullable=False, default=_uuid_default)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    change_type = Column(String(100), default="")
    target = Column(_JSONType, default=dict)
    before = Column(_JSONType, default=dict)
    after = Column(_JSONType, default=dict)
    reason = Column(Text, default="")
    impact = Column(_JSONType, default=dict)
    user_feedback = Column(Text, default="")
    status = Column(String(20), default="confirmed")


class OutlineChunk(Base):
    __tablename__ = "outline_chunks"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    chunk_type = Column(String(50), default="chapter_summary")
    content = Column(Text, nullable=False)
    embedding = Column(_embedding_type(384), default=None)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ForeshadowingLine(Base):
    __tablename__ = "foreshadowing_lines"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(Integer, default=1)
    status = Column(String(32), default="planned")
    priority = Column(String(16), default="moderate")

    secret_canonical_statement = Column(Text, nullable=False)
    secret_truth_type = Column(String(32), default="past_event")
    secret_impact_level = Column(String(16), default="moderate")
    secret_spoiler_scope = Column(_JSONType, default=dict)

    bury_window_start = Column(Integer, nullable=True)
    bury_window_end = Column(Integer, nullable=True)
    maintenance_window_start = Column(Integer, nullable=True)
    maintenance_window_end = Column(Integer, nullable=True)
    reveal_window_start = Column(Integer, nullable=True)
    reveal_window_end = Column(Integer, nullable=True)
    latest_safe_reveal_chapter = Column(Integer, nullable=True)

    bury_rhythm = Column(String(16), default="gradual")
    total_clues_planned = Column(Integer, default=0)
    clues_placed = Column(Integer, default=0)
    complexity_score = Column(Float, default=0.0)

    max_active_foreshadowing_count = Column(Integer, default=3)
    clue_density_min = Column(Float, default=0.02)
    clue_density_max = Column(Float, default=0.12)
    inference_distance_min = Column(Integer, default=2)
    inference_distance_max = Column(Integer, default=5)
    salience = Column(String(16), default="moderate")
    repetition_distance = Column(Integer, default=2)

    reader_intended_state = Column(String(32), default="misdirected")
    reader_allowed_interpretations = Column(_JSONType, default=list)
    reader_forbidden_interpretations = Column(_JSONType, default=list)
    reader_fairness_level = Column(String(32), default="fair_but_hidden")
    reader_surprise_target = Column(Float, default=0.75)
    reader_reread_reward_target = Column(Float, default=0.9)

    reveal_readiness_score = Column(Float, default=0.0)
    reveal_readiness_components = Column(_JSONType, default=dict)
    resolved_chapter = Column(Integer, nullable=True)
    resolution_summary = Column(Text, default="")
    legacy_payload = Column(_JSONType, default=dict)

    created_by_agent = Column(String(64), default="outline_architect")
    owner_agent = Column(String(64), default="outline_architect")
    revision_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CharacterCognitiveState(Base):
    __tablename__ = "character_cognitive_states"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    foreshadowing_line_id = Column(_UUIDType, ForeignKey("foreshadowing_lines.id", ondelete="CASCADE"), nullable=False)
    character_name = Column(String(255), nullable=False)
    cognitive_level = Column(String(32), default="fully_blind")
    cognitive_status = Column(String(32), default="blind")
    known_clues = Column(_JSONType, default=list)
    last_knowledge_update_chapter = Column(Integer, nullable=True)
    last_knowledge_update_event = Column(Text, default="")
    valid_from_chapter = Column(Integer, default=1)
    valid_to_chapter = Column(Integer, nullable=True)
    is_intentionally_misled = Column(Boolean, default=False)
    misled_by_character = Column(String(255), default="")
    misled_narrative = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ForeshadowingClue(Base):
    __tablename__ = "foreshadowing_clues"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    foreshadowing_line_id = Column(_UUIDType, ForeignKey("foreshadowing_lines.id", ondelete="CASCADE"), nullable=False)
    clue_type = Column(String(32), default="supportive")
    chapter_number = Column(Integer, nullable=False)
    scene_index = Column(Integer, default=0)
    clue_text = Column(Text, nullable=False)
    clue_text_embedding = Column(_embedding_type(384), default=None)
    pov_character = Column(String(255), default="")
    salience_at_time = Column(String(16), default="subtle")
    is_revealed_clue = Column(Boolean, default=False)
    revealed_in_chapter = Column(Integer, nullable=True)
    source_observation_id = Column(
        _UUIDType,
        ForeignKey("worldview_observations.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_fingerprint = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_foreshadowing_clues_source_observation", "source_observation_id"),
        Index("ix_foreshadowing_clues_line_chapter", "foreshadowing_line_id", "chapter_number"),
    )


class ForeshadowingCausalEdge(Base):
    __tablename__ = "foreshadowing_causal_edges"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(_UUIDType, ForeignKey("foreshadowing_lines.id", ondelete="CASCADE"), nullable=False)
    target_id = Column(_UUIDType, ForeignKey("foreshadowing_lines.id", ondelete="CASCADE"), nullable=False)
    edge_type = Column(String(32), default="depends_on")
    weight = Column(Float, default=1.0)
    narrative_justification = Column(Text, default="")
    active_from_chapter = Column(Integer, nullable=True)
    active_until_chapter = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ForeshadowingRevisionLog(Base):
    __tablename__ = "foreshadowing_revision_log"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    foreshadowing_line_id = Column(_UUIDType, ForeignKey("foreshadowing_lines.id", ondelete="CASCADE"), nullable=False)
    revision_type = Column(String(32), default="update")
    changed_by = Column(String(64), default="system")
    change_summary = Column(Text, default="")
    before_snapshot = Column(_JSONType, default=dict)
    after_snapshot = Column(_JSONType, default=dict)
    affected_chapters = Column(_JSONType, default=list)
    affected_characters = Column(_JSONType, default=list)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ChapterBaseline(Base):
    __tablename__ = "chapter_baselines"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    outline_version = Column(Integer, default=0)
    baseline_story_state = Column(_JSONType, default=dict)
    baseline_chapter_state = Column(_JSONType, default=dict)
    snapshot_source = Column(String(50), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("project_id", "chapter_number", name="uq_chapter_baseline_project_chapter"),
    )


class ChapterSnapshot(Base):
    __tablename__ = "chapter_snapshots"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    story_state = Column(_JSONType, default=dict)
    chapter_state = Column(_JSONType, default=dict)
    committed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    execution_id = Column(String(100), default="")
    lineage_version = Column(Integer, default=1)
    stale = Column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("project_id", "chapter_number", name="uq_chapter_snapshot_project_chapter"),
    )


class ChapterDiagnosticRecord(Base):
    """Persisted post-save diagnostic for a user-authored chapter revision.

    This table belongs to the auxiliary editor flow.  It is deliberately
    separate from generation/FBI review records so a user's decision cannot
    change the AI generation commit path.
    """

    __tablename__ = "chapter_diagnostic_records"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    chapter_revision_hash = Column(String(64), nullable=False)
    fingerprint = Column(String(128), nullable=False)
    category = Column(String(64), nullable=False, default="state_consistency")
    severity = Column(String(16), nullable=False, default="low")
    title = Column(String(255), nullable=False, default="")
    detail = Column(Text, default="")
    evidence_quote = Column(Text, default="")
    source = Column(_JSONType, default=dict)
    confidence = Column(Float, default=0.0)
    reference_baseline_id = Column(
        _UUIDType,
        ForeignKey("chapter_baselines.id", ondelete="SET NULL"),
        nullable=True,
    )
    reference_snapshot = Column(_JSONType, default=dict)
    affected_domains = Column(_JSONType, default=list)
    available_actions = Column(_JSONType, default=list)
    status = Column(String(48), nullable=False, default="open")
    decision_by = Column(String(128), default="")
    decision_reason = Column(Text, default="")
    decision_payload = Column(_JSONType, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    decided_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "chapter_number",
            "chapter_revision_hash",
            "fingerprint",
            name="uq_chapter_diagnostic_revision_fingerprint",
        ),
        Index(
            "ix_chapter_diagnostics_project_chapter_status",
            "project_id",
            "chapter_number",
            "status",
        ),
    )


class ChapterSettlementJob(Base):
    """Durable post-save work for a user-authored chapter revision."""

    __tablename__ = "chapter_settlement_jobs"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    chapter_revision_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    phase = Column(String(48), nullable=False, default="queued")
    stage_results = Column(_JSONType, default=dict)
    attempts = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, default="")
    next_retry_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "chapter_number",
            "chapter_revision_hash",
            name="uq_chapter_settlement_revision",
        ),
        Index(
            "ix_chapter_settlement_project_chapter_status",
            "project_id",
            "chapter_number",
            "status",
        ),
    )


class ChapterContentRevision(Base):
    """Immutable backup created before an explicit AI replacement."""

    __tablename__ = "chapter_content_revisions"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    source_chapter_id = Column(_UUIDType, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(255), default="")
    content = Column(Text, nullable=False, default="")
    chapter_status = Column(String(50), default="draft")
    content_hash = Column(String(64), nullable=False)
    reason = Column(String(64), nullable=False, default="ai_explicit_replace")
    execution_id = Column(String(100), nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("project_id", "chapter_number", "execution_id", name="uq_chapter_revision_execution"),
        Index("ix_chapter_revision_project_chapter", "project_id", "chapter_number", "created_at"),
    )


class ChapterEffectOutbox(Base):
    __tablename__ = "chapter_effect_outbox"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    scene_index = Column(Integer, nullable=False)
    effect_type = Column(String(50), nullable=False)
    effect_version = Column(Integer, default=1)
    payload = Column(_JSONType, default=dict)
    applied = Column(Boolean, default=False)
    idempotency_key = Column(String(255), nullable=False, unique=True)
    status = Column(String(32), default="pending")
    attempts = Column(Integer, default=0)
    error_message = Column(Text, default="")
    next_retry_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    applied_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "chapter_number", "scene_index", "effect_type", "effect_version",
                         name="uq_effect_outbox_identity"),
    )


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(50), default="running")
    trigger_type = Column(String(100), default="")
    input_context = Column(_JSONType, default=dict)
    current_layer = Column(Integer, default=0)
    total_layers = Column(Integer, default=0)
    result_context = Column(_JSONType, default=dict)
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"

    # P2-W5 修复：F1/F6 UPSERT 修复已解除阻塞（_persist_node_start/_persist_node_skipped
    # 已改为 UPDATE first, INSERT if rowcount==0），_persist_node_complete/_persist_node_failed
    # 只 UPDATE 不 INSERT。所有写入点已统一，可安全添加唯一约束。
    __table_args__ = (
        UniqueConstraint("execution_id", "agent_name", name="uq_workflow_step_exec_agent"),
    )

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    execution_id = Column(_UUIDType, ForeignKey("workflow_executions.id", ondelete="CASCADE"), nullable=False)
    agent_name = Column(String(100), nullable=False)
    layer = Column(Integer, default=0)
    status = Column(String(50), default="running")
    input_snapshot = Column(_JSONType, default=dict)
    output_snapshot = Column(_JSONType, default=dict)
    error_message = Column(Text, default="")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, default=0)
    # 方案16：DAG 可观测性补全字段
    skip_reason = Column(Text, default="")
    phase_trace = Column(_JSONType, default=list)
    inputs_from = Column(_JSONType, default=dict)
    output_to = Column(_JSONType, default=list)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    agent_type = Column(String(50), nullable=False)
    title = Column(String(200), default="")
    chapter_number = Column(Integer, nullable=True)
    is_pinned = Column(Boolean, default=False)
    summary = Column(Text, default="")
    message_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ChatMessageRecord(Base):
    __tablename__ = "chat_message_records"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    session_id = Column(_UUIDType, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CrossSystemEvent(Base):
    __tablename__ = "cross_system_events"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    sequence_number = Column(BigInteger)
    event_type = Column(String(50))
    event_schema_version = Column(String(10), default="v1")
    source_system = Column(String(50))
    priority = Column(String(20), default="normal")
    payload = Column(_JSONType, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("project_id", "sequence_number", name="uq_cross_system_events_project_sequence"),
        Index("ix_cross_system_events_project_type", "project_id", "event_type"),
    )


class CrossSystemEventDelivery(Base):
    __tablename__ = "cross_system_event_deliveries"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    event_id = Column(_UUIDType, ForeignKey("cross_system_events.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    target_system = Column(String(50))
    status = Column(String(20), default="pending")
    priority_override = Column(String(20), nullable=True)
    require_ack = Column(Boolean, default=False)
    relevant_chapter_range = Column(String(50), nullable=True)
    claimed_by = Column(String(255), nullable=True)
    claim_batch_id = Column(String(255), nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    lease_ttl_seconds = Column(Integer, default=300)
    delivered_at = Column(DateTime, nullable=True)
    seen_at = Column(DateTime, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String(255), nullable=True)
    snoozed_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_cross_system_event_deliveries_project_target_status", "project_id", "target_system", "status"),
        Index("ix_cross_system_event_deliveries_event_id", "event_id"),
    )


class CrossSystemProposal(Base):
    __tablename__ = "cross_system_proposals"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_system = Column(String(50))
    target_domain = Column(String(50))
    target_entity_type = Column(String(50))
    idempotency_key = Column(String(255))
    base_version = Column(String(50), nullable=True)
    proposal_type = Column(String(50))
    title = Column(String(255))
    description = Column(Text, default="")
    proposal_data = Column(_JSONType, default=dict)
    risk_level = Column(String(20), default="normal")
    affected_chapters = Column(_JSONType, default=list)
    conflict_refs = Column(_JSONType, default=list)
    consistency_result = Column(_JSONType, default=dict)
    auto_action = Column(String(20), default="review")
    status = Column(String(20), default="pending")
    executing_until = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String(50), nullable=True)
    review_notes = Column(Text, default="")
    executed_by_service = Column(String(255), nullable=True)
    executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_proposals_project_idempotency"),
        Index("ix_cross_system_proposals_project_domain_status", "project_id", "target_domain", "status"),
        Index("ix_cross_system_proposals_project_source_status", "project_id", "source_system", "status"),
    )


class CoordinatorRun(Base):
    __tablename__ = "coordinator_runs"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    trigger_type = Column(String(50))
    trigger_source = Column(String(50), default="")
    trigger_event = Column(String(50), default="")
    input_versions = Column(_JSONType, default=dict)
    status = Column(String(20), default="running")
    summary = Column(Text, default="")
    findings = Column(_JSONType, default=list)
    actions_taken = Column(_JSONType, default=list)
    proposals_created = Column(Integer, default=0)
    events_published = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    llm_calls = Column(Integer, default=0)
    tokens_used = Column(Integer, default=0)
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_coordinator_runs_project_created", "project_id", "created_at"),
    )


class WorldviewObservation(Base):
    __tablename__ = "worldview_observations"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    scene_index = Column(Integer, nullable=True)
    chapter_id = Column(_UUIDType, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    entity_type = Column(String(32), nullable=False)
    entity_name = Column(String(255), nullable=False)
    entity_name_normalized = Column(String(255), nullable=False)
    operation = Column(String(32), nullable=False)
    payload = Column(_JSONType, default=dict)
    evidence_text = Column(Text, default="")
    fingerprint = Column(String(64), nullable=False)
    confidence = Column(Float, default=0.0)
    status = Column(String(32), default="active")
    core_entity_id = Column(String(255), nullable=True)
    auto_promoted = Column(Boolean, default=False)
    confirmed_by = Column(String(100), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    extraction_version = Column(Integer, default=1)
    extraction_source = Column(String(32), default="chapter_commit")
    generation_revision = Column(Integer, default=1)
    retracted_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    orphan_warning = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_wv_observation_fingerprint"),
        Index("ix_wv_observation_project_chapter", "project_id", "chapter_number"),
        Index("ix_wv_observation_project_entity", "project_id", "entity_type", "entity_name_normalized"),
        Index("ix_wv_observation_project_status", "project_id", "status"),
        Index(
            "ix_wv_observation_project_review_page",
            "project_id",
            "status",
            "auto_promoted",
            "confirmed_by",
            "entity_type",
            "chapter_number",
            "created_at",
            "id",
        ).ddl_if(dialect="sqlite"),
    )


class GenerationTrace(Base):
    __tablename__ = "generation_traces"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    scene_index = Column(Integer, default=0)
    trace_version = Column(Integer, default=1)
    mode = Column(String(32), default="metadata")
    status = Column(String(32), default="completed")
    context_manifest = Column(_JSONType, default=dict)
    prompt_preview = Column(_JSONType, default=dict)
    quality_summary = Column(_JSONType, default=dict)
    recovery_summary = Column(_JSONType, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_generation_traces_project_chapter", "project_id", "chapter_number"),
        Index("ix_generation_traces_project_created", "project_id", "created_at"),
    )


class EntityProgression(Base):
    __tablename__ = "entity_progressions"

    id = Column(_UUIDType, primary_key=True, default=_uuid_default)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(32), nullable=False)
    entity_id = Column(String(255), nullable=False)
    effective_chapter = Column(Integer, nullable=False)
    effective_scene = Column(Integer, default=0)
    change_type = Column(String(64), default="mention")
    before_value = Column(_JSONType, default=dict)
    after_value = Column(_JSONType, default=dict)
    evidence_text = Column(Text, default="")
    source = Column(String(64), default="mention_detector")
    status = Column(String(32), default="candidate")
    schema_version = Column(Integer, default=1)
    fingerprint = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_entity_progressions_project_entity", "project_id", "entity_type", "entity_id"),
        Index("ix_entity_progressions_project_chapter", "project_id", "effective_chapter"),
        Index("ix_entity_progressions_project_status", "project_id", "status"),
        UniqueConstraint("fingerprint", name="uq_entity_progressions_fingerprint"),
    )


class NarrativePropositionORM(Base):
    __tablename__ = "narrative_propositions"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    scene_index = Column(Integer, nullable=False)
    generation_revision = Column(Integer, nullable=False, default=1)
    subject_name = Column(Text, nullable=False)
    subject_type = Column(Text, nullable=False, default="unknown")
    subject_id = Column(Text, nullable=True)
    predicate_name = Column(Text, nullable=False)
    predicate_category = Column(Text, nullable=False)
    object_name = Column(Text, nullable=True)
    object_type = Column(Text, nullable=True)
    object_id = Column(Text, nullable=True)
    truth_layer = Column(Text, nullable=False)
    certainty = Column(Text, nullable=False)
    polarity = Column(Text, nullable=False)
    responsibility = Column(Text, nullable=False)
    time_scope = Column(Text, nullable=True)
    location_scope = Column(Text, nullable=True)
    source_text = Column(Text, nullable=False)
    source_agent = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=0.0)
    status = Column(Text, nullable=False, default="active")
    lifecycle_status = Column(Text, nullable=False, default="current")
    valid_from_chapter = Column(Integer, nullable=True)
    valid_to_chapter = Column(Integer, nullable=True)
    supersedes_id = Column(Text, nullable=True)
    importance = Column(Float, nullable=False, default=0.5)
    last_confirmed_chapter = Column(Integer, nullable=True)
    source_chunk_index = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    retracted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_narrative_propositions_project_chapter", "project_id", "chapter_number"),
        Index("ix_narrative_propositions_project_chapter_scene", "project_id", "chapter_number", "scene_index"),
        Index("ix_narrative_propositions_project_status", "project_id", "status"),
        Index("ix_narrative_propositions_project_lifecycle", "project_id", "lifecycle_status"),
        Index("ix_narrative_propositions_project_validity", "project_id", "valid_from_chapter", "valid_to_chapter"),
        Index("ix_narrative_propositions_project_chapter_revision", "project_id", "chapter_number", "generation_revision"),
    )


class SceneFactContract(Base):
    __tablename__ = "scene_fact_contracts"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    scene_index = Column(Integer, nullable=False)
    generation_revision = Column(Integer, nullable=False, default=1)
    scene_id = Column(Text, nullable=True)
    fact_contract = Column(_JSONType, nullable=False)
    compiler_warnings = Column(_JSONType, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_scene_fact_contracts_project_chapter_scene", "project_id", "chapter_number", "scene_index"),
        Index("ix_scene_fact_contracts_project_chapter_revision", "project_id", "chapter_number", "generation_revision"),
    )


class PropositionAuditReport(Base):
    __tablename__ = "proposition_audit_reports"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    scene_index = Column(Integer, nullable=False)
    generation_revision = Column(Integer, nullable=False, default=1)
    passed = Column(Boolean, nullable=False)
    commit_blocked = Column(Boolean, nullable=False)
    violations = Column(_JSONType, nullable=True)
    proposition_ids = Column(_JSONType, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_proposition_audit_reports_project_chapter_scene", "project_id", "chapter_number", "scene_index"),
        Index("ix_proposition_audit_reports_project_chapter_revision", "project_id", "chapter_number", "generation_revision"),
    )


class PropositionLink(Base):
    __tablename__ = "proposition_links"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_proposition_id = Column(Text, nullable=False)
    target_proposition_id = Column(Text, nullable=False)
    relation_type = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_proposition_links_project_source", "project_id", "source_proposition_id"),
        Index("ix_proposition_links_project_target", "project_id", "target_proposition_id"),
        Index("ix_proposition_links_project_relation", "project_id", "relation_type"),
    )


class SceneExperienceContract(Base):
    __tablename__ = "scene_experience_contracts"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    scene_index = Column(Integer, nullable=False)
    generation_revision = Column(Integer, nullable=False, default=1)
    writing_mode_id = Column(Text, nullable=False, default="general")
    contract = Column(_JSONType, nullable=False)
    compiler_warnings = Column(_JSONType, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_scene_experience_contracts_project_chapter_scene", "project_id", "chapter_number", "scene_index"),
        Index("ix_scene_experience_contracts_project_chapter_revision", "project_id", "chapter_number", "generation_revision"),
    )


class SceneLiteraryQualityContract(Base):
    __tablename__ = "scene_literary_quality_contracts"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    scene_index = Column(Integer, nullable=False)
    generation_revision = Column(Integer, nullable=False, default=1)
    writing_mode_id = Column(Text, nullable=False, default="general")
    contract = Column(_JSONType, nullable=False)
    style_profile_id = Column(Text, nullable=False, default="")
    conflict_summary = Column(_JSONType, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_scene_literary_quality_contracts_project_chapter_scene", "project_id", "chapter_number", "scene_index"),
        Index("ix_scene_literary_quality_contracts_project_chapter_revision", "project_id", "chapter_number", "generation_revision"),
    )


class ExperienceQualityReport(Base):
    __tablename__ = "experience_quality_reports"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    scene_index = Column(Integer, nullable=False)
    generation_revision = Column(Integer, nullable=False, default=1)
    writing_mode_id = Column(Text, nullable=False, default="general")
    scores = Column(_JSONType, nullable=True)
    advisories = Column(_JSONType, nullable=True)
    mode_fit = Column(_JSONType, nullable=True)
    style_conflicts = Column(_JSONType, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_experience_quality_reports_project_chapter_scene", "project_id", "chapter_number", "scene_index"),
        Index("ix_experience_quality_reports_project_chapter_revision", "project_id", "chapter_number", "generation_revision"),
        Index("ix_experience_quality_reports_project_mode", "project_id", "writing_mode_id"),
    )


class QualityRevisionAudit(Base):
    __tablename__ = "quality_revision_audits"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    scene_index = Column(Integer, nullable=False)
    generation_revision = Column(Integer, nullable=False, default=1)
    revision_type = Column(Text, nullable=False, default="")
    before_hash = Column(Text, nullable=False, default="")
    after_hash = Column(Text, nullable=False, default="")
    applied_patches = Column(_JSONType, nullable=True)
    locked_contract_hash = Column(Text, nullable=False, default="")
    locked_style_hash = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_quality_revision_audits_project_chapter_scene", "project_id", "chapter_number", "scene_index"),
        Index("ix_quality_revision_audits_project_revision", "project_id", "chapter_number", "generation_revision"),
    )


class WritingModeProfileSnapshot(Base):
    __tablename__ = "writing_mode_profile_snapshots"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    writing_mode_id = Column(Text, nullable=False, default="general")
    profile = Column(_JSONType, nullable=False)
    source = Column(Text, nullable=False, default="system")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_writing_mode_profile_snapshots_project_mode", "project_id", "writing_mode_id"),
    )


class ReaderCorpusSourceORM(Base):
    __tablename__ = "reader_corpus_sources"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(Text, nullable=False, default="")
    author = Column(Text, nullable=False, default="")
    genre = Column(Text, nullable=False, default="")
    platform = Column(Text, nullable=False, default="")
    quality_tier = Column(Text, nullable=False, default="unknown")
    allowed_uses = Column(_JSONType, default=list)
    status = Column(Text, nullable=False, default="ready")
    raw_path = Column(Text, nullable=False, default="")
    word_count = Column(Integer, nullable=False, default=0)
    chapter_count = Column(Integer, nullable=False, default=0)
    tags = Column(_JSONType, default=list)
    notes = Column(Text, nullable=False, default="")
    error_message = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_reader_corpus_sources_genre_quality", "genre", "quality_tier"),
        Index("ix_reader_corpus_sources_status", "status"),
    )


class ReaderCorpusChapterORM(Base):
    __tablename__ = "reader_corpus_chapters"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(Text, ForeignKey("reader_corpus_sources.id", ondelete="CASCADE"), nullable=False)
    chapter_index = Column(Integer, nullable=False, default=0)
    title = Column(Text, nullable=False, default="")
    char_count = Column(Integer, nullable=False, default=0)
    content_hash = Column(Text, nullable=False, default="")
    sample_excerpt = Column(Text, nullable=False, default="")
    metrics = Column(_JSONType, default=dict)
    structure_tags = Column(_JSONType, default=list)
    pattern_summary = Column(_JSONType, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_reader_corpus_chapters_source_index", "source_id", "chapter_index"),
        Index("ix_reader_corpus_chapters_hash", "content_hash"),
    )


class ReaderExperiencePatternORM(Base):
    __tablename__ = "reader_experience_patterns"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id = Column(Text, ForeignKey("reader_corpus_sources.id", ondelete="CASCADE"), nullable=True)
    chapter_id = Column(Text, ForeignKey("reader_corpus_chapters.id", ondelete="CASCADE"), nullable=True)
    genre = Column(Text, nullable=False, default="")
    chapter_position = Column(Text, nullable=False, default="unknown")
    scene_position = Column(Text, nullable=False, default="any")
    pattern_type = Column(Text, nullable=False)
    pattern_key = Column(Text, nullable=False)
    score = Column(Float, nullable=False, default=0.0)
    evidence_count = Column(Integer, nullable=False, default=1)
    metrics = Column(_JSONType, default=dict)
    guidance = Column(_JSONType, default=dict)
    tags = Column(_JSONType, default=list)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_reader_experience_patterns_lookup", "genre", "chapter_position", "scene_position"),
        Index("ix_reader_experience_patterns_type", "pattern_type", "pattern_key"),
    )


class ReaderCorpusImportJobORM(Base):
    __tablename__ = "reader_corpus_import_jobs"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    source = Column(Text, nullable=False, default="")
    status = Column(Text, nullable=False, default="running")
    total_files = Column(Integer, nullable=False, default=0)
    processed_files = Column(Integer, nullable=False, default=0)
    failed_files = Column(Integer, nullable=False, default=0)
    report = Column(_JSONType, default=dict)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_reader_corpus_import_jobs_status", "status"),
        Index("ix_reader_corpus_import_jobs_started", "started_at"),
    )


class FBIRepairCaseORM(Base):
    __tablename__ = "fbi_repair_cases"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(Text, nullable=True)
    scene_id = Column(Text, nullable=True)
    workflow_execution_id = Column(Text, nullable=True)
    source = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="pending")
    base_text_hash = Column(Text, nullable=False, default="")
    current_text_hash = Column(Text, nullable=False, default="")
    issue_count = Column(Integer, nullable=False, default=0)
    resolved_issue_count = Column(Integer, nullable=False, default=0)
    policy_json = Column(_JSONType, default=dict)
    context_summary_json = Column(_JSONType, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_fbi_repair_cases_project_status", "project_id", "status"),
        Index("ix_fbi_repair_cases_project_workflow", "project_id", "workflow_execution_id"),
    )


class FBIRepairIssueORM(Base):
    __tablename__ = "fbi_repair_issues"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(Text, ForeignKey("fbi_repair_cases.id", ondelete="CASCADE"), nullable=False)
    source_layer = Column(Text, nullable=False)
    violation_type = Column(Text, nullable=False)
    severity = Column(Text, nullable=False, default="major")
    repairability = Column(Text, nullable=False, default="auto")
    span_start = Column(Integer, nullable=True)
    span_end = Column(Integer, nullable=True)
    evidence = Column(Text, default="")
    repair_goal = Column(Text, default="")
    acceptance_criteria_json = Column(_JSONType, default=list)
    blocks_commit = Column(Boolean, nullable=False, default=True)
    status = Column(Text, nullable=False, default="pending")

    __table_args__ = (
        Index("ix_fbi_repair_issues_case", "case_id"),
        Index("ix_fbi_repair_issues_case_layer", "case_id", "source_layer"),
    )


class FBIRepairAttemptORM(Base):
    __tablename__ = "fbi_repair_attempts"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(Text, ForeignKey("fbi_repair_cases.id", ondelete="CASCADE"), nullable=False)
    attempt_index = Column(Integer, nullable=False, default=0)
    specialist = Column(Text, nullable=False)
    model_name = Column(Text, default="")
    prompt_trace_id = Column(Text, default="")
    status = Column(Text, nullable=False, default="pending")
    failure_reason = Column(Text, default="")
    input_hash = Column(Text, default="")
    output_hash = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_fbi_repair_attempts_case_index", "case_id", "attempt_index"),
    )


class FBIRepairPatchORM(Base):
    __tablename__ = "fbi_repair_patches"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(Text, ForeignKey("fbi_repair_cases.id", ondelete="CASCADE"), nullable=False)
    attempt_id = Column(Text, ForeignKey("fbi_repair_attempts.id", ondelete="SET NULL"), nullable=True)
    intent_id = Column(Text, default="")
    base_text_hash = Column(Text, nullable=False)
    span_start = Column(Integer, nullable=False)
    span_end = Column(Integer, nullable=False)
    original_text = Column(Text, nullable=False)
    replacement_text = Column(Text, nullable=False)
    changed_chars = Column(Integer, nullable=False, default=0)
    strategy = Column(Text, default="")
    risk_level = Column(Text, nullable=False, default="low")
    resolves_issue_ids_json = Column(_JSONType, default=list)
    validator_report_json = Column(_JSONType, default=dict)
    applied = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_fbi_repair_patches_case", "case_id"),
        Index("ix_fbi_repair_patches_attempt", "attempt_id"),
    )


class FBIRepairAuditORM(Base):
    __tablename__ = "fbi_repair_audits"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(Text, ForeignKey("fbi_repair_cases.id", ondelete="CASCADE"), nullable=False)
    attempt_id = Column(Text, ForeignKey("fbi_repair_attempts.id", ondelete="SET NULL"), nullable=True)
    audit_type = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="passed")
    report_json = Column(_JSONType, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_fbi_repair_audits_case", "case_id"),
        Index("ix_fbi_repair_audits_attempt", "attempt_id"),
    )


class FBIAutoRepairRunORM(Base):
    __tablename__ = "fbi_auto_repair_runs"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(Text, ForeignKey("fbi_repair_cases.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(_UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(Text, nullable=True)
    scene_id = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="pending")
    round_index = Column(Integer, nullable=False, default=0)
    max_rounds = Column(Integer, nullable=False, default=3)
    current_text_hash = Column(Text, default="")
    unresolved_issue_ids_json = Column(_JSONType, default=list)
    resolved_issue_ids_json = Column(_JSONType, default=list)
    last_failure_reason = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_fbi_auto_repair_runs_case", "case_id"),
        Index("ix_fbi_auto_repair_runs_project_status", "project_id", "status"),
    )


class FBIResolvedIssueORM(Base):
    __tablename__ = "fbi_resolved_issues"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(Text, ForeignKey("fbi_repair_cases.id", ondelete="CASCADE"), nullable=False)
    run_id = Column(Text, default="")
    issue_id = Column(Text, nullable=False)
    round_index = Column(Integer, nullable=False, default=0)
    resolved_by_specialist = Column(Text, default="")
    patch_ids_json = Column(_JSONType, default=list)
    pre_text_hash = Column(Text, default="")
    post_text_hash = Column(Text, default="")
    validation_checker_ids_json = Column(_JSONType, default=list)
    validation_report_json = Column(_JSONType, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_fbi_resolved_issues_case", "case_id"),
        Index("ix_fbi_resolved_issues_issue", "issue_id",),
    )


class FBIProtectedSpanORM(Base):
    __tablename__ = "fbi_protected_spans"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(Text, ForeignKey("fbi_repair_cases.id", ondelete="CASCADE"), nullable=False)
    span_start = Column(Integer, nullable=False)
    span_end = Column(Integer, nullable=False)
    text_hash = Column(Text, default="")
    reason = Column(Text, nullable=False, default="resolved_issue")
    owner_issue_ids_json = Column(_JSONType, default=list)
    allowed_touch_by_json = Column(_JSONType, default=list)
    active = Column(Boolean, nullable=False, default=True)
    expires_at_round = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_fbi_protected_spans_case", "case_id"),
        Index("ix_fbi_protected_spans_case_active", "case_id", "active"),
    )


class FBIPatchConflictORM(Base):
    __tablename__ = "fbi_patch_conflicts"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(Text, ForeignKey("fbi_repair_cases.id", ondelete="CASCADE"), nullable=False)
    patch_id_a = Column(Text, nullable=False)
    patch_id_b = Column(Text, nullable=False)
    conflict_type = Column(Text, nullable=False, default="span_overlap")
    resolution = Column(Text, default="")
    resolved_patch_id = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_fbi_patch_conflicts_case", "case_id"),
    )


class FBIRecheckReportORM(Base):
    __tablename__ = "fbi_recheck_reports"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(Text, ForeignKey("fbi_repair_cases.id", ondelete="CASCADE"), nullable=False)
    run_id = Column(Text, default="")
    round_index = Column(Integer, nullable=False, default=0)
    recheck_type = Column(Text, nullable=False, default="issue_targeted")
    status = Column(Text, nullable=False, default="passed")
    checked_issue_ids_json = Column(_JSONType, default=list)
    report_json = Column(_JSONType, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_fbi_recheck_reports_case_round", "case_id", "round_index"),
    )


async def create_tables():
    async with engine.begin() as conn:
        if PGVECTOR_AVAILABLE and not _IS_SQLITE:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # SQLite create_all does not add columns to an existing table. Keep the
        # proposition lifecycle migration local, idempotent, and SQLite-first.
        if _IS_SQLITE:
            # Existing SQLite databases predate the lightweight list contract;
            # create_all does not add indexes to an existing table.
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_projects_summary_created ON projects("
                "created_at, id, name, description, genre, word_count_target, "
                "current_chapter, total_words, updated_at)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_projects_summary_owner_created ON projects("
                "owner_id, created_at, id, name, description, genre, word_count_target, "
                "current_chapter, total_words, updated_at)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_wv_observation_project_review_page "
                "ON worldview_observations(project_id, status, auto_promoted, confirmed_by, "
                "entity_type, chapter_number, created_at, id)"
            ))

            foreshadowing_rows = (
                await conn.execute(text("PRAGMA table_info(foreshadowing_lines)"))
            ).fetchall()
            foreshadowing_existing = {str(row[1]) for row in foreshadowing_rows}
            for column, ddl in (
                ("resolved_chapter", "INTEGER"),
                ("resolution_summary", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in foreshadowing_existing:
                    await conn.execute(text(f"ALTER TABLE foreshadowing_lines ADD COLUMN {column} {ddl}"))

            clue_rows = (
                await conn.execute(text("PRAGMA table_info(foreshadowing_clues)"))
            ).fetchall()
            clue_existing = {str(row[1]) for row in clue_rows}
            for column, ddl in (
                ("source_observation_id", "CHAR(36)"),
                ("source_fingerprint", "TEXT"),
            ):
                if column not in clue_existing:
                    await conn.execute(text(f"ALTER TABLE foreshadowing_clues ADD COLUMN {column} {ddl}"))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_foreshadowing_clues_source_observation "
                "ON foreshadowing_clues(source_observation_id)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_foreshadowing_clues_line_chapter "
                "ON foreshadowing_clues(foreshadowing_line_id, chapter_number)"
            ))

            diagnostic_rows = (
                await conn.execute(text("PRAGMA table_info(chapter_diagnostic_records)"))
            ).fetchall()
            diagnostic_existing = {str(row[1]) for row in diagnostic_rows}
            if "reference_baseline_id" not in diagnostic_existing:
                await conn.execute(text(
                    "ALTER TABLE chapter_diagnostic_records ADD COLUMN reference_baseline_id CHAR(36)"
                ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_chapter_diagnostics_reference_baseline "
                "ON chapter_diagnostic_records(reference_baseline_id)"
            ))

            rows = (await conn.execute(text("PRAGMA table_info(narrative_propositions)"))).fetchall()
            existing = {str(row[1]) for row in rows}
            for column, ddl in (
                ("lifecycle_status", "TEXT NOT NULL DEFAULT 'current'"),
                ("valid_from_chapter", "INTEGER"),
                ("valid_to_chapter", "INTEGER"),
                ("supersedes_id", "TEXT"),
                ("importance", "FLOAT NOT NULL DEFAULT 0.5"),
                ("last_confirmed_chapter", "INTEGER"),
                ("source_chunk_index", "INTEGER"),
            ):
                if column not in existing:
                    await conn.execute(text(f"ALTER TABLE narrative_propositions ADD COLUMN {column} {ddl}"))
            await conn.execute(text(
                "UPDATE narrative_propositions SET lifecycle_status = CASE "
                "WHEN status = 'retracted' THEN 'retracted' "
                "WHEN predicate_category = 'clue' THEN 'unresolved' "
                "WHEN truth_layer IN ('plan', 'future_hint') OR predicate_category = 'intention' THEN 'planned' "
                "WHEN predicate_category IN ('action', 'event') THEN 'historical_event' "
                "ELSE COALESCE(NULLIF(lifecycle_status, ''), 'current') END"
            ))
            await conn.execute(text(
                "UPDATE narrative_propositions SET "
                "valid_from_chapter = COALESCE(valid_from_chapter, chapter_number), "
                "last_confirmed_chapter = COALESCE(last_confirmed_chapter, chapter_number)"
            ))
            await conn.execute(text(
                "WITH ranked AS ("
                " SELECT id, chapter_number, ROW_NUMBER() OVER ("
                "  PARTITION BY project_id, COALESCE(NULLIF(subject_id, ''), subject_name), "
                "  predicate_category, CASE WHEN predicate_category = 'state' THEN predicate_name ELSE '' END "
                "  ORDER BY chapter_number DESC, scene_index DESC, generation_revision DESC, created_at DESC"
                " ) AS rn FROM narrative_propositions "
                " WHERE status = 'active' AND lifecycle_status = 'current' "
                " AND predicate_category IN ('state', 'location', 'ownership')"
                ") UPDATE narrative_propositions SET lifecycle_status = 'superseded', "
                "valid_to_chapter = chapter_number WHERE id IN (SELECT id FROM ranked WHERE rn > 1)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_narrative_propositions_project_lifecycle "
                "ON narrative_propositions(project_id, lifecycle_status)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_narrative_propositions_project_validity "
                "ON narrative_propositions(project_id, valid_from_chapter, valid_to_chapter)"
            ))
        # 方案16：为已存在的 workflow_steps 表补充可观测性列（幂等）
        if not _IS_SQLITE:
            for ddl in (
                "ALTER TABLE narrative_propositions ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'current'",
                "ALTER TABLE narrative_propositions ADD COLUMN IF NOT EXISTS valid_from_chapter INTEGER",
                "ALTER TABLE narrative_propositions ADD COLUMN IF NOT EXISTS valid_to_chapter INTEGER",
                "ALTER TABLE narrative_propositions ADD COLUMN IF NOT EXISTS supersedes_id TEXT",
                "ALTER TABLE narrative_propositions ADD COLUMN IF NOT EXISTS importance DOUBLE PRECISION NOT NULL DEFAULT 0.5",
                "ALTER TABLE narrative_propositions ADD COLUMN IF NOT EXISTS last_confirmed_chapter INTEGER",
                "ALTER TABLE narrative_propositions ADD COLUMN IF NOT EXISTS source_chunk_index INTEGER",
                "CREATE INDEX IF NOT EXISTS ix_narrative_propositions_project_lifecycle ON narrative_propositions(project_id, lifecycle_status)",
                "CREATE INDEX IF NOT EXISTS ix_narrative_propositions_project_validity ON narrative_propositions(project_id, valid_from_chapter, valid_to_chapter)",
            ):
                await conn.execute(text(ddl))
            for col, col_type, default in [
                ("skip_reason", "TEXT", "''"),
                ("phase_trace", "JSONB", "'[]'"),
                ("inputs_from", "JSONB", "'{}'"),
                ("output_to", "JSONB", "'[]'"),
            ]:
                await conn.execute(text(
                    f"DO $$ BEGIN "
                    f"ALTER TABLE workflow_steps ADD COLUMN IF NOT EXISTS {col} {col_type} DEFAULT {default};"
                    f" EXCEPTION WHEN OTHERS THEN NULL; END $$;"
                ))


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
