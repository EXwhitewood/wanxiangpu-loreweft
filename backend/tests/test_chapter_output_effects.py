import uuid

import pytest

from app.db.db_models import ChapterSnapshot, ForeshadowingLine, NarrativePropositionORM, Project
from app.models.chapter_review import SceneReviewPacket
from app.services.chapter_output_effects import (
    build_scene_output_effects,
    merge_scene_facts_into_chapter_state,
)
from app.services.context_envelope_builder import ContextEnvelopeBuilder
from app.services.proposition_store_service import PropositionStoreService
from app.services.quality_memory_service import QualityMemoryService


def test_output_effects_enrich_character_state_and_fallback_propositions():
    text = "Lin opened the sealed file. Lin found the missing token."
    chapter_state = {"established_facts": [], "character_states": {}, "completed_events": [], "active_constraints": []}
    scene_facts = {
        "established_facts": ["Lin opened the sealed file."],
        "completed_events": ["Lin found the missing token."],
    }
    scene_contract = {"pov_character": "Lin", "location_anchor": "Archive"}

    merge_scene_facts_into_chapter_state(
        chapter_state,
        scene_facts,
        generated_text=text,
        character_cards=[{"name": "Lin"}],
        scene_contract=scene_contract,
    )

    assert "Lin" in chapter_state["character_states"]
    assert chapter_state["character_states"]["Lin"]["location"] == "Archive"

    packet = SceneReviewPacket(
        scene_index=0,
        advisory_violations=[
            {"type": "fragmented_paragraphs", "severity": "medium", "confidence": 0.8}
        ],
    )
    effects = build_scene_output_effects(
        project_id="project-1",
        chapter_number=3,
        scene_index=0,
        generated_text=text,
        scene_contract=scene_contract,
        scene_facts=scene_facts,
        review_packet=packet,
    )

    assert effects["propositions"]
    assert effects["proposition_audit_report"]["passed"] is False
    assert "deterministic_fallback_only" in effects["proposition_audit_report"]["warnings"]
    assert effects["proposition_extraction_status"] == "degraded"
    assert effects["fact_contract"]["current_facts"] == ["Lin opened the sealed file."]
    assert effects["experience_quality_reports"]["workflow_advisory"]["advisories"]


def test_audited_quality_report_replaces_degraded_proposition_status():
    effects = build_scene_output_effects(
        project_id="project-1",
        chapter_number=3,
        scene_index=0,
        generated_text="Lin opened the sealed file.",
        scene_contract={"pov_character": "Lin"},
        base_effects={"proposition_extraction_status": "degraded"},
        quality_gate_report={
            "reports": {
                "hard_correctness": {
                    "proposition_extraction": {
                        "propositions": [{"proposition_id": "audited-prop"}],
                    },
                    "proposition_audit": {
                        "passed": True,
                        "commit_blocked": False,
                    },
                },
                "narrative_contract": {},
            }
        },
    )

    assert effects["proposition_extraction_status"] == "complete"
    assert effects["propositions"] == [{"proposition_id": "audited-prop"}]


@pytest.mark.asyncio
async def test_proposition_store_reads_recent_chapter_window(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="proposition-window-test"))
    for chapter_number in (8, 9, 10):
        db.add(NarrativePropositionORM(
            id=f"prop-{chapter_number}",
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=0,
            generation_revision=1,
            subject_name=f"subject-{chapter_number}",
            subject_type="character",
            predicate_name="establishes",
            predicate_category="event",
            truth_layer="current",
            certainty="confirmed",
            polarity="affirmed",
            responsibility="unknown",
            source_text=f"source {chapter_number}",
            source_agent="test",
            confidence=0.9,
            status="active",
        ))
    await db.commit()

    rows = await PropositionStoreService().get_propositions_for_scene_preparation(
        db,
        project_id,
        chapter_number=10,
        max_chapters_back=3,
    )

    assert [row["proposition_id"] for row in rows] == ["prop-8", "prop-9", "prop-10"]


@pytest.mark.asyncio
async def test_proposition_revision_retracts_only_the_revised_chapter(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="proposition-revision-scope-test"))
    db.add_all([
        NarrativePropositionORM(
            id="prior-chapter-prop",
            project_id=project_id,
            chapter_number=25,
            scene_index=0,
            generation_revision=1,
            subject_name="Lin",
            subject_type="character",
            predicate_name="knows",
            predicate_category="knowledge",
            truth_layer="current",
            certainty="confirmed",
            polarity="affirmed",
            responsibility="unknown",
            source_text="Lin knows the seal is real.",
            source_agent="test",
            confidence=0.9,
            status="active",
        ),
        NarrativePropositionORM(
            id="revised-chapter-prop",
            project_id=project_id,
            chapter_number=26,
            scene_index=0,
            generation_revision=1,
            subject_name="Lin",
            subject_type="character",
            predicate_name="opens",
            predicate_category="event",
            truth_layer="current",
            certainty="confirmed",
            polarity="affirmed",
            responsibility="active_actor",
            source_text="Lin opened the seal.",
            source_agent="test",
            confidence=0.9,
            status="active",
        ),
    ])
    await db.commit()

    retracted = await PropositionStoreService().retract_propositions(
        db,
        project_id=project_id,
        chapter_number=26,
        generation_revision=2,
    )
    await db.commit()

    prior = await db.get(NarrativePropositionORM, "prior-chapter-prop")
    revised = await db.get(NarrativePropositionORM, "revised-chapter-prop")
    assert retracted == 1
    assert prior.status == "active"
    assert revised.status == "retracted"


@pytest.mark.asyncio
async def test_quality_memory_persists_workflow_advisory_to_project_core(db):
    project_id = uuid.uuid4()
    db.add(Project(id=project_id, name="quality-memory-test", core_data={}))
    await db.commit()

    await QualityMemoryService().persist_scene_quality_payload(
        db,
        project_id=project_id,
        chapter_number=4,
        payload={
            "scene_index": 1,
            "quality_reports": {
                "workflow_advisory": {
                    "advisories": [
                        {"type": "must_show_overload", "severity": "medium", "confidence": 0.9}
                    ]
                }
            },
        },
    )
    await db.commit()

    project = await db.get(Project, project_id)
    patterns = project.core_data["quality_memory"]["recent_quality_patterns"]
    assert patterns[0]["type"] == "must_show_overload"
    assert patterns[0]["last_seen_chapter"] == 4


@pytest.mark.asyncio
async def test_context_builder_loads_structured_memory(db):
    project_id = uuid.uuid4()
    project = Project(
        id=project_id,
        name="structured-memory-test",
        core_data={
            "quality_memory": {
                "recent_quality_patterns": [
                    {
                        "type": "fragmented_paragraphs",
                        "count": 3,
                        "last_seen_chapter": 1,
                        "last_seen_scene": 0,
                        "suggestion": "Keep paragraphs connected through a clear scene beat.",
                    }
                ]
            }
        },
    )
    db.add(project)
    db.add(ChapterSnapshot(
        project_id=project_id,
        chapter_number=2,
        chapter_state={
            "established_facts": ["Lin opened the sealed file."],
            "character_states": {"Lin": {"location": "Archive"}},
        },
        stale=False,
    ))
    db.add(NarrativePropositionORM(
        id="prop-context",
        project_id=project_id,
        chapter_number=2,
        scene_index=0,
        generation_revision=1,
        subject_name="Lin",
        subject_type="character",
        predicate_name="knows",
        predicate_category="knowledge",
        truth_layer="current",
        certainty="confirmed",
        polarity="affirmed",
        responsibility="active_actor",
        source_text="Lin knows the file is real.",
        source_agent="test",
        confidence=0.9,
        status="active",
    ))
    db.add(ForeshadowingLine(
        project_id=project_id,
        name="Sealed file origin",
        status="active",
        priority="high",
        secret_canonical_statement="The sealed file was planted by OrganizationZ.",
        secret_truth_type="past_event",
        bury_window_start=3,
        bury_window_end=3,
        reveal_window_start=8,
        reveal_window_end=10,
        reader_intended_state="suspicious",
        reader_allowed_interpretations=["The file is authentic", "The file may be bait"],
        reader_forbidden_interpretations=["OrganizationZ planted the file"],
    ))
    await db.commit()

    memory = await ContextEnvelopeBuilder()._load_structured_memory(
        db=db,
        project_id=str(project_id),
        chapter_number=3,
        project=project,
    )

    assert memory["active_character_states"]["Lin"]["location"] == "Archive"
    assert "active_propositions" not in memory
    assert memory["proposition_selection_trace"]["selected"][0]["proposition_id"] == "prop-context"
    assert memory["quality_memory"]["recent_quality_patterns"][0]["type"] == "fragmented_paragraphs"
    assert memory["active_foreshadowing"][0]["name"] == "Sealed file origin"
    assert "secret_canonical_statement" not in memory["active_foreshadowing"][0]
    assert memory["required_prior_facts"]
    assert memory["character_state_constraints"][0]["character_name"] == "Lin"
    assert memory["proposition_constraints"][0]["constraint"] == "preserve_established_claim"
    assert memory["foreshadowing_constraints"][0]["constraint"] == "plant_clue_without_confirming_secret"
    assert "secret_canonical_statement" not in memory["foreshadowing_constraints"][0]
    assert memory["quality_policy"]["writer_constraints"][0]["type"] == "fragmented_paragraphs"


def test_context_builder_enriches_scene_contracts_with_memory_and_budget():
    scene_contracts = [{
        "scene_id": "scene-1",
        "must_show": [f"beat-{idx}" for idx in range(10)],
        "quality_extensions": {"schema_version": 1},
    }]
    structured_memory = {
        "required_prior_facts": [
            {"type": "established_facts", "text": "Lin opened the sealed file."}
        ],
        "character_state_constraints": [
            {
                "character_name": "Lin",
                "state": {"location": "Archive"},
                "constraint": "do_not_contradict_without_explicit_transition",
            }
        ],
        "proposition_constraints": [
            {
                "claim": "Lin knows file",
                "source_text": "Lin knows the file is real.",
                "constraint": "preserve_established_claim",
            }
        ],
        "foreshadowing_constraints": [
            {
                "name": "Sealed file origin",
                "action": "protect_secret",
                "constraint": "forbid_premature_reveal",
                "reader_intended_state": "suspicious",
            }
        ],
        "quality_policy": {
            "writer_constraints": [
                {"type": "fragmented_paragraphs", "instruction": "Keep beats connected."}
            ],
            "planning_warnings": [],
        },
        "source_refs": ["chapter_snapshot_2"],
    }

    enriched = ContextEnvelopeBuilder()._enrich_scene_contracts_with_structured_memory(
        scene_contracts,
        structured_memory,
    )

    contract = enriched[0]
    assert scene_contracts[0] is contract
    assert contract["must_show"] == [f"beat-{idx}" for idx in range(5)]
    assert contract["hard_must_show"] == [f"beat-{idx}" for idx in range(5)]
    assert contract["soft_guidance"] == ["beat-5", "beat-6", "beat-7"]
    assert contract["deferred_items"] == ["beat-8", "beat-9"]
    assert contract["information_budget"]["over_budget"] is True
    assert contract["long_term_constraints"]["source_refs"] == ["chapter_snapshot_2"]
    assert contract["quality_extensions"]["quality_memory_guidance"] == ["Keep beats connected."]
    guardrail = contract["quality_extensions"]["proposition_guardrails"][0]
    assert guardrail["constraint"] == "preserve_established_claim"
    assert "claim" not in guardrail
    assert "source_text" not in guardrail
    assert contract["quality_extensions"]["foreshadowing_guardrails"][0]["constraint"] == "forbid_premature_reveal"
