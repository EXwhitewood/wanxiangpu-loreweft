import os

FORESHADOWING_CONFIG = {
    "budget": {
        "max_active_count": int(os.environ.get("FS_BUDGET_MAX_ACTIVE", "3")),
        "clue_density_min": float(os.environ.get("FS_BUDGET_DENSITY_MIN", "0.02")),
        "clue_density_max": float(os.environ.get("FS_BUDGET_DENSITY_MAX", "0.12")),
    },
    "readiness": {
        "threshold": float(os.environ.get("FS_READINESS_THRESHOLD", "0.65")),
        "weights": {
            "evidence_coverage": 0.30,
            "causal_precondition": 0.20,
            "knowledge_alignment": 0.20,
            "reader_fairness": 0.15,
            "contradiction_absence": 0.15,
        },
    },
    "detection": {
        "block_on_critical": True,
        "suggest_on_high": True,
        "llm_timeout_seconds": int(os.environ.get("FS_LLM_TIMEOUT", "30")),
        "llm_max_retries": int(os.environ.get("FS_LLM_MAX_RETRIES", "1")),
    },
    "fcip": {
        "compiled_hint_max_tokens": int(os.environ.get("FS_FCIP_MAX_TOKENS", "500")),
        "repetition_distance_default": int(os.environ.get("FS_REPETITION_DIST", "2")),
    },
}
