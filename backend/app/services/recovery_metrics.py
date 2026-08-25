import time
import threading


class RecoveryMetrics:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._reset()
            return cls._instance

    def _reset(self):
        self._parse_failures = 0
        self._parse_attempts = 0
        self._validator_retries = 0
        self._validator_recoveries = 0
        self._content_repairs = 0
        self._content_accepted = 0
        self._human_content_reviews = 0
        self._human_system_reviews = 0
        self._start_time = time.monotonic()

    def record_parse_attempt(self, success: bool):
        self._parse_attempts += 1
        if not success:
            self._parse_failures += 1

    def record_validator_retry(self, recovered: bool):
        self._validator_retries += 1
        if recovered:
            self._validator_recoveries += 1

    def record_content_repair(self, accepted: bool):
        self._content_repairs += 1
        if accepted:
            self._content_accepted += 1

    def record_human_review(self, review_type: str):
        if review_type == "content_review":
            self._human_content_reviews += 1
        elif review_type == "system_review":
            self._human_system_reviews += 1

    @property
    def parse_failure_rate(self) -> float:
        if self._parse_attempts == 0:
            return 0.0
        return self._parse_failures / self._parse_attempts

    @property
    def average_validator_retries(self) -> float:
        if self._validator_retries == 0:
            return 0.0
        return self._validator_retries / max(1, self._validator_recoveries + self._human_system_reviews)

    @property
    def auto_recovery_success_rate(self) -> float:
        total = self._validator_recoveries + self._human_system_reviews
        if total == 0:
            return 1.0
        return self._validator_recoveries / total

    def snapshot(self) -> dict:
        uptime = time.monotonic() - self._start_time
        return {
            "parse_attempts": self._parse_attempts,
            "parse_failures": self._parse_failures,
            "parse_failure_rate": round(self.parse_failure_rate, 4),
            "validator_retries": self._validator_retries,
            "validator_recoveries": self._validator_recoveries,
            "average_validator_retries": round(self.average_validator_retries, 2),
            "auto_recovery_success_rate": round(self.auto_recovery_success_rate, 4),
            "content_repairs": self._content_repairs,
            "content_accepted": self._content_accepted,
            "human_content_reviews": self._human_content_reviews,
            "human_system_reviews": self._human_system_reviews,
            "uptime_seconds": round(uptime, 1),
        }
