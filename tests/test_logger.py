"""Tests for the structured application logger."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.infrastructure.logger import (
    MorchLogFilter,
    get_logger,
)


class TestMorchLogFilter:
    """Tests for the log filter that injects defaults."""

    def test_injects_agent_when_missing(self):
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "msg", (), None,
        )
        f = MorchLogFilter()
        assert f.filter(record) is True
        assert record.agent == "-"
        assert record.phase == "-"

    def test_preserves_existing_agent(self):
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "msg", (), None,
        )
        record.agent = "cursor"
        record.phase = "implement"
        f = MorchLogFilter()
        f.filter(record)
        assert record.agent == "cursor"
        assert record.phase == "implement"


class TestGetLogger:
    """Tests for get_logger."""

    def test_returns_logger_with_orchestrator_prefix(self):
        log = get_logger("test_module")
        assert log.name == "orchestrator.test_module"

    def test_already_prefixed_name_unchanged(self):
        log = get_logger("orchestrator.cli")
        assert log.name == "orchestrator.cli"

    def test_logger_can_log_without_error(self):
        log = get_logger("test_basic")
        log.info("test message")
        log.warning("test warning")

    def test_logger_with_extra_fields(self):
        log = get_logger("test_extra")
        log.info(
            "test with extra",
            extra={"agent": "cursor", "phase": "review"},
        )
