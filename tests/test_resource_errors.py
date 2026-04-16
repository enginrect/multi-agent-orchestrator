"""Tests for agent resource limit errors and classification."""

from __future__ import annotations

import pytest

from orchestrator.domain.errors import (
    AgentProviderRefusalError,
    AgentQuotaLimitError,
    AgentRateLimitError,
    AgentResourceLimitError,
    AgentTokenLimitError,
    classify_resource_error,
)


class TestAgentResourceLimitErrors:
    """Tests for the error hierarchy."""

    def test_base_error_str(self):
        err = AgentResourceLimitError("claude", "limit hit")
        assert "claude" in str(err)
        assert "limit hit" in str(err)

    def test_retry_after_in_message(self):
        err = AgentRateLimitError("codex", "rate limit", retry_after=60)
        assert "60s" in str(err)
        assert err.retry_after == 60

    def test_token_limit_is_resource_limit(self):
        err = AgentTokenLimitError("cursor", "too many tokens")
        assert isinstance(err, AgentResourceLimitError)

    def test_all_subclasses_store_fields(self):
        for cls in (
            AgentTokenLimitError,
            AgentRateLimitError,
            AgentQuotaLimitError,
            AgentProviderRefusalError,
        ):
            err = cls("agent", "msg", retry_after=10)
            assert err.agent_name == "agent"
            assert err.message == "msg"
            assert err.retry_after == 10

    def test_repr(self):
        err = AgentTokenLimitError("claude", "exceeded", retry_after=30)
        r = repr(err)
        assert "AgentTokenLimitError" in r
        assert "claude" in r


class TestClassifyResourceError:
    """Tests for classify_resource_error."""

    def test_token_limit_detected(self):
        err = classify_resource_error("claude", "Error: max tokens exceeded", 1)
        assert isinstance(err, AgentTokenLimitError)

    def test_context_length_detected(self):
        err = classify_resource_error("cursor", "context length limit reached", 1)
        assert isinstance(err, AgentTokenLimitError)

    def test_rate_limit_detected(self):
        err = classify_resource_error("codex", "Error: rate limit exceeded", 1)
        assert isinstance(err, AgentRateLimitError)

    def test_too_many_requests_detected(self):
        err = classify_resource_error("codex", "too many requests", 1)
        assert isinstance(err, AgentRateLimitError)

    def test_http_429_exit_code(self):
        err = classify_resource_error("claude", "some error", 429)
        assert isinstance(err, AgentRateLimitError)

    def test_quota_detected(self):
        err = classify_resource_error("codex", "quota exceeded for org", 1)
        assert isinstance(err, AgentQuotaLimitError)

    def test_billing_detected(self):
        err = classify_resource_error("codex", "billing limit reached", 1)
        assert isinstance(err, AgentQuotaLimitError)

    def test_insufficient_credits(self):
        err = classify_resource_error("codex", "insufficient credits", 1)
        assert isinstance(err, AgentQuotaLimitError)

    def test_provider_refused(self):
        err = classify_resource_error("claude", "request refused by server", 1)
        assert isinstance(err, AgentProviderRefusalError)

    def test_overloaded_detected(self):
        err = classify_resource_error("claude", "server overloaded", 1)
        assert isinstance(err, AgentProviderRefusalError)

    def test_http_503_exit_code(self):
        err = classify_resource_error("codex", "server error", 503)
        assert isinstance(err, AgentProviderRefusalError)

    def test_unrelated_error_returns_none(self):
        err = classify_resource_error("cursor", "file not found", 1)
        assert err is None

    def test_empty_stderr_returns_none(self):
        err = classify_resource_error("cursor", "", 1)
        assert err is None

    def test_retry_after_parsed(self):
        err = classify_resource_error("codex", "rate limit, retry after 120 seconds", 1)
        assert isinstance(err, AgentRateLimitError)
        assert err.retry_after == 120
