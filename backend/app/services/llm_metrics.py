"""LLM 调用可观测性指标（P0-13 修复）。

设计原则：
1. 零外部依赖：用纯 Python threading.Lock + dict 实现，不引入 prometheus_client
2. 线程安全：所有指标更新加锁，适合多线程/多协程环境
3. 异步安全：指标更新是纯内存操作，无 I/O，不阻塞事件循环
4. 可导出：提供 to_dict() 供 /metrics 端点消费
5. 通用：不针对任何特定 LLM 调用，对所有调用一视同仁

指标维度：
- api_format / model / task_type / status
- 调用次数 / 耗时分布 / 重试次数 / token 用量 / 活跃请求数
"""
from __future__ import annotations

import threading
import time
from contextvars import ContextVar, Token
from collections import defaultdict
from typing import Any


_LLM_METRIC_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "loreweft_llm_metric_context",
    default={},
)


def push_llm_metric_context(**values: Any) -> Token:
    """Attach workflow/node attribution to downstream async LLM calls."""
    merged = dict(_LLM_METRIC_CONTEXT.get() or {})
    merged.update({key: value for key, value in values.items() if value not in (None, "")})
    return _LLM_METRIC_CONTEXT.set(merged)


def pop_llm_metric_context(token: Token) -> None:
    _LLM_METRIC_CONTEXT.reset(token)


def get_llm_metric_context() -> dict[str, Any]:
    return dict(_LLM_METRIC_CONTEXT.get() or {})


class LLMetricsRegistry:
    """LLM 调用指标注册表（线程安全单例）。

    使用 defaultdict + Lock 实现线程安全的指标聚合。
    所有指标按 (api_format, model, task_type, status) 维度细分。
    """

    _instance: "LLMetricsRegistry | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "LLMetricsRegistry":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._lock = threading.Lock()
        # 调用次数：{(api, model, task_type, status): count}
        self._calls: dict[tuple[str, str, str, str], int] = defaultdict(int)
        # 耗时直方图：{(api, model, task_type): [count_per_bucket]}
        # 桶边界（秒）：0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, +inf
        self._latency_buckets = (0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600)
        self._latency: dict[tuple[str, str, str], list[int]] = defaultdict(
            lambda: [0] * (len(self._latency_buckets) + 1)
        )
        self._latency_sum: dict[tuple[str, str, str], float] = defaultdict(float)
        self._latency_count: dict[tuple[str, str, str], int] = defaultdict(int)
        # 重试次数：{(api, model, reason): count}
        self._retries: dict[tuple[str, str, str], int] = defaultdict(int)
        # token 用量：{(api, model, token_type): count}
        self._tokens: dict[tuple[str, str, str], int] = defaultdict(int)
        # token 用量按任务细分，保留旧聚合键以兼容现有监控。
        self._tokens_by_task: dict[tuple[str, str, str, str], int] = defaultdict(int)
        # cache token 用量：{(api, model, cache_type): count}
        # cache_type: cache_hit_tokens, cache_miss_tokens
        self._cache_tokens: dict[tuple[str, str, str], int] = defaultdict(int)
        self._cache_tokens_by_task: dict[tuple[str, str, str, str], int] = defaultdict(int)
        # 工作流归因：execution / step / scene / task / status。
        self._scoped_calls: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
        self._scoped_latency: dict[tuple[str, str, str, str], float] = defaultdict(float)
        self._scoped_tokens: dict[tuple[str, str, str, str], int] = defaultdict(int)
        self._scoped_cache_tokens: dict[tuple[str, str, str, str], int] = defaultdict(int)
        # 活跃请求数（gauge 语义）
        self._active_requests: dict[tuple[str, str, str], int] = defaultdict(int)

    def record_call(
        self,
        *,
        api_format: str,
        model: str,
        task_type: str,
        status: str,  # success / error / timeout / rate_limited
        duration_s: float,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """记录一次 LLM 调用。线程安全。"""
        with self._lock:
            metric_context = get_llm_metric_context()
            execution_id = str(metric_context.get("execution_id") or "")
            workflow_step = str(metric_context.get("workflow_step") or "")
            scene_index = str(metric_context.get("scene_index") if metric_context.get("scene_index") is not None else "")
            normalized_task = str(task_type or "untyped")
            key = (api_format, model, task_type, status)
            self._calls[key] += 1

            lat_key = (api_format, model, task_type)
            bucket_idx = self._bucket_index(duration_s)
            self._latency[lat_key][bucket_idx] += 1
            self._latency_sum[lat_key] += duration_s
            self._latency_count[lat_key] += 1

            if execution_id:
                scope_key = (
                    execution_id,
                    workflow_step,
                    scene_index,
                    normalized_task,
                    status,
                )
                self._scoped_calls[scope_key] += 1
                self._scoped_latency[scope_key[:-1]] += duration_s

            if usage and isinstance(usage, dict):
                for token_type, field_name in (
                    ("prompt", "prompt_tokens"),
                    ("completion", "completion_tokens"),
                ):
                    val = usage.get(field_name)
                    if isinstance(val, (int, float)) and val > 0:
                        self._tokens[(api_format, model, token_type)] += int(val)
                        self._tokens_by_task[(api_format, model, normalized_task, token_type)] += int(val)
                        if execution_id:
                            self._scoped_tokens[
                                (execution_id, workflow_step, scene_index, f"{normalized_task}|{token_type}")
                            ] += int(val)
                # 费用优化：记录 cache hit/miss tokens（DeepSeek 字段名）
                cache_hit = usage.get("prompt_cache_hit_tokens") or 0
                cache_miss = usage.get("prompt_cache_miss_tokens") or 0
                # OpenAI 兼容字段
                if not cache_hit:
                    cache_hit = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                if isinstance(cache_hit, (int, float)) and cache_hit > 0:
                    self._cache_tokens[(api_format, model, "cache_hit_tokens")] += int(cache_hit)
                    self._cache_tokens_by_task[(api_format, model, normalized_task, "cache_hit_tokens")] += int(cache_hit)
                    if execution_id:
                        self._scoped_cache_tokens[
                            (execution_id, workflow_step, scene_index, f"{normalized_task}|cache_hit_tokens")
                        ] += int(cache_hit)
                if isinstance(cache_miss, (int, float)) and cache_miss > 0:
                    self._cache_tokens[(api_format, model, "cache_miss_tokens")] += int(cache_miss)
                    self._cache_tokens_by_task[(api_format, model, normalized_task, "cache_miss_tokens")] += int(cache_miss)
                    if execution_id:
                        self._scoped_cache_tokens[
                            (execution_id, workflow_step, scene_index, f"{normalized_task}|cache_miss_tokens")
                        ] += int(cache_miss)

    def _bucket_index(self, seconds: float) -> int:
        for i, bound in enumerate(self._latency_buckets):
            if seconds <= bound:
                return i
        return len(self._latency_buckets)  # +inf 桶

    def record_retry(
        self,
        *,
        api_format: str,
        model: str,
        reason: str,  # timeout / http_429 / http_5xx / network_error
    ) -> None:
        """记录一次重试。线程安全。"""
        with self._lock:
            self._retries[(api_format, model, reason)] += 1

    def inc_active(self, *, api_format: str, model: str, task_type: str) -> None:
        """增加活跃请求数（调用开始时）。线程安全。"""
        with self._lock:
            self._active_requests[(api_format, model, task_type)] += 1

    def dec_active(self, *, api_format: str, model: str, task_type: str) -> None:
        """减少活跃请求数（调用结束时）。线程安全。"""
        with self._lock:
            key = (api_format, model, task_type)
            current = self._active_requests.get(key, 0)
            if current > 0:
                self._active_requests[key] = current - 1

    def to_dict(self) -> dict[str, Any]:
        """导出所有指标为 dict（供 /metrics 端点消费）。线程安全。"""
        with self._lock:
            return {
                "calls": {f"{k[0]}|{k[1]}|{k[2]}|{k[3]}": v for k, v in self._calls.items()},
                "latency": {
                    f"{k[0]}|{k[1]}|{k[2]}": {
                        "buckets": dict(zip(
                            [str(b) for b in self._latency_buckets] + ["+inf"],
                            v,
                        )),
                        "sum_seconds": self._latency_sum.get(k, 0.0),
                        "count": self._latency_count.get(k, 0),
                        "avg_seconds": (
                            self._latency_sum.get(k, 0.0) / self._latency_count.get(k, 1)
                            if self._latency_count.get(k, 0) > 0
                            else 0.0
                        ),
                    }
                    for k, v in self._latency.items()
                },
                "retries": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in self._retries.items()},
                "tokens": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in self._tokens.items()},
                "tokens_by_task": {
                    f"{k[0]}|{k[1]}|{k[2]}|{k[3]}": v
                    for k, v in self._tokens_by_task.items()
                },
                "cache_tokens": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in self._cache_tokens.items()},
                "cache_tokens_by_task": {
                    f"{k[0]}|{k[1]}|{k[2]}|{k[3]}": v
                    for k, v in self._cache_tokens_by_task.items()
                },
                "cache_summary": self._cache_summary(),
                "usage_by_execution": {
                    execution_id: self._usage_for_execution_unlocked(execution_id)
                    for execution_id in sorted({key[0] for key in self._scoped_calls})
                },
                "active_requests": {
                    f"{k[0]}|{k[1]}|{k[2]}": v for k, v in self._active_requests.items()
                },
                "snapshot_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

    def reset(self) -> None:
        """重置所有指标（测试用）。线程安全。"""
        with self._lock:
            self._calls.clear()
            self._latency.clear()
            self._latency_sum.clear()
            self._latency_count.clear()
            self._retries.clear()
            self._tokens.clear()
            self._tokens_by_task.clear()
            self._cache_tokens.clear()
            self._cache_tokens_by_task.clear()
            self._scoped_calls.clear()
            self._scoped_latency.clear()
            self._scoped_tokens.clear()
            self._scoped_cache_tokens.clear()
            self._active_requests.clear()

    def usage_for_execution(self, execution_id: str) -> dict[str, Any]:
        with self._lock:
            return self._usage_for_execution_unlocked(str(execution_id))

    def _usage_for_execution_unlocked(self, execution_id: str) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "execution_id": execution_id,
            "call_count": 0,
            "latency_seconds": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "by_step": {},
            "by_task": {},
        }

        def bucket(container: dict, name: str) -> dict:
            return container.setdefault(name or "unscoped", {
                "call_count": 0,
                "latency_seconds": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
            })

        for (scope_execution, step, _scene, task, _status), count in self._scoped_calls.items():
            if scope_execution != execution_id:
                continue
            summary["call_count"] += count
            bucket(summary["by_step"], step)["call_count"] += count
            bucket(summary["by_task"], task)["call_count"] += count

        for (scope_execution, step, _scene, task), seconds in self._scoped_latency.items():
            if scope_execution != execution_id:
                continue
            summary["latency_seconds"] += seconds
            bucket(summary["by_step"], step)["latency_seconds"] += seconds
            bucket(summary["by_task"], task)["latency_seconds"] += seconds

        for source, field_map in (
            (self._scoped_tokens, {"prompt": "prompt_tokens", "completion": "completion_tokens"}),
            (self._scoped_cache_tokens, {
                "cache_hit_tokens": "cache_hit_tokens",
                "cache_miss_tokens": "cache_miss_tokens",
            }),
        ):
            for (scope_execution, step, _scene, task_and_type), amount in source.items():
                if scope_execution != execution_id:
                    continue
                task, token_type = task_and_type.rsplit("|", 1)
                field = field_map.get(token_type)
                if not field:
                    continue
                summary[field] += amount
                bucket(summary["by_step"], step)[field] += amount
                bucket(summary["by_task"], task)[field] += amount

        summary["latency_seconds"] = round(summary["latency_seconds"], 3)
        for dimension in ("by_step", "by_task"):
            for item in summary[dimension].values():
                item["latency_seconds"] = round(item["latency_seconds"], 3)
        return summary

    def _cache_summary(self) -> dict[str, Any]:
        """汇总 cache hit/miss 统计（供费用优化监控）。"""
        total_hit = 0
        total_miss = 0
        for (api, model, cache_type), count in self._cache_tokens.items():
            if cache_type == "cache_hit_tokens":
                total_hit += count
            elif cache_type == "cache_miss_tokens":
                total_miss += count
        total = total_hit + total_miss
        return {
            "cache_hit_tokens": total_hit,
            "cache_miss_tokens": total_miss,
            "cache_hit_rate": round(total_hit / total, 4) if total > 0 else 0.0,
            "total_input_tokens": total,
        }


def get_llm_metrics() -> LLMetricsRegistry:
    """获取 LLM 指标注册表单例。"""
    return LLMetricsRegistry()
