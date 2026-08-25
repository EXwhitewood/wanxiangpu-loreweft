"""方案15：ErrorClassifier 异常分类与重试测试。

覆盖任务：
- ErrorClassifier.is_retryable：可重试/不可重试异常判断
- ErrorClassifier.is_contract_error：contract 错误识别
- ErrorClassifier.classify：异常分类字符串
- _is_transient_review_error 委托验证：DB 异常类名补充 + ErrorClassifier 委托
- handle_parallel_scene_repair 异常分类行为验证（通过 ErrorClassifier 接口模拟）
"""
from __future__ import annotations

import asyncio

import httpx

from app.services.fbi.error_classifier import ErrorClassifier


# ---------------------------------------------------------------------------
# 1. is_retryable：可重试异常
# ---------------------------------------------------------------------------


class TestIsRetryableRetryable:
    """可重试异常应返回 True。"""

    def test_timeout_error(self):
        """asyncio.TimeoutError / 内置 TimeoutError → 可重试。"""
        assert ErrorClassifier.is_retryable(asyncio.TimeoutError()) is True
        # 内置 TimeoutError 在 Python 3.11+ 是 asyncio.TimeoutError 的别名，
        # 在更早版本通过 "timed out" 消息关键词匹配
        assert ErrorClassifier.is_retryable(TimeoutError("timed out")) is True

    def test_connection_error(self):
        """内置 ConnectionError → 可重试。"""
        assert ErrorClassifier.is_retryable(ConnectionError("connection dropped")) is True

    def test_httpx_connect_error(self):
        """httpx.ConnectError → 可重试。"""
        assert ErrorClassifier.is_retryable(httpx.ConnectError("connection refused")) is True

    def test_httpx_read_error(self):
        """httpx.ReadError → 可重试。"""
        assert ErrorClassifier.is_retryable(httpx.ReadError("read timeout")) is True

    def test_rate_limit_message(self):
        """rate_limit / rate limit 消息 → 可重试。"""
        # 下划线写法
        assert ErrorClassifier.is_retryable(Exception("rate_limit exceeded")) is True
        # 空格写法（兼容真实 LLM 错误消息）
        assert ErrorClassifier.is_retryable(Exception("rate limit exceeded")) is True
        assert ErrorClassifier.is_retryable(Exception("too many requests")) is True

    def test_timeout_message(self):
        """timeout / timed out 消息 → 可重试。"""
        assert ErrorClassifier.is_retryable(Exception("operation timeout")) is True
        assert ErrorClassifier.is_retryable(Exception("request timed out")) is True

    def test_service_unavailable_message(self):
        """service unavailable / internal server error 消息 → 可重试。"""
        assert ErrorClassifier.is_retryable(Exception("service unavailable")) is True
        assert ErrorClassifier.is_retryable(Exception("internal server error")) is True

    def test_connection_reset_message(self):
        """connection reset / connection closed 消息 → 可重试。"""
        assert ErrorClassifier.is_retryable(Exception("Connection reset by peer")) is True
        assert ErrorClassifier.is_retryable(Exception("connection closed")) is True

    def test_httpx_429_status_error(self):
        """httpx.HTTPStatusError 429 → 可重试。"""
        request = httpx.Request("POST", "http://test")
        response = httpx.Response(429, request=request)
        error = httpx.HTTPStatusError("rate limited", request=request, response=response)
        assert ErrorClassifier.is_retryable(error) is True

    def test_httpx_503_status_error(self):
        """httpx.HTTPStatusError 503 → 可重试。"""
        request = httpx.Request("POST", "http://test")
        response = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError("unavailable", request=request, response=response)
        assert ErrorClassifier.is_retryable(error) is True


# ---------------------------------------------------------------------------
# 2. is_retryable：不可重试异常
# ---------------------------------------------------------------------------


class TestIsRetryableNonRetryable:
    """不可重试异常应返回 False。"""

    def test_value_error(self):
        """ValueError → 不可重试。"""
        assert ErrorClassifier.is_retryable(ValueError("invalid argument")) is False

    def test_type_error(self):
        """TypeError → 不可重试。"""
        assert ErrorClassifier.is_retryable(TypeError("wrong type")) is False

    def test_key_error(self):
        """KeyError → 不可重试。"""
        assert ErrorClassifier.is_retryable(KeyError("missing_key")) is False

    def test_attribute_error(self):
        """AttributeError → 不可重试。"""
        assert ErrorClassifier.is_retryable(AttributeError("no attribute")) is False

    def test_contract_message(self):
        """contract 错误消息 → 不可重试。"""
        assert ErrorClassifier.is_retryable(Exception("contract_patch validation failed")) is False
        assert ErrorClassifier.is_retryable(RuntimeError("scene_contract mismatch")) is False

    def test_validation_message(self):
        """validation 错误消息 → 不可重试。"""
        assert ErrorClassifier.is_retryable(Exception("validation error: missing field")) is False

    def test_schema_message(self):
        """schema 错误消息 → 不可重试。"""
        assert ErrorClassifier.is_retryable(Exception("schema violation")) is False

    def test_httpx_400_status_error(self):
        """httpx.HTTPStatusError 400 → 不可重试。"""
        request = httpx.Request("POST", "http://test")
        response = httpx.Response(400, request=request)
        error = httpx.HTTPStatusError("bad request", request=request, response=response)
        assert ErrorClassifier.is_retryable(error) is False

    def test_generic_exception_default(self):
        """无法识别的普通异常 → 默认不可重试（保守策略）。"""
        assert ErrorClassifier.is_retryable(Exception("something unexpected")) is False


# ---------------------------------------------------------------------------
# 3. is_contract_error
# ---------------------------------------------------------------------------


class TestIsContractError:
    """contract 错误识别。"""

    def test_contract_keyword(self):
        """包含 'contract' → True。"""
        assert ErrorClassifier.is_contract_error(Exception("contract violation")) is True

    def test_contract_patch_keyword(self):
        """包含 'contract_patch' → True（'contract' 是子串）。"""
        assert ErrorClassifier.is_contract_error(RuntimeError("contract_patch failed")) is True

    def test_scene_contract_keyword(self):
        """包含 'scene_contract' → True。"""
        assert ErrorClassifier.is_contract_error(Exception("scene_contract mismatch")) is True

    def test_validation_keyword(self):
        """包含 'validation' → True。"""
        assert ErrorClassifier.is_contract_error(Exception("validation error")) is True

    def test_schema_keyword(self):
        """包含 'schema' → True。"""
        assert ErrorClassifier.is_contract_error(Exception("schema error")) is True

    def test_case_insensitive(self):
        """大小写不敏感。"""
        assert ErrorClassifier.is_contract_error(Exception("CONTRACT ERROR")) is True
        assert ErrorClassifier.is_contract_error(Exception("Validation Failed")) is True

    def test_non_contract_error(self):
        """不含 contract 关键词 → False。"""
        assert ErrorClassifier.is_contract_error(Exception("timeout error")) is False
        assert ErrorClassifier.is_contract_error(ValueError("invalid value")) is False
        assert ErrorClassifier.is_contract_error(ConnectionError("network down")) is False


# ---------------------------------------------------------------------------
# 4. classify
# ---------------------------------------------------------------------------


class TestClassify:
    """异常分类字符串。"""

    def test_timeout_class(self):
        """asyncio.TimeoutError → 'timeout'。"""
        assert ErrorClassifier.classify(asyncio.TimeoutError()) == "timeout"

    def test_connection_error_class(self):
        """ConnectionError → 'network'。"""
        assert ErrorClassifier.classify(ConnectionError("dropped")) == "network"

    def test_httpx_connect_error_class(self):
        """httpx.ConnectError → 'network'。"""
        assert ErrorClassifier.classify(httpx.ConnectError("refused")) == "network"

    def test_httpx_429_class(self):
        """httpx.HTTPStatusError 429 → 'rate_limit'。"""
        request = httpx.Request("POST", "http://test")
        response = httpx.Response(429, request=request)
        error = httpx.HTTPStatusError("rate limited", request=request, response=response)
        assert ErrorClassifier.classify(error) == "rate_limit"

    def test_httpx_500_class(self):
        """httpx.HTTPStatusError 500 → 'server_error'。"""
        request = httpx.Request("POST", "http://test")
        response = httpx.Response(500, request=request)
        error = httpx.HTTPStatusError("server error", request=request, response=response)
        assert ErrorClassifier.classify(error) == "server_error"

    def test_httpx_404_class(self):
        """httpx.HTTPStatusError 404 → 'http_404'。"""
        request = httpx.Request("POST", "http://test")
        response = httpx.Response(404, request=request)
        error = httpx.HTTPStatusError("not found", request=request, response=response)
        assert ErrorClassifier.classify(error) == "http_404"

    def test_contract_class(self):
        """contract 错误 → 'contract'。"""
        assert ErrorClassifier.classify(Exception("contract_patch failed")) == "contract"

    def test_programming_class(self):
        """编程错误 → 'programming'。"""
        assert ErrorClassifier.classify(ValueError("bad value")) == "programming"
        assert ErrorClassifier.classify(TypeError("wrong type")) == "programming"
        assert ErrorClassifier.classify(KeyError("missing")) == "programming"
        assert ErrorClassifier.classify(AttributeError("no attr")) == "programming"

    def test_unknown_class(self):
        """无法识别的异常 → 'unknown'。"""
        assert ErrorClassifier.classify(Exception("something weird")) == "unknown"


# ---------------------------------------------------------------------------
# 5. _is_transient_review_error 委托验证
# ---------------------------------------------------------------------------


class TestIsTransientReviewErrorDelegation:
    """验证 _is_transient_review_error 正确委托给 ErrorClassifier。"""

    def test_timeout_delegates(self):
        """TimeoutError → True（通过 ErrorClassifier.is_retryable）。"""
        from app.api.editor_chat import _is_transient_review_error

        assert _is_transient_review_error(asyncio.TimeoutError()) is True
        assert _is_transient_review_error(TimeoutError("timed out")) is True

    def test_connection_error_delegates(self):
        """内置 ConnectionError → True（通过 ErrorClassifier.is_retryable）。"""
        from app.api.editor_chat import _is_transient_review_error

        assert _is_transient_review_error(ConnectionError("dropped")) is True

    def test_db_operational_error_by_name(self):
        """DB OperationalError（按类名匹配）→ True（ErrorClassifier 未覆盖的补充）。"""
        from app.api.editor_chat import _is_transient_review_error

        # 模拟 SQLAlchemy OperationalError（类名匹配）
        class OperationalError(Exception):
            pass

        assert _is_transient_review_error(OperationalError("connection lost")) is True

    def test_db_interface_error_by_name(self):
        """DB InterfaceError（按类名匹配）→ True。"""
        from app.api.editor_chat import _is_transient_review_error

        class InterfaceError(Exception):
            pass

        assert _is_transient_review_error(InterfaceError("interface down")) is True

    def test_rate_limit_message_delegates(self):
        """rate limit 消息 → True（通过 ErrorClassifier.is_retryable 消息关键词）。"""
        from app.api.editor_chat import _is_transient_review_error

        class RateLimitError(Exception):
            pass

        assert _is_transient_review_error(RateLimitError("rate limit exceeded")) is True

    def test_connection_reset_message_delegates(self):
        """connection reset 消息 → True（通过 ErrorClassifier.is_retryable 消息关键词）。"""
        from app.api.editor_chat import _is_transient_review_error

        class SomeNetworkError(Exception):
            pass

        assert _is_transient_review_error(SomeNetworkError("Connection reset by peer")) is True

    def test_non_retryable_delegates(self):
        """编程错误 → False（通过 ErrorClassifier.is_retryable）。"""
        from app.api.editor_chat import _is_transient_review_error

        assert _is_transient_review_error(ValueError("invalid argument")) is False
        assert _is_transient_review_error(TypeError("wrong type")) is False
        assert _is_transient_review_error(KeyError("missing_key")) is False

    def test_contract_error_delegates(self):
        """contract 错误消息 → False（ErrorClassifier 判定为不可重试）。"""
        from app.api.editor_chat import _is_transient_review_error

        assert _is_transient_review_error(Exception("contract_patch failed")) is False
        assert _is_transient_review_error(Exception("validation error")) is False


# ---------------------------------------------------------------------------
# 6. handle_parallel_scene_repair 异常分类行为验证
# ---------------------------------------------------------------------------


class TestParallelSceneRepairErrorClassification:
    """验证 handle_parallel_scene_repair 使用的 ErrorClassifier 接口行为。

    由于 handle_parallel_scene_repair 是嵌套在大型函数内的闭包，无法直接单测。
    这里通过验证其依赖的 ErrorClassifier 接口行为来间接覆盖：
    - contract 错误 → is_contract_error 返回 True（走 waiting_review + EditorDAGAbort 分支）
    - 非 contract 可重试错误 → is_contract_error False + is_retryable True（标 failed + retryable=True）
    - 非 contract 不可重试错误 → is_contract_error False + is_retryable False（标 failed + retryable=False）
    """

    def test_contract_patch_error_triggers_contract_branch(self):
        """contract_patch 异常被 is_contract_error 识别（替代旧的手动字符串匹配）。"""
        exc = RuntimeError("contract_patch validation failed")
        # 旧逻辑：is_contract_error = "contract_patch" in str(e) or "contract" in str(e).lower()
        old_result = "contract_patch" in str(exc) or "contract" in str(exc).lower()
        # 新逻辑：ErrorClassifier.is_contract_error
        new_result = ErrorClassifier.is_contract_error(exc)
        # 两者必须一致（确保替换不改变行为）
        assert old_result == new_result is True

    def test_plain_contract_error_triggers_contract_branch(self):
        """仅含 'contract' 的异常被 is_contract_error 识别。"""
        exc = RuntimeError("contract validation failed")
        old_result = "contract_patch" in str(exc) or "contract" in str(exc).lower()
        new_result = ErrorClassifier.is_contract_error(exc)
        assert old_result == new_result is True

    def test_non_contract_timeout_is_retryable(self):
        """非 contract 的 timeout 异常：is_contract_error=False + is_retryable=True。"""
        exc = asyncio.TimeoutError()
        assert ErrorClassifier.is_contract_error(exc) is False
        assert ErrorClassifier.is_retryable(exc) is True
        assert ErrorClassifier.classify(exc) == "timeout"

    def test_non_contract_value_error_is_non_retryable(self):
        """非 contract 的 ValueError：is_contract_error=False + is_retryable=False。"""
        exc = ValueError("bad input")
        assert ErrorClassifier.is_contract_error(exc) is False
        assert ErrorClassifier.is_retryable(exc) is False
        assert ErrorClassifier.classify(exc) == "programming"

    def test_validation_error_is_contract_and_non_retryable(self):
        """validation 错误：被 is_contract_error 识别（覆盖范围比旧的 'contract' 更广）。"""
        exc = Exception("validation failed for field X")
        # 旧逻辑不识别（不含 'contract'）
        old_result = "contract_patch" in str(exc) or "contract" in str(exc).lower()
        assert old_result is False
        # 新逻辑识别（'validation' 关键词）—— 这是覆盖范围的扩展
        new_result = ErrorClassifier.is_contract_error(exc)
        assert new_result is True
        # 且不可重试
        assert ErrorClassifier.is_retryable(exc) is False

    def test_retryable_error_output_fields(self):
        """验证可重试异常的 output 字段结构（retryable + error_class）。"""
        exc = asyncio.TimeoutError()
        is_retryable = ErrorClassifier.is_retryable(exc)
        error_class = ErrorClassifier.classify(exc)
        # 模拟 handle_parallel_scene_repair 的 output 结构
        output = {
            "error": str(exc),
            "repair_blocked": False,
            "retryable": is_retryable,
            "error_class": error_class,
        }
        assert output["retryable"] is True
        assert output["error_class"] == "timeout"
        assert output["repair_blocked"] is False

    def test_non_retryable_error_output_fields(self):
        """验证不可重试异常的 output 字段结构。"""
        exc = TypeError("wrong type")
        is_retryable = ErrorClassifier.is_retryable(exc)
        error_class = ErrorClassifier.classify(exc)
        output = {
            "error": str(exc),
            "repair_blocked": False,
            "retryable": is_retryable,
            "error_class": error_class,
        }
        assert output["retryable"] is False
        assert output["error_class"] == "programming"
