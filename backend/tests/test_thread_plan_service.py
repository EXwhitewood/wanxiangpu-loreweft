from app.services.thread_plan_service import ThreadPlanService


def test_normalize_thread_plan_supports_guided_mode_aliases():
    service = ThreadPlanService()

    normalized = service._normalize_thread_plan({
        "threads": [{
            "thread_id": "thread_1",
            "name": "主线谜团",
            "type": "main",
            "status": "planned",
            "plant_chapters": [1, "2"],
            "payoff_chapters": [10],
        }]
    })

    thread = normalized["threads"][0]
    assert thread["thread_type"] == "main"
    assert thread["plant_chapters"] == [1, 2]
    assert thread["escalation_chapters"] == []
    assert thread["reveal_chapters"] == [10]
    assert thread["depends_on_threads"] == []


def test_normalize_thread_plan_handles_invalid_shape():
    service = ThreadPlanService()

    assert service._normalize_thread_plan(None) == {"threads": []}
    assert service._normalize_thread_plan({"threads": "invalid"}) == {"threads": []}
