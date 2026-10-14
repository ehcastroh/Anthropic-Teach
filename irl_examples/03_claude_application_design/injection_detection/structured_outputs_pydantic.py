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
