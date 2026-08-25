from app.services.llm_metrics import (
    LLMetricsRegistry,
    pop_llm_metric_context,
    push_llm_metric_context,
)
from app.services.llm_task_profiles import LLMTaskType, get_profile


def test_llm_metrics_attribute_tokens_to_execution_step_and_task():
    metrics = LLMetricsRegistry()
    token = push_llm_metric_context(
        execution_id="exec-1",
        workflow_step="parallel_review_1",
        scene_index=0,
    )
    try:
        metrics.record_call(
            api_format="openai_compatible",
            model="model-a",
            task_type="json_audit",
            status="success",
            duration_s=1.25,
            usage={
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "prompt_cache_hit_tokens": 20,
                "prompt_cache_miss_tokens": 100,
            },
        )
    finally:
        pop_llm_metric_context(token)

    usage = metrics.usage_for_execution("exec-1")
    assert usage["call_count"] == 1
    assert usage["prompt_tokens"] == 120
    assert usage["completion_tokens"] == 30
    assert usage["cache_hit_tokens"] == 20
    assert usage["cache_miss_tokens"] == 100
    assert usage["by_step"]["parallel_review_1"]["call_count"] == 1
    assert usage["by_task"]["json_audit"]["completion_tokens"] == 30

    snapshot = metrics.to_dict()
    assert snapshot["tokens_by_task"][
        "openai_compatible|model-a|json_audit|completion"
    ] == 30


def test_non_creative_task_profiles_have_bounded_output_budgets():
    assert get_profile(LLMTaskType.FBI_REPAIR).max_tokens == 8192
    assert get_profile(LLMTaskType.JSON_AUDIT).max_tokens == 4096
    assert get_profile(LLMTaskType.SHORT_EXTRACTION).max_tokens == 2048
