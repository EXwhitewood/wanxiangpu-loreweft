from app.services.fact_progression_protocol import apply_protocol_to_violations
from app.services.scene_recovery_controller import SceneRecoveryController
from app.services.unified_repair_router import UnifiedRepairRouter


def test_fact_contradiction_routes_to_fbi_prose():
    violations = apply_protocol_to_violations([{
        "type": "fact_conflict",
        "detail": "established object origin is contradicted",
        "blocks_commit": True,
        "scope": "prose_text",
        "suggested_strategy": "patch_text",
    }], {})

    route = UnifiedRepairRouter().route(violations, fbi_mode="assist")

    assert route.lane == "fbi_prose"
    assert route.strategy == "fbi_repair"
    assert route.fbi_repairable_count == 1


def test_validator_failure_routes_to_validator_retry_not_fbi():
    route = UnifiedRepairRouter().route([{
        "type": "consistency_check_unavailable",
        "detail": "consistency_response_parse_error",
        "blocks_commit": True,
        "scope": "validator_system",
    }], fbi_mode="assist")

    assert route.lane == "validator_retry"
    assert route.strategy == "validator_retry"
    assert route.validator_count == 1


def test_contract_conflict_routes_to_contract_repair():
    violations = apply_protocol_to_violations([{
        "type": "style_contract_conflict",
        "detail": "contract requirements are incompatible",
        "blocks_commit": True,
        "scope": "scene_contract",
        "suggested_strategy": "repair_contract",
    }], {})

    route = UnifiedRepairRouter().route(violations, fbi_mode="assist")

    assert route.lane == "contract_repair"
    assert route.strategy == "repair_contract"


def test_alias_and_retcon_route_to_human_governance():
    violations = apply_protocol_to_violations([{
        "type": "identity_conflict",
        "detail": "two names may refer to one entity",
        "blocks_commit": True,
    }], {})

    route = UnifiedRepairRouter().route(violations, fbi_mode="assist")

    assert route.lane == "human_governance"
    assert route.strategy == "blocked"


def test_non_blocking_progression_is_accepted():
    violations = apply_protocol_to_violations([{
        "type": "ownership_conflict",
        "detail": "物品被交给另一个角色，形成新的归属状态",
        "blocks_commit": True,
    }], {})

    route = UnifiedRepairRouter().route(violations, fbi_mode="assist")

    assert violations[0]["classification"] == "progression"
    assert violations[0]["blocks_commit"] is False
    assert route.lane == "accept"


def test_scene_recovery_prefers_fbi_for_repairable_text_conflict():
    controller = SceneRecoveryController()
    controller._context = {"generation_features": {"fbi_repair_mode": "assist"}}
    violations = apply_protocol_to_violations([{
        "type": "fact_conflict",
        "detail": "established fact is contradicted",
        "blocks_commit": True,
        "scope": "prose_text",
        "suggested_strategy": "patch_text",
    }], {})

    strategy = controller._decide_strategy({"violations": violations})

    assert strategy == "fbi_repair"
    assert controller._last_repair_route["lane"] == "fbi_prose"
