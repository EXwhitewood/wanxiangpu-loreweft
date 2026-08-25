"""轻量级告警服务（单例）。

提供内存级告警收集，用于关键失败事件的可观测性。
不依赖外部告警系统，仅维护带时间戳的告警列表。
"""
import threading
import time
from typing import Literal


AlertLevel = Literal["info", "warning", "critical"]


class AlertService:
    """告警服务单例。

    线程安全，内部维护一个 alerts 列表（带时间戳）。
    提供 alert() / list_alerts() / clear_alerts() 方法。
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._alerts: list[dict] = []
                    cls._instance._alerts_lock = threading.Lock()
                    cls._instance._max_alerts = 500
        return cls._instance

    def alert(self, level: AlertLevel, event: str, detail: str = "") -> None:
        """记录一条告警。

        Args:
            level: 告警级别 info/warning/critical
            event: 事件名称（简短标识）
            detail: 详情文本
        """
        if level not in ("info", "warning", "critical"):
            level = "info"
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": level,
            "event": str(event),
            "detail": str(detail),
        }
        with self._alerts_lock:
            self._alerts.append(entry)
            if len(self._alerts) > self._max_alerts:
                self._alerts = self._alerts[-self._max_alerts:]

    def list_alerts(self) -> list[dict]:
        """返回所有告警（按时间倒序）。"""
        with self._alerts_lock:
            return list(reversed(self._alerts))

    def clear_alerts(self) -> int:
        """清空告警，返回被清除的数量。"""
        with self._alerts_lock:
            count = len(self._alerts)
            self._alerts = []
            return count


def get_alert_service() -> AlertService:
    """获取 AlertService 单例。"""
    return AlertService()
