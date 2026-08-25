from app.services.scene_contract_protocol import (
    build_scene_contract_protocol,
    bind_contract_violation,
    hard_obligation_sources,
    stabilize_recheck_contract_violations,
)


def test_scene_contract_protocol_keeps_authority_layers_separate():
    protocol = build_scene_contract_protocol({
        "hard_facts": ["artifact-x is sealed"],
        "opening_state": "actor-a enters late",
        "ending_state": "artifact-x opens",
        "hard_must_show": ["actor-a uses key-y"],
        "must_show": ["old staging flourish"],
        "soft_guidance": ["make the prose punchier"],
    })

    assert protocol["hard_facts"] == ["artifact-x is sealed"]
    assert protocol["initial_state"] == ["actor-a enters late"]
    assert protocol["required_outcomes"] == ["artifact-x opens"]
    assert protocol["required_transitions"] == ["actor-a uses key-y"]
    assert protocol["performance_requirements"] == ["old staging flourish"]
    assert protocol["soft_guidance"] == ["make the prose punchier"]


def test_hard_obligation_sources_exclude_performance_and_soft_layers():
    obligations = hard_obligation_sources({
        "hard_facts": ["artifact-x is sealed"],
        "ending_state": "artifact-x opens",
        "must_show": ["old staging flourish"],
        "soft_guidance": ["make the prose punchier"],
    })

    assert obligations == {
        "hard_facts": ["artifact-x is sealed"],
        "required_outcomes": ["artifact-x opens"],
        "required_transitions": [],
    }


def test_contract_protocol_generates_stable_obligations():
    protocol = build_scene_contract_protocol({
        "scene_index": 2,
        "ending_state": "artifact-x opens",
        "hard_must_show": ["actor-a uses key-y"],
        "must_show": ["old staging flourish"],
    })

    obligations = protocol["obligations"]

    assert {item["layer"] for item in obligations} == {
        "required_outcomes",
        "required_transitions",
        "performance_requirements",
    }
    assert {item["scope"] for item in obligations} == {"scene"}
    assert {item["owner_scene"] for item in obligations} == {2}
    assert all(item["obligation_id"].startswith("obl_") for item in obligations)


def test_explicit_obligation_preserves_scope_owner_and_id():
    protocol = build_scene_contract_protocol({
        "scene_index": 1,
        "contract_protocol": {
            "required_transitions": [
                {
                    "obligation_id": "obl_memory",
                    "scope": "chapter",
                    "owner_scene": 1,
                    "text": "actor-a remembers the hidden map",
                }
            ]
        },
    })

    obligation = protocol["obligations"][0]

    assert obligation["obligation_id"] == "obl_memory"
    assert obligation["scope"] == "chapter"
    assert obligation["owner_scene"] is None
    assert obligation["ownership_explicit"] is True


def test_bind_contract_violation_attaches_obligation_metadata():
    bound = bind_contract_violation(
        {
            "scene_index": 1,
            "contract_protocol": {
                "required_transitions": [
                    {
                        "obligation_id": "obl_joint_action",
                        "scope": "scene",
                        "owner_scene": 1,
                        "text": "actor-a uses key-y",
                    }
                ]
            },
        },
        {
            "type": "missing_must_show",
            "detail": "actor-a uses key-y never appears",
            "blocks_commit": True,
        },
        scene_index=1,
    )

    assert bound["obligation_id"] == "obl_joint_action"
    assert bound["obligation_scope"] == "scene"
    assert bound["obligation_owner_scene"] == 1
    assert bound["obligation_owner_matches_scene"] is True


def test_recheck_demotes_new_unowned_contract_gap():
    kept, demoted = stabilize_recheck_contract_violations(
        initial_violations=[],
        recheck_blocking=[
            {
                "type": "missing_must_show",
                "severity": "high",
                "detail": "A hidden beat never appears",
                "blocks_commit": True,
            }
        ],
        scene_contract={"scene_index": 1, "must_show": ["A hidden beat"]},
        scene_index=1,
    )

    assert kept == []
    assert len(demoted) == 1
    assert demoted[0]["blocks_commit"] is False
    assert (
        demoted[0]["recheck_demotion_reason"]
        == "new_recheck_contract_gap_without_explicit_ownership"
    )


def test_recheck_keeps_explicit_scene_owned_contract_gap():
    kept, demoted = stabilize_recheck_contract_violations(
        initial_violations=[],
        recheck_blocking=[
            {
                "type": "missing_must_show",
                "severity": "high",
                "detail": "actor-a uses key-y never appears",
                "blocks_commit": True,
            }
        ],
        scene_contract={
            "scene_index": 1,
            "contract_protocol": {
                "required_transitions": [
                    {
                        "obligation_id": "obl_joint_action",
                        "scope": "scene",
                        "owner_scene": 1,
                        "text": "actor-a uses key-y",
                    }
                ]
            },
        },
        scene_index=1,
    )

    assert len(kept) == 1
    assert demoted == []
    assert kept[0]["obligation_id"] == "obl_joint_action"
