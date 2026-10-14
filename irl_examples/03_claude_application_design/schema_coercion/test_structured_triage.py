"""Tests for structured_triage.evaluate_deployment.

Offline mode (default): mocks the Anthropic client; no API key required.
Online mode (--online): calls the live API; requires ANTHROPIC_API_KEY.

conftest.py in this directory adds the schema_coercion/ directory
to sys.path so `structured_triage` is importable by name.

Usage:
    # Offline (mocked client, no API key needed)
    pytest study/irl_examples/03_claude_application_design/schema_coercion/ -v

    # Online (live API, loads ANTHROPIC_API_KEY from environment)
    pytest study/irl_examples/03_claude_application_design/schema_coercion/ -v --online
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

# conftest.py adds this directory to sys.path
from structured_triage import (  # type: ignore[import]
    MAX_RETRIES,
    TOOL_NAME,
    DeploymentGateDecision,
    evaluate_deployment,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_client_single(tool_input: dict[str, Any]) -> MagicMock:
    """Return a mock Anthropic client that emits tool_input as a tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = TOOL_NAME
    block.input = tool_input

    msg = MagicMock()
    msg.content = [block]

    client = MagicMock()
    client.messages.create.return_value = msg
    return client


def _mock_client_sequence(*inputs: dict[str, Any]) -> MagicMock:
    """Return a mock client that cycles through the given inputs across successive calls."""
    responses = []
    for tool_input in inputs:
        block = MagicMock()
        block.type = "tool_use"
        block.name = TOOL_NAME
        block.input = tool_input

        msg = MagicMock()
        msg.content = [block]
        responses.append(msg)

    client = MagicMock()
    client.messages.create.side_effect = responses
    return client


def _mock_client_no_tool() -> MagicMock:
    """Return a mock client whose response contains no tool_use block."""
    text_block = MagicMock()
    text_block.type = "text"

    msg = MagicMock()
    msg.content = [text_block]

    client = MagicMock()
    client.messages.create.return_value = msg
    return client


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_allow_decision_returns_typed_object(self) -> None:
        valid_input = {
            "gate_decision": "allow",
            "risk_score": 0.05,
            "blocking_reasons": [],
            "summary": "Version bump with no breaking API changes.",
        }
        client = _mock_client_single(valid_input)
        decision = evaluate_deployment("Bumped requests 2.28->2.31", client=client)

        assert isinstance(decision, DeploymentGateDecision)
        assert decision.gate_decision == "allow"
        assert decision.risk_score == pytest.approx(0.05)
        assert decision.blocking_reasons == []
        assert len(decision.summary) > 0

    def test_block_decision_has_reasons(self) -> None:
        block_input = {
            "gate_decision": "block",
            "risk_score": 0.97,
            "blocking_reasons": ["Drops database migration rollback path"],
            "summary": "Hard stop - rollback support removed.",
        }
        client = _mock_client_single(block_input)
        decision = evaluate_deployment("Removed migration down() method", client=client)

        assert decision.gate_decision == "block"
        assert decision.risk_score >= 0.5
        assert len(decision.blocking_reasons) >= 1

    def test_require_review_decision(self) -> None:
        review_input = {
            "gate_decision": "require_review",
            "risk_score": 0.55,
            "blocking_reasons": ["Auth flow modified - manual test needed"],
            "summary": "Authentication change requires human sign-off.",
        }
        client = _mock_client_single(review_input)
        decision = evaluate_deployment("Refactored OAuth callback handler", client=client)

        assert decision.gate_decision == "require_review"


# ---------------------------------------------------------------------------
# Validation failure + retry
# ---------------------------------------------------------------------------


class TestValidationRetry:
    def test_bad_risk_score_retries_then_succeeds(self) -> None:
        """First response exceeds risk_score upper bound; second is valid."""
        bad_input = {
            "gate_decision": "require_review",
            "risk_score": 1.5,  # violates Field(le=1.0)
            "blocking_reasons": ["unclear scope"],
            "summary": "Needs review.",
        }
        good_input = {
            "gate_decision": "require_review",
            "risk_score": 0.6,
            "blocking_reasons": ["unclear scope"],
            "summary": "Scope unclear; route to reviewer.",
        }
        client = _mock_client_sequence(bad_input, good_input)
        decision = evaluate_deployment("Changed feature-flag evaluation logic", client=client)

        assert decision.gate_decision == "require_review"
        assert 0.0 <= decision.risk_score <= 1.0
        assert client.messages.create.call_count == 2

    def test_invalid_gate_decision_literal_retries(self) -> None:
        """Model produces an unsupported verdict string; retry fixes it."""
        bad_input = {
            "gate_decision": "maybe",  # not in Literal
            "risk_score": 0.4,
            "blocking_reasons": [],
            "summary": "Not sure.",
        }
        good_input = {
            "gate_decision": "require_review",
            "risk_score": 0.4,
            "blocking_reasons": [],
            "summary": "Sending for review given ambiguity.",
        }
        client = _mock_client_sequence(bad_input, good_input)
        decision = evaluate_deployment("Partial config change", client=client)

        assert decision.gate_decision in ("allow", "block", "require_review")

    def test_all_retries_exhausted_raises_runtime_error(self) -> None:
        """Every attempt returns an invalid risk_score; RuntimeError is raised."""
        always_bad = {
            "gate_decision": "allow",
            "risk_score": -0.5,  # violates Field(ge=0.0)
            "blocking_reasons": [],
            "summary": "ok",
        }
        client = _mock_client_sequence(*([always_bad] * MAX_RETRIES))

        with pytest.raises(RuntimeError, match="validation failed after"):
            evaluate_deployment("Some change", client=client)

        assert client.messages.create.call_count == MAX_RETRIES

    def test_corrective_suffix_included_in_reprompt(self) -> None:
        """After a ValidationError, the next API call includes a correction note."""
        bad_input = {
            "gate_decision": "allow",
            "risk_score": 9.9,  # violates le=1.0
            "blocking_reasons": [],
            "summary": "fine",
        }
        good_input = {
            "gate_decision": "allow",
            "risk_score": 0.1,
            "blocking_reasons": [],
            "summary": "Low-risk change.",
        }
        client = _mock_client_sequence(bad_input, good_input)
        evaluate_deployment("Minor config update", client=client)

        second_call_args = client.messages.create.call_args_list[1]
        user_content = second_call_args[1]["messages"][0]["content"]
        assert "correction" in user_content.lower() or "invalid" in user_content.lower()


# ---------------------------------------------------------------------------
# No tool_use block
# ---------------------------------------------------------------------------


class TestNoToolBlock:
    def test_missing_tool_block_retries(self) -> None:
        """When the response has no tool_use block, the call is retried."""
        good_input = {
            "gate_decision": "allow",
            "risk_score": 0.1,
            "blocking_reasons": [],
            "summary": "Safe.",
        }
        no_tool_resp = MagicMock()
        no_tool_resp.content = [MagicMock(type="text")]

        good_block = MagicMock()
        good_block.type = "tool_use"
        good_block.name = TOOL_NAME
        good_block.input = good_input
        good_resp = MagicMock()
        good_resp.content = [good_block]

        client = MagicMock()
        client.messages.create.side_effect = [no_tool_resp, good_resp]

        decision = evaluate_deployment("Config change", client=client)
        assert isinstance(decision, DeploymentGateDecision)
        assert client.messages.create.call_count == 2


# ---------------------------------------------------------------------------
# Online tests
# ---------------------------------------------------------------------------


class TestOnline:
    def test_live_api_returns_valid_decision(self, online: bool) -> None:
        """Live API call: verify the full coercion pipeline end-to-end."""
        if not online:
            pytest.skip("Pass --online to run live API tests")

        import anthropic as _anthropic

        from ccdvf.security.secrets import load_api_key

        live_client = _anthropic.Anthropic(api_key=load_api_key())
        decision = evaluate_deployment(
            "Upgraded pydantic from 1.10 to 2.5; updated all model definitions "
            "and added strict mode to all validators. No schema changes.",
            client=live_client,
        )

        assert isinstance(decision, DeploymentGateDecision)
        assert decision.gate_decision in ("allow", "block", "require_review")
        assert 0.0 <= decision.risk_score <= 1.0
        assert isinstance(decision.blocking_reasons, list)
        assert isinstance(decision.summary, str) and len(decision.summary) > 0
