import time
from collections import defaultdict


class CircuitBreaker:
    FAILURE_THRESHOLD = 3
    RECOVERY_TIMEOUT = 60
    HALF_OPEN_MAX_CALLS = 1

    def __init__(self):
        self._failure_counts: dict[str, int] = defaultdict(int)
        self._last_failure_time: dict[str, float] = defaultdict(float)
        self._state: dict[str, str] = defaultdict(lambda: "closed")
        self._half_open_calls: dict[str, int] = defaultdict(int)

    def is_available(self, agent_name: str) -> bool:
        state = self._state[agent_name]
        if state == "closed":
            return True
        if state == "open":
            elapsed = time.time() - self._last_failure_time[agent_name]
            if elapsed >= self.RECOVERY_TIMEOUT:
                self._state[agent_name] = "half_open"
                self._half_open_calls[agent_name] = 0
                return True
            return False
        if state == "half_open":
            return self._half_open_calls[agent_name] < self.HALF_OPEN_MAX_CALLS
        return True

    def record_success(self, agent_name: str) -> None:
        if self._state[agent_name] == "half_open":
            self._state[agent_name] = "closed"
        self._failure_counts[agent_name] = 0
        self._half_open_calls[agent_name] = 0

    def record_failure(self, agent_name: str) -> None:
        self._failure_counts[agent_name] += 1
        self._last_failure_time[agent_name] = time.time()
        if self._failure_counts[agent_name] >= self.FAILURE_THRESHOLD:
            self._state[agent_name] = "open"
        if self._state[agent_name] == "half_open":
            self._state[agent_name] = "open"

    def get_state(self, agent_name: str) -> dict:
        return {
            "agent": agent_name,
            "state": self._state[agent_name],
            "failure_count": self._failure_counts[agent_name],
            "last_failure": self._last_failure_time.get(agent_name, 0),
        }


circuit_breaker = CircuitBreaker()
