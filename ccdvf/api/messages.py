"""ccdvf.api.messages: synchronous, non-streaming Messages API wrapper.

Verified against platform.claude.com/docs/en/api/messages on 2026-09-02.
"""
from __future__ import annotations

from dataclasses import dataclass

import anthropic

from ccdvf.api.budget import BudgetTracker
from ccdvf.api.errors import BudgetExceededError, MalformedResponseError
from ccdvf.api.retry import with_retry
from ccdvf.config.model_registry import cost_for_call, get_model
from ccdvf.config.settings import load
from ccdvf.security.secrets import load_api_key


@dataclass(frozen=True, slots=True)
class CallResult:
    """Result of a single synchronous Messages API call.

    Attributes:
        text: concatenation of all text blocks in response.content.
        content: raw content block list as dicts (text + tool_use).
        stop_reason: "end_turn" | "tool_use" | "max_tokens" | "stop_sequence".
        input_tokens: prompt tokens billed.
        output_tokens: completion tokens billed.
        cache_write_tokens: tokens written to prompt cache (5-min TTL).
        cache_read_tokens: tokens read from prompt cache.
        cost_usd: exact cost of this call per model_registry pricing.
        model_id: raw Anthropic model ID used.
    """

    text: str
    content: list[dict]
    stop_reason: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    cost_usd: float
    model_id: str


def _content_to_dicts(content: list) -> list[dict]:
    """Convert SDK content block objects to plain dicts."""
    result = []
    for block in content:
        if hasattr(block, "model_dump"):
            result.append(block.model_dump())
        elif hasattr(block, "__dict__"):
            result.append(vars(block))
        else:
            result.append(dict(block))
    return result


def _extract_text(content: list) -> str:
    parts = []
    for block in content:
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if btype == "text":
            text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else "")
            if text:
                parts.append(text)
    return "".join(parts)


class MessagesClient:
    """Synchronous non-streaming Messages API wrapper with retry, cost tracking, budget cap.

    Owns:
        - Messages API request/response cycle.
        - Retry on 429, 5xx, network errors (up to Settings.retry_max_attempts).
        - Per-call cost accounting via BudgetTracker.

    Does NOT own:
        - Streaming (see StreamingClient).
        - Batches API (see BatchesClient).

    Preconditions:
        - ANTHROPIC_API_KEY must be set in env OR a client is passed.
        - model must be a key in ccdvf.config.model_registry.MODELS.
        - budget_usd > 0 when provided; None means uncapped.
    """

    def __init__(
        self,
        model: str = "haiku_current",
        budget_usd: float | None = None,
    ) -> None:
        settings = load()
        self._model_key = model
        self._model_spec = get_model(model)
        self._client = anthropic.Anthropic(api_key=load_api_key())
        cap = budget_usd if budget_usd is not None else settings.max_budget_usd
        self._budget = BudgetTracker(cap_usd=cap, model_key=model)

    def __enter__(self) -> MessagesClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._client.close()

    @property
    def budget(self) -> BudgetTracker:
        return self._budget

    def call(
        self,
        messages: list[dict],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        cache_control: dict | None = None,
        extended_thinking: dict | None = None,
    ) -> CallResult:
        """Submit one synchronous Messages API request and return a CallResult.

        Preconditions:
            - messages is a non-empty list of {role, content} dicts.
            - max_tokens > 0.
        Postconditions:
            - Raises BudgetExceededError BEFORE the API is contacted when projected cost exceeds cap.
            - Updates self._budget AFTER a successful call.
        Raises:
            BudgetExceededError: projected call would exceed budget.
            MalformedResponseError: response content is empty or missing expected fields.
            anthropic.APIStatusError: non-retryable API error (400, 401, 403).
        """
        # Budget pre-check (rough estimate based on max_tokens)
        if self._budget.would_exceed(
            projected_input_tokens=sum(len(str(m)) // 4 for m in messages),
            projected_output_tokens=max_tokens,
        ):
            raise BudgetExceededError(
                f"Projected call would exceed budget cap of ${self._budget.cap_usd:.4f} USD. "
                f"Spent so far: ${self._budget.spent_usd:.4f} USD."
            )

        kwargs: dict = dict(
            model=self._model_spec.id,
            max_tokens=max_tokens,
            messages=messages,
        )
        if system is not None:
            kwargs["system"] = system
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if extended_thinking is not None:
            kwargs["thinking"] = extended_thinking

        def _call():
            return self._client.messages.create(**kwargs)  # Verified against platform.claude.com/docs/en/api/messages on 2026-09-02

        resp = with_retry(_call)

        if not resp.content:
            raise MalformedResponseError("API response has empty content list")

        usage = resp.usage
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0

        cost = cost_for_call(
            self._model_key,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=cw,
            cache_read_tokens=cr,
        )

        self._budget.record(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=cw,
            cache_read_tokens=cr,
        )

        return CallResult(
            text=_extract_text(resp.content),
            content=_content_to_dicts(resp.content),
            stop_reason=resp.stop_reason or "end_turn",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=cw,
            cache_read_tokens=cr,
            cost_usd=cost,
            model_id=resp.model,
        )
