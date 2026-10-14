# tests/test_structured_outputs_pydantic.py
import pytest
from unittest.mock import MagicMock, patch
import anthropic
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from structured_outputs_pydantic import (
    DeploymentGate,
    DeploymentVerdict,
    VerdictValidationError,
    derive_input_schema,
)
from claude_code.config.model_registry import get_model

haiku_current = get_model("haiku_current")


@pytest.fixture
def mock_anthropic(run_online):
    """
    Conditionally intercepts calls to anthropic.Anthropic based on flags.
    If --online is active, this fixture steps aside completely.
    """
    if run_online:
        yield None
    else:
        with patch("structured_outputs_pydantic.anthropic.Anthropic") as mock_class:
            mock_instance = MagicMock()
            mock_class.return_value = mock_instance
            yield mock_instance


def create_tool_response(input_dict: dict, tool_use_id: str = "toolu_1"):
    """Helper utility to assemble an artificial tool_use response block."""
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_use_id
    block.input = input_dict
    resp = MagicMock()
    resp.content = [block]
    return resp


# =====================================================================
# SCHEMA SHAPE TESTS (no API involved)
# =====================================================================

def test_derived_schema_is_api_shaped():
    """1. No $ref/$defs/title/description noise; additionalProperties and required are correct."""
    schema = derive_input_schema(DeploymentVerdict)
    Draft202012Validator.check_schema(schema)
    assert "$defs" not in schema
    assert "$ref" not in str(schema)
    assert "title" not in schema
    assert "description" not in schema
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"decision", "confidence", "rationale"}


def test_cross_field_rule_passes_jsonschema_but_fails_model_validate():
    """5. jsonschema sees nothing wrong with a rollback that names no blocking check -
    only the model's own validator catches it."""
    schema = derive_input_schema(DeploymentVerdict)
    bad = {"decision": "rollback", "confidence": 0.9, "blocking_checks": [], "rationale": "x"}
    Draft202012Validator(schema).validate(bad)  # no complaint
    with pytest.raises(ValidationError):
        DeploymentVerdict.model_validate(bad)


def test_extra_field_rejected():
    """6. extra='forbid' rejects a hallucinated field the schema never declared."""
    with pytest.raises(ValidationError):
        DeploymentVerdict.model_validate({
            "decision": "promote", "confidence": 0.9, "rationale": "x", "unexpected": True,
        })


# =====================================================================
# STANDARD FUNCTIONALITY TESTS
# =====================================================================

def test_happy_path_single_attempt(mock_anthropic, run_online):
    """2. First forced call validates; no retry needed."""
    if not run_online:
        mock_anthropic.messages.create.return_value = create_tool_response(
            {"decision": "promote", "confidence": 0.95, "blocking_checks": [], "rationale": "all tests pass"}
        )

    gate = DeploymentGate(model_key=haiku_current.id, max_attempts=3)
    verdict = gate.evaluate({"candidate": "v1.0", "test_results": "all green"})

    if not run_online:
        assert verdict.decision == "promote"
        assert gate.attempt_count == 1
        assert gate.validation_failures == 0
        mock_anthropic.messages.create.assert_called_once()
    else:
        assert verdict.decision in ("promote", "hold", "rollback")


# =====================================================================
# ADVANCED VALIDATION-RETRY TESTS (OFFLINE ONLY)
# =====================================================================

def test_validation_failure_retry_recovers(mock_anthropic, run_online):
    """3. Shape failure (confidence out of range) on attempt 1, valid on attempt 2."""
    if run_online:
        pytest.skip("Skipping offline error injection tracking during live execution mode.")

    bad = create_tool_response(
        {"decision": "rollback", "confidence": 1.7, "blocking_checks": ["ci_failed"], "rationale": "bad"}
    )
    good = create_tool_response(
        {"decision": "rollback", "confidence": 0.8, "blocking_checks": ["ci_failed"], "rationale": "ci failed"}
    )
    mock_anthropic.messages.create.side_effect = [bad, good]

    gate = DeploymentGate(model_key=haiku_current.id, max_attempts=3)
    verdict = gate.evaluate({"candidate": "v1.0", "test_results": "ci failed"})

    assert verdict.confidence == 0.8
    assert gate.attempt_count == 2
    assert gate.validation_failures == 1
    assert mock_anthropic.messages.create.call_count == 2


def test_correction_turn_carries_field_path(mock_anthropic, run_online):
    """4. The retry's request carries the offending field name, not a blind repeat."""
    if run_online:
        pytest.skip("Skipping offline error injection tracking during live execution mode.")

    bad = create_tool_response(
        {"decision": "promote", "confidence": 1.7, "blocking_checks": [], "rationale": "x"}
    )
    good = create_tool_response(
        {"decision": "promote", "confidence": 0.8, "blocking_checks": [], "rationale": "x"}
    )
    mock_anthropic.messages.create.side_effect = [bad, good]

    gate = DeploymentGate(model_key=haiku_current.id, max_attempts=3)
    gate.evaluate({"candidate": "v1.0", "test_results": "x"})

    second_call = mock_anthropic.messages.create.call_args_list[1]
    messages_sent = second_call.kwargs["messages"]
    correction_block = messages_sent[-1]["content"][0]
    assert correction_block["type"] == "tool_result"
    assert correction_block["is_error"] is True
    assert "confidence" in correction_block["content"]


def test_retries_exhausted_raises(mock_anthropic, run_online):
    """7. Every attempt invalid -> VerdictValidationError, not a half-valid object."""
    if run_online:
        pytest.skip("Skipping offline error injection tracking during live execution mode.")

    always_bad = create_tool_response(
        {"decision": "promote", "confidence": 2.0, "blocking_checks": [], "rationale": "x"}
    )
    mock_anthropic.messages.create.side_effect = [always_bad, always_bad, always_bad]

    gate = DeploymentGate(model_key=haiku_current.id, max_attempts=3)
    with pytest.raises(VerdictValidationError) as exc_info:
        gate.evaluate({"candidate": "v1.0", "test_results": "x"})

    assert gate.attempt_count == 3
    assert gate.validation_failures == 3
    assert exc_info.value.last_errors


def test_fails_fast_on_bad_request(mock_anthropic, run_online):
    """8. A schema the API itself rejects bubbles up immediately, no retry loop."""
    if run_online:
        pytest.skip("Skipping offline error injection tracking during live execution mode.")

    bad_request_error = anthropic.BadRequestError(
        message="Invalid schema", response=MagicMock(status_code=400), body={}
    )
    mock_anthropic.messages.create.side_effect = bad_request_error

    gate = DeploymentGate(model_key=haiku_current.id, max_attempts=3)
    with pytest.raises(anthropic.BadRequestError):
        gate.evaluate({"candidate": "v1.0", "test_results": "x"})

    assert mock_anthropic.messages.create.call_count == 1
