"""Deployment gate decision via Pydantic schema coercion.

Production scenario: an automated deployment-gate service asks Claude to
analyze a change summary and decide whether to allow, block, or route the
change for human review. The response must be typed and immediately actionable -
no free-text parsing by the caller.

Key pattern: Pydantic's BaseModel defines the contract once, in Python.
model_json_schema() derives the JSON Schema automatically. That schema becomes
the tool's input_schema. tool_choice forces Claude to populate it. The caller
inflates the result with Model(**tool_block.input) and catches ValidationError
as the re-prompt signal.
"""

from __future__ import annotations

import logging
from typing import Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError

from ccdvf.security.secrets import load_api_key

logger = logging.getLogger(__name__)

MODEL_ID = "claude-haiku-4-5-20251001"
MAX_RETRIES = 3
TOOL_NAME = "emit_gate_decision"


class DeploymentGateDecision(BaseModel):
    """Structured output for a deployment gate evaluation.

    Attributes:
        gate_decision: Routing decision - allow, block, or require a human review pass.
        risk_score: Estimated risk level in [0.0, 1.0]; 0.0 is no risk, 1.0 is critical.
        blocking_reasons: List of reasons if blocking or escalating; empty list if allowing.
        summary: One-to-two sentence plain-English rationale for the decision.
    """

    gate_decision: Literal["allow", "block", "require_review"]
    risk_score: float = Field(ge=0.0, le=1.0)
    blocking_reasons: list[str]
    summary: str


_GATE_TOOL: dict = {
    "name": TOOL_NAME,
    "description": (
        "Emit a structured deployment gate decision. "
        "Set gate_decision to 'allow' when changes are safe to deploy, "
        "'block' for hard stops (data loss risk, broken migrations, security holes), "
        "or 'require_review' when human judgment is needed before proceeding. "
        "risk_score must be between 0.0 and 1.0. "
        "blocking_reasons must be an empty list when gate_decision is 'allow'."
    ),
    "input_schema": DeploymentGateDecision.model_json_schema(),
}

_SYSTEM_PROMPT = (
    "You are a deployment gate assistant. Analyze the change summary and emit a structured "
    "gate decision. Be conservative: when the risk is ambiguous, prefer require_review over allow."
)


def evaluate_deployment(
    change_summary: str,
    client: anthropic.Anthropic | None = None,
) -> DeploymentGateDecision:
    """Evaluate a deployment change summary and return a typed gate decision.

    Calls Claude with forced tool use so the response is validated against
    DeploymentGateDecision's schema by the API. Re-prompts up to MAX_RETRIES
    times on pydantic.ValidationError; raises RuntimeError on exhaustion.

    Preconditions:
      - change_summary is a non-empty string.
      - client, if provided, is an authenticated anthropic.Anthropic instance.

    Postconditions:
      - Returns a fully validated DeploymentGateDecision.
      - Raises RuntimeError if all MAX_RETRIES validation attempts fail.
      - Raises anthropic.APIError on network or API-level failures (not retried here).
    """
    assert change_summary, "change_summary must not be empty"

    if client is None:
        client = anthropic.Anthropic(api_key=load_api_key())

    last_error: ValidationError | None = None
    corrective_suffix = ""

    for attempt in range(1, MAX_RETRIES + 1):
        user_content = change_summary
        if corrective_suffix:
            user_content = (
                f"{change_summary}\n\n"
                f"[Previous response was invalid - correction required: {corrective_suffix}]"
            )

        logger.debug("Gate evaluation attempt %d/%d", attempt, MAX_RETRIES)

        resp = client.messages.create(
            model=MODEL_ID,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            tools=[_GATE_TOOL],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{"role": "user", "content": user_content}],
        )

        tool_block = next(
            (b for b in resp.content if b.type == "tool_use"),
            None,
        )
        if tool_block is None:
            logger.warning("No tool_use block returned (attempt %d); re-prompting", attempt)
            corrective_suffix = "you must emit a decision via the emit_gate_decision tool"
            continue

        try:
            decision = DeploymentGateDecision(**tool_block.input)
            logger.debug(
                "Gate decision: %s (risk=%.2f, reasons=%d)",
                decision.gate_decision,
                decision.risk_score,
                len(decision.blocking_reasons),
            )
            return decision

        except ValidationError as exc:
            last_error = exc
            first_err = exc.errors()[0]
            field_path = ".".join(str(x) for x in first_err["loc"])
            corrective_suffix = f"field '{field_path}' is invalid: {first_err['msg']}"
            logger.warning(
                "ValidationError on attempt %d/%d: %s",
                attempt, MAX_RETRIES, corrective_suffix,
            )

    raise RuntimeError(
        f"DeploymentGateDecision validation failed after {MAX_RETRIES} attempts. "
        f"Last pydantic error: {last_error}"
    )
