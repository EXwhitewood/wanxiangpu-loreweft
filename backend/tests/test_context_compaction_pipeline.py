from __future__ import annotations

from app.services.compaction_validator import stable_hash
from app.services.context_compressor import ContextCompressor
from app.services.context_ledger_service import ContextLedgerService


def _large_findings(count: int = 120) -> list[dict]:
    return [
        {
            "finding_id": f"f{i}",
            "type": "style_noise",
            "severity": "low",
            "detail": "low value observation " * 12,
            "status": "open",
            "source_ref": f"review:{i}",
        }
        for i in range(count)
    ]


def test_context_compaction_preserves_protected_blocks_and_records_report():
    envelope = {
        "envelope_id": "ctx_test",
        "model_context_profile": {
            "profile_id": "tiny-test",
            "context_window_tokens": 1600,
            "reserve_output_tokens": 200,
            "soft_compaction_ratio": 0.2,
            "hard_compaction_ratio": 0.5,
        },
        "blocks": [
            {
                "block_id": "p0_scene_map",
                "block_type": "scene_map",
                "priority": "P0",
                "compressible": False,
                "content": {
                    "scene_map": [
                        {"scene_id": "c1-s1", "goal": "open"},
                        {"scene_id": "c1-s2", "goal": "close"},
                    ],
                    "forbidden": ["do not reveal the hidden identity"],
                },
            },
            {
                "block_id": "p1_state",
                "block_type": "story_state",
                "priority": "P1",
                "compressible": False,
                "content": {
                    "plot_beats": [{"text": "The heroine is displaced.", "source_ref": "state:1"}],
                    "object_states": [{"object": "book", "state": "on desk", "source_ref": "state:2"}],
                },
            },
            {
                "block_id": "p5_review_noise",
                "block_type": "review_findings",
                "priority": "P5",
                "compressible": True,
                "source_refs": ["review:bulk"],
                "content": _large_findings(),
            },
        ],
    }
    p0_hash = stable_hash(envelope["blocks"][0]["content"])
    p1_hash = stable_hash(envelope["blocks"][1]["content"])

    result = ContextCompressor().compact(envelope, manual_focus="keep object continuity")
    compacted = result["envelope"]
    report = result["report"]

    assert report["saved_tokens"] > 0
    assert report["blocks_compacted"][0]["block_id"] == "p5_review_noise"
    assert result["validation"]["passed"] is True
    assert compacted["token_report"]["compaction_applied"] is True
    assert stable_hash(compacted["blocks"][0]["content"]) == p0_hash
    assert stable_hash(compacted["blocks"][1]["content"]) == p1_hash


def test_context_ledger_records_latest_entry(tmp_path):
    service = ContextLedgerService(base_dir=tmp_path)
    envelope = {
        "model_context_profile": {"profile_id": "tiny-test"},
        "token_report": {
            "estimated_input_tokens": 321,
            "soft_input_limit_tokens": 1000,
            "hard_input_limit_tokens": 1200,
        },
        "blocks": [
            {
                "block_id": "scene_map",
                "block_type": "scene_map",
                "priority": "P0",
                "compressible": False,
                "estimated_tokens": 20,
                "source_refs": ["outline:1"],
            }
        ],
    }
    report = {
        "before_tokens": 500,
        "after_tokens": 321,
        "validation": {"passed": True},
    }

    entry = service.record(
        project_id="project-a",
        chapter_number=3,
        task_id="task-1",
        agent="chapter_writer",
        envelope=envelope,
        compaction_report=report,
    )

    assert service.get("project-a", entry["ledger_id"])["token_after"] == 321
    assert service.latest("project-a", 3, "chapter_writer")["ledger_id"] == entry["ledger_id"]
    assert service.latest("project-a", 2, "chapter_writer") is None
