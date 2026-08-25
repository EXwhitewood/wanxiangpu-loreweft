"""异常分类器（方案15）。

区分可重试异常（LLM 超时、API 限流、网络错误、DB 临时错误）
与不可重试异常（contract 错误、编程错误、数据校验错误）。

为方案12（DAG持久化与崩溃恢复）提供断点续跑的决策输入：
- 可重试异常：自动重试，重试耗尽后标记 failed
- 不可重试异常：直接标记 failed，不重试
"""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class ErrorClassifier:
    """异常分类器，判断异常是否可重试。"""

    @staticmethod
    def is_retryable(error: Exception) -> bool:
        """判断异常是否可重试。

        可重试异常：LLM 超时、API 限流、DB 临时错误、网络错误
        不可重试异常：contract 错误、编程错误、数据校验错误
        """
        # 可重试异常类型
        if isinstance(error, (asyncio.TimeoutError, ConnectionError, httpx.ConnectError, httpx.ReadError)):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            if error.response.status_code in (429, 500, 502, 503, 504):
                return True
            return False

        error_str = str(error).lower()
        # 可重试的错误特征（包含下划线与空格两种写法以兼容真实 LLM/DB 错误消息）
        retryable_keywords = (
            "rate_limit", "rate limit", "too many requests",
            "timeout", "timed out",
            "temporary", "service unavailable", "internal server error",
            "connection reset", "connection closed",
        )
        for keyword in retryable_keywords:
            if keyword in error_str:
                return True

        # 不可重试的错误特征
        non_retryable_keywords = ("contract", "validation", "invalid", "missing required", "schema")
        for keyword in non_retryable_keywords:
            if keyword in error_str:
                return False

        # 编程错误不可重试
        if isinstance(error, (ValueError, TypeError, KeyError, AttributeError)):
            return False

        # 默认不可重试（保守策略）
        return False

    @staticmethod
    def is_contract_error(error: Exception) -> bool:
        """判断异常是否为 contract 错误（应触发 EditorDAGAbort 而非简单 failed）。"""
        error_str = str(error).lower()
        contract_keywords = ("contract", "contract_patch", "scene_contract", "validation", "schema")
        for keyword in contract_keywords:
            if keyword in error_str:
                return True
        return False

    @staticmethod
    def classify(error: Exception) -> str:
        """返回异常分类字符串（用于持久化 error_class 字段）。"""
        if isinstance(error, asyncio.TimeoutError):
            return "timeout"
        if isinstance(error, (httpx.ConnectError, httpx.ReadError, ConnectionError)):
            return "network"
        if isinstance(error, httpx.HTTPStatusError):
            if error.response.status_code == 429:
                return "rate_limit"
            if error.response.status_code >= 500:
                return "server_error"
            return f"http_{error.response.status_code}"
        if ErrorClassifier.is_contract_error(error):
            return "contract"
        if isinstance(error, (ValueError, TypeError, KeyError, AttributeError)):
            return "programming"
        return "unknown"
