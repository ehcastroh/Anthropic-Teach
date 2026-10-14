"""claude_code.api.batches: Message Batches API wrapper.

Verified against platform.claude.com/docs/en/api/creating-message-batches on 2026-09-02.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import anthropic

from claude_code.config.model_registry import MODELS, get_model
from claude_code.security.secrets import load_api_key


@dataclass(frozen=True, slots=True)
class BatchRequest:
    """One request to submit in a batch.

    Attributes:
        custom_id: unique identifier per batch (caller-assigned).
        messages: list of {role, content} dicts.
        model: model_registry key or raw Anthropic model ID.
        max_tokens: maximum tokens to generate.
        system: optional system prompt.
    """

    custom_id: str
    messages: list[dict]
    model: str = "haiku_current"
    max_tokens: int = 1024
    system: str | None = None

    def _model_id(self) -> str:
        if self.model in MODELS:
            return get_model(self.model).id
        return self.model

    def to_api_dict(self) -> dict:
        """Serialize as the API 'requests' list item shape."""
        params: dict = dict(
            model=self._model_id(),  # Verified against platform.claude.com/docs/en/api/creating-message-batches on 2026-09-02
            max_tokens=self.max_tokens,
            messages=self.messages,
        )
        if self.system is not None:
            params["system"] = self.system
        return {"custom_id": self.custom_id, "params": params}


@dataclass(frozen=True, slots=True)
class BatchHandle:
    """Status snapshot of a submitted batch.

    Attributes:
        id: Anthropic-issued batch ID.
        status: "in_progress" | "canceling" | "ended".
        request_counts: dict with keys "processing", "succeeded", "errored", "canceled", "expired".
    """

    id: str
    status: str
    request_counts: dict


def _handle_from_response(resp) -> BatchHandle:
    counts = {}
    if hasattr(resp, "request_counts"):
        rc = resp.request_counts
        for field in ("processing", "succeeded", "errored", "canceled", "expired"):
            counts[field] = getattr(rc, field, 0)
    return BatchHandle(id=resp.id, status=resp.processing_status, request_counts=counts)


class BatchesClient:
    """Message Batches API wrapper.

    Owns:
        - submit(requests) -> BatchHandle
        - retrieve(batch_id) -> BatchHandle
        - poll_until_complete(batch_id, timeout_seconds=None) -> BatchHandle
        - results(batch_id) -> Iterator[dict]

    Batches API supports up to a 24-hour processing window at approximately half the
    per-token cost of realtime calls.

    Preconditions:
        - ANTHROPIC_API_KEY must be set in env.
    """

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=load_api_key())

    def submit(self, requests: list[BatchRequest]) -> BatchHandle:
        """Submit a batch of requests. Returns a BatchHandle with status 'in_progress'.

        Preconditions:
            - requests is a non-empty list of BatchRequest.
            - All custom_id values within requests are unique.
        Postconditions:
            - Returns a BatchHandle with the Anthropic-issued batch ID.
        Raises:
            ValueError: if any two requests share the same custom_id.
            anthropic.APIStatusError: on API errors.
        """
        ids = [r.custom_id for r in requests]
        if len(ids) != len(set(ids)):
            raise ValueError("All BatchRequest custom_id values must be unique within a batch")

        resp = self._client.messages.batches.create(  # Verified against platform.claude.com/docs/en/api/creating-message-batches on 2026-09-02
            requests=[r.to_api_dict() for r in requests]
        )
        return _handle_from_response(resp)

    def retrieve(self, batch_id: str) -> BatchHandle:
        """Retrieve the current status of a batch by its Anthropic-issued ID.

        Preconditions: batch_id is a non-empty string returned by a prior submit call.
        Raises:
            anthropic.APIStatusError: on API errors.
        """
        resp = self._client.messages.batches.retrieve(batch_id)  # Verified against platform.claude.com/docs/en/api/creating-message-batches on 2026-09-02
        return _handle_from_response(resp)

    def poll_until_complete(
        self,
        batch_id: str,
        *,
        timeout_seconds: int | None = None,
        poll_interval_seconds: int = 30,
    ) -> BatchHandle:
        """Poll retrieve() until status == 'ended', then return the handle.

        Preconditions:
            - batch_id is a valid Anthropic batch ID.
            - poll_interval_seconds > 0.
        Postconditions:
            - Returns a BatchHandle with status 'ended'.
        Raises:
            TimeoutError: if timeout_seconds elapses while the batch is still 'in_progress'.
            anthropic.APIStatusError: on API errors.
        """
        start = time.monotonic()
        while True:
            handle = self.retrieve(batch_id)
            if handle.status == "ended":
                return handle
            if timeout_seconds is not None and (time.monotonic() - start) >= timeout_seconds:
                raise TimeoutError(
                    f"Batch {batch_id!r} did not complete within {timeout_seconds}s. "
                    f"Current status: {handle.status!r}"
                )
            time.sleep(poll_interval_seconds)

    def results(self, batch_id: str) -> Iterator[dict]:
        """Yield one result dict per request in the completed batch.

        Preconditions: batch_id refers to an 'ended' batch.
        Postconditions: yields dicts with keys 'custom_id' and 'result'.
        Raises:
            anthropic.APIStatusError: if the batch has not ended or does not exist.
        """
        for item in self._client.messages.batches.results(batch_id):  # Verified against platform.claude.com/docs/en/api/creating-message-batches on 2026-09-02
            yield {"custom_id": item.custom_id, "result": item.result}
