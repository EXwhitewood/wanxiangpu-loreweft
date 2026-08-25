from app.agents.scene_critic import SceneCriticAgent
from app.agents.scene_validator import SceneValidator
from app.services.temporal_layer_policy import detect_temporal_layer_confusion


def test_memory_required_scene_allows_weak_memory_markers():
    contract = {
        "opening_state": "凤溪和五师兄来到山坳前，必须尽快拿到灵药。",
        "must_show": [
            "凤溪利用前世记忆认出灵药并制定采药策略",
            "凤溪凭借前世记忆炼制破境丹",
        ],
    }
    text = (
        "凤溪贴着岩壁看过去，记忆中那株灵草长在朝北的石缝里。"
        "她采下第二株时，又确认叶脉和记忆中的纹路一致。"
    )

    assert detect_temporal_layer_confusion(text, contract) is None
    critic_violations = SceneCriticAgent()._deterministic_check(text, contract, {})
    validator_violations = SceneValidator()._deterministic_check(text, contract, {})

    assert not _has_temporal_conflict(critic_violations)
    assert not _has_temporal_conflict(validator_violations)


def test_strong_flashback_markers_still_block_temporal_layer_confusion():
    contract = {
        "opening_state": "凤溪推门进入丹房，必须立刻查看炉火。",
    }
    text = (
        "凤溪推门进入丹房，却想起了三年前的雨夜。"
        "那时候她还不知道丹炉会炸。"
        "当时她只顾着逃，连炉火颜色都没看清。"
    )

    finding = detect_temporal_layer_confusion(text, contract)
    assert finding is not None
    assert finding["blocking_count"] >= 2
    assert "想起了" in finding["target_span"]

    critic_violations = SceneCriticAgent()._deterministic_check(text, contract, {})
    validator_violations = SceneValidator()._deterministic_check(text, contract, {})

    assert _has_temporal_conflict(critic_violations)
    assert _has_temporal_conflict(validator_violations)


def _has_temporal_conflict(violations: list[dict]) -> bool:
    return any(
        violation.get("type") == "temporal_layer_confusion"
        for violation in violations
    )
