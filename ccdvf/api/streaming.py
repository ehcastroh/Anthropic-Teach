"""ccdvf.api.streaming: streaming Messages API wrapper.

Verified against platform.claude.com/docs/en/docs/build-with-claude/streaming on 2026-09-02.
"""
from __future__ import annotations

from collections.abc import Iterator

import anthropic

from ccdvf.api.budget import BudgetTracker
from ccdvf.config.model_registry import cost_for_call, get_model
from ccdvf.config.settings import load
from ccdvf.security.secrets import load_api_key


class StreamingClient:
    """Streaming Messages API wrapper.

    Owns:
        - stream() context manager over anthropic.Anthropic().messages.stream.
        - Per-chunk yield via text_stream.
        - Post-stream usage / cost accounting via the final message.

    Does NOT own:
        - Non-streaming calls (see MessagesClient).

    Preconditions:
        - ANTHROPIC_API_KEY must be set in env.
        - model must be a key in ccdvf.config.model_registry.MODELS.
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
        self._last_call_usd: float = 0.0
        self._last_call_tokens: tuple[int, int] = (0, 0)

    def __enter__(self) -> StreamingClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._client.close()

    @property
    def last_call_usd(self) -> float:
        """Cost of the most recently completed stream, in USD."""
        return self._last_call_usd

    @property
    def last_call_tokens(self) -> tuple[int, int]:
        """(input_tokens, output_tokens) from the most recently completed stream."""
        return self._last_call_tokens

    def stream(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> Iterator[str]:
        """Open a streaming call and yield text chunks as they arrive.

        Preconditions:
            - prompt is a non-empty string.
            - max_tokens > 0.
        Postconditions:
            - Yields str chunks in order; the underlying HTTP connection closes on iterator exhaustion
              or on exception exit from the enclosing with-block.
            - Sets last_call_usd and last_call_tokens after the stream ends.
        Raises:
            anthropic.APIStatusError: on API errors during streaming.
        """
        kwargs: dict = dict(
            model=self._model_spec.id,  # Verified against platform.claude.com/docs/en/api/messages on 2026-09-02
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if system is not None:
            kwargs["system"] = system

        with self._client.messages.stream(**kwargs) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()

        usage = final.usage
        in_tok = usage.input_tokens
        out_tok = usage.output_tokens
        self._last_call_tokens = (in_tok, out_tok)
        self._last_call_usd = cost_for_call(
            self._model_key,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
        self._budget.record(input_tokens=in_tok, output_tokens=out_tok)
