# Structured Outputs with Pydantic

## The Metaphor: The Customs Declaration

Think of your pydantic model as the customs declaration form a shipper must fill out, and `model_json_schema()` as printing a blank copy of that form for the shipper to see before they pack the crate. The form states what fields exist and what each one may contain - a weight in kilograms, a country code, a declared value. That is the derived `input_schema`: an advertisement of shape, handed to Claude before it ever calls the tool.

`model_validate()` is the customs inspector who opens the crate on arrival. The inspector checks the same fields the form asked for - but the inspector can also apply a rule the form itself has no box for: *a shipment declared as "hazardous" must list at least one containment measure*. No printed form has a checkbox that compares two other checkboxes against each other. Only the inspector, standing there with the actual crate, can enforce that. That is the cross-field rule a `model_validator` catches and a JSON Schema dict cannot.

And if the shipper writes "500 grams of gold" on a crate that actually contains lead, both the form and the inspector wave it through - the declaration is well-formed, so nothing here catches a lie. Catching that requires someone who can weigh the crate against outside knowledge, which is a different job entirely.

## The Biggest Takeaway

> A derived schema is the advertisement of your model's shape. The model itself is the contract.

Only the model's own validator can enforce a rule that spans two fields, and only printing `model_json_schema()` before you ship it catches what pydantic's defaults quietly changed on the way out - an `enum.Enum` field that serialized as an unresolved `$ref`, a `title` key nobody asked for, a defaulted field that silently dropped out of `required`. None of that is visible until you look.

> **Note:** The following code is for reference. All executable files are in `irl_examples/03_claude_application_design/injection_detection/`.
>
> Jump to [Part 5](#part-5-script-execution-routes) for instructions on how to use scripts.

---

## Deriving and Enforcing Structured Outputs: A Validation-Retry Guide

This guide walks through a release-gate tool that derives its `input_schema` from a pydantic model instead of hand-writing a parallel JSON Schema dict, forces Claude to call it with `tool_choice`, and retries a `ValidationError` by feeding the model the specific correction rather than repeating the same request. It is the companion to `untrusted_boundary_tests.py` and `injection_detection.md` in this same directory: those cover the *input* boundary (untrusted text arriving), while this covers the *output* boundary (a structured decision leaving, and what happens when it comes back malformed).

---

### Part 1: The Three-Step Environment Setup

#### Step 1: Create a Clean Sandbox Directory

Open your terminal and create a dedicated folder to safely isolate your dependencies and code assets.
```bash
mkdir -p structured_outputs_sandbox/tests
cd structured_outputs_sandbox
```

#### Step 2: Set Up and Activate a Python Virtual Environment

Isolate your Python runtime environment to avoid conflicts with global system packages.
```bash
python3 -m venv .venv
source .venv/bin/activate
```

#### Step 3: Install Required Dependencies

Install the Anthropic client, pydantic, jsonschema, and pytest.
```bash
pip install --upgrade pip
pip install anthropic pydantic jsonschema pytest
```

---

### Part 2: Core Implementation (DeploymentGate)

Save the following code inside a file named **`structured_outputs_pydantic.py`**. It defines a `DeploymentVerdict` model, a `derive_input_schema()` function that turns the model into an API-ready tool schema, and a `DeploymentGate` that forces the tool call and retries on `ValidationError`.

```python
# structured_outputs_pydantic.py
"""A deployment gate that derives its tool schema from a pydantic model and
retries on ValidationError with corrective feedback, not with a sleep-and-repeat
loop. See triager.py in ../../02_software_engineering_foundations/integration_mocking/ for the network-failure retry
this is deliberately not: that one retries the same request after a backoff
because nothing about the request was wrong. This one retries with new
information in the prompt, because what failed was the content, not the
connection.
"""
from __future__ import annotations

import json
from typing import Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_TOOL_NAME = "emit_verdict"


class VerdictValidationError(Exception):
    """Raised when every attempt up to max_attempts fails validation.

    Carries the last ValidationError's errors() list so a caller can see what
    was wrong with the final attempt, not just that retries ran out.
    """

    def __init__(self, message: str, last_errors: list[dict]) -> None:
        super().__init__(message)
        self.last_errors = last_errors


class DeploymentVerdict(BaseModel):
    """A release-gate decision.

    Combines a shape jsonschema can enforce (decision/confidence/blocking_checks
    as fields) with a cross-field rule no JSON Schema keyword can express: a
    non-promote decision must name at least one blocking check.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["promote", "hold", "rollback"] = Field(
        description="Release verdict for this candidate build."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in the verdict, 0 to 1.")
    blocking_checks: list[str] = Field(
        default_factory=list,
        description="Checks that must pass before a non-promote verdict clears.",
    )
    rationale: str = Field(max_length=400, description="One or two sentences explaining the verdict.")

    @model_validator(mode="after")
    def _non_promote_needs_blocking_check(self) -> DeploymentVerdict:
        if self.decision != "promote" and not self.blocking_checks:
            raise ValueError("a non-promote verdict must name at least one blocking check")
        return self


def derive_input_schema(model: type[BaseModel]) -> dict:
    """Turn model_json_schema() into an API-ready input_schema.

    Args:
        model: A pydantic model with every enum/nested model inlined - no field
            may itself be a BaseModel or enum.Enum, since both serialize as a
            $ref into a $defs block the API would otherwise have to resolve.

    Returns:
        The schema with pydantic's 'title' noise stripped from every node, and
        the top-level 'description' dropped - pydantic populates that from the
        class docstring, which duplicates the Tool dict's own description field
        and leaks Python documentation onto the wire.

    Raises:
        ValueError: if $ref/$defs reached the wire. Catching that here, not at
            the API boundary, is the point of this function.
    """
    schema = model.model_json_schema()
    if "$defs" in schema or "$ref" in json.dumps(schema):
        raise ValueError("schema still contains $ref/$defs - inline every enum/nested model first")

    def _strip_title(node: object) -> None:
        if isinstance(node, dict):
            node.pop("title", None)
            for value in node.values():
                _strip_title(value)
        elif isinstance(node, list):
            for item in node:
                _strip_title(item)

    _strip_title(schema)
    schema.pop("description", None)
    return schema


class DeploymentGate:
    """Forces Claude to emit a schema-validated DeploymentVerdict.

    Retries on ValidationError by feeding the specific field-level correction
    back as a tool_result, bounded by max_attempts. Does not retry (and does
    not sleep) on anthropic.BadRequestError/AuthenticationError - a schema the
    API itself rejects is a developer bug, not something a correction turn
    fixes, so it is left to propagate.
    """

    def __init__(self, model_key: str, max_attempts: int = 3) -> None:
        self.model_key = model_key
        self.max_attempts = max_attempts
        self.attempt_count = 0
        self.validation_failures = 0
        self.client = anthropic.Anthropic()
        self._tool = {
            "name": _TOOL_NAME,
            "description": "Emit the final deployment verdict for this release candidate.",
            "input_schema": derive_input_schema(DeploymentVerdict),
        }

    def evaluate(self, release: dict) -> DeploymentVerdict:
        """Force a verdict tool call for `release`, retrying on ValidationError.

        Args:
            release: Describes the candidate build, e.g.
                {"candidate": "v2.4.1", "test_results": "3 of 5 smoke tests failed"}.

        Returns:
            A validated DeploymentVerdict.

        Raises:
            VerdictValidationError: every attempt up to max_attempts failed validation.
            anthropic.APIStatusError: the API itself rejected the request.
        """
        messages: list[dict] = [{
            "role": "user",
            "content": (
                f"Release candidate: {release.get('candidate', 'unknown')}\n"
                f"Test results: {release.get('test_results', 'no results provided')}\n"
                "Emit a deployment verdict."
            ),
        }]

        last_errors: list[dict] = []
        for attempt in range(1, self.max_attempts + 1):
            self.attempt_count = attempt
            resp = self.client.messages.create(
                model=self.model_key,
                max_tokens=256,
                tools=[self._tool],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=messages,
            )
            tool_block = next(b for b in resp.content if b.type == "tool_use")

            try:
                return DeploymentVerdict.model_validate(tool_block.input)
            except ValidationError as e:
                self.validation_failures += 1
                last_errors = e.errors(include_url=False)
                messages.append({"role": "assistant", "content": resp.content})
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": json.dumps(last_errors, default=str),
                        "is_error": True,
                    }],
                })

        raise VerdictValidationError(
            f"deployment verdict failed validation after {self.max_attempts} attempts",
            last_errors,
        )
```

---

### Part 3: Recommended Defense Against Failure Guideline

A `ValidationError` is not one failure class - it is where several distinct problems happen to surface at the same call site. Treating all of them the same way (sleep and repeat, or give up immediately) is wrong for every one of them. Partition by what actually broke:

```
[model_validate() Result]
│
├──► Network/transport failure (429, 5xx, connection drop) ──► [TRANSIENT]   ──► same request, exponential backoff (see triager.py)
│
├──► Field-shape violation (range, enum, missing key) ────────► [SHAPE]       ──► jsonschema and model_validate agree; retry with errors()
│
├──► Cross-field rule violation (model_validator) ─────────────► [CROSS-FIELD] ──► jsonschema silent; only the model catches it; retry with errors()
│
└──► Claim is well-formed but false ────────────────────────────► [OUT OF SCOPE] ──► not a retry problem at all; a retrieval/grounding problem
```

| Failure | Caught by | Retry strategy |
|:---|:---|:---|
| 429 / 503 | the transport layer | same request, exponential backoff (`triager.py` in `../../02_software_engineering_foundations/integration_mocking/`) |
| Out-of-range / wrong enum / extra key | derived schema + `model_validate` | new turn carrying `errors()` |
| Cross-field rule violated | `model_validator` only | new turn carrying `errors()` |
| Claim is well-formed but false | nothing in this stack | not a retry problem |

**Why the last row matters at a trust boundary.** `e.errors()`'s default `input` field echoes back whatever produced the failure - including untrusted text, if the payload came from a document rather than the model itself. Replaying `input` verbatim into the next prompt turn can re-inject whatever was in it. A correction payload fed back into a conversation should carry only `loc`, `msg`, and `type` - never `input` - exactly as `safe_correction()` does in Homework 3's Q6.

**Why `DeploymentGate.evaluate` does not sleep.** The transient-failure row is the only one with a clock in it. A `ValidationError` means the content of the next request has to change, not that the same request needs to be tried again later - so there is no `time.sleep` anywhere in the retry loop, and the only thing bounding it is `max_attempts`.

---

### Part 4: Advanced Test Suite (Hybrid Offline/Online Routing)

#### Step 1: Create `conftest.py`

Save this file next to `structured_outputs_pydantic.py` to add the `--online` flag. `pytest_addoption` is only honored in an *initial* conftest, so run pytest from this directory - a `conftest.py` nested under some other invocation point would never see this file.

```python
# tests/conftest.py
import pytest

def pytest_addoption(parser):
    parser.addoption(
        "--online",
        action="store_true",
        default=False,
        help="Run tests using live, billable Anthropic API network transactions"
    )

@pytest.fixture
def run_online(request):
    return request.config.getoption("--online")
```

#### Step 2: Create `test_structured_outputs_pydantic.py`

Save the complete test suite below next to the module. It mirrors `test_triager.py`'s offline/online routing, adapted to force `tool_use` responses instead of plain text ones.

```python
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
```

---

### Part 5: Script Execution Routes

#### Activate/Deactivate Virtual Environment

```bash
# create sandbox
source .venv/bin/activate
```

```bash
# Exit the virtual environment safely
deactivate

# Obliterate the virtual environment directory completely from your disk
rm -rf .venv/
```

#### Route A: Standard Fast Verification (Offline & Free)

```bash
# Run from inside this directory so conftest.py is an initial conftest
cd irl_examples/03_claude_application_design/injection_detection

# Run all tests with standard progress output
pytest

# Run with the Verbose (-v) flag to see the exact execution status of each unique test case
pytest -v

# Run only a specified test case from the file
pytest -k "test_cross_field_rule_passes_jsonschema_but_fails_model_validate" -v
```

#### Route B: Live API Verification (Billable Integration Routing)

```bash
# Export your live API credential token to the active shell context
export ANTHROPIC_API_KEY=$(python3 -c 'from claude_code.security.secrets import load_api_key; print(load_api_key("ANTHROPIC_API_KEY"))')

# Verify the environment key is loaded
echo $ANTHROPIC_API_KEY # displays API Key
[ -n "$ANTHROPIC_API_KEY" ] && echo "✅ Key is safely loaded." || echo "❌ Key is missing."

# Force pytest to skip mocks and hit the live endpoint
cd irl_examples/03_claude_application_design/injection_detection && pytest -v --online

# Target one specific test case to run against the live Claude server
cd irl_examples/03_claude_application_design/injection_detection && pytest -k "test_happy_path_single_attempt" -v --online
```

#### Useful Diagnostic & Debugging Flags

```bash
# Show local variable print() updates directly on the terminal screen during test execution
pytest -s

# Drop directly into a live interactive PDB debugging terminal window if a test failure occurs
pytest --pdb
```
