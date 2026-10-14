"""ccdvf.api.retry: exponential backoff with jitter for Anthropic API calls."""
from __future__ import annotations

import random
import time
from collections.abc import Callable

import anthropic

from ccdvf.config.settings import load

# Status codes that indicate a transient failure and should be retried.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def with_retry[T](fn: Callable[[], T], *, max_attempts: int | None = None) -> T:
    """Call fn, retrying on transient Anthropic errors with exponential backoff + jitter.

    Preconditions:
        - fn is a zero-argument callable that either returns a value or raises anthropic.APIError.
    Postconditions:
        - Returns fn() on the first success.
        - Raises the last exception after max_attempts failures.
        - Never retries on 400, 401, 403 (client errors).

    Raises:
        anthropic.APIStatusError: after all retries exhausted, or immediately for non-retryable status.
        anthropic.APIConnectionError: after all retries exhausted.
    """
    settings = load()
    attempts = max_attempts if max_attempts is not None else settings.retry_max_attempts
    base = settings.retry_backoff_base_seconds

    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except anthropic.APIStatusError as exc:
            if exc.status_code not in _RETRYABLE_STATUS:
                raise
            last_exc = exc
        except anthropic.APIConnectionError as exc:
            last_exc = exc

        if attempt < attempts - 1:
            delay = base * (2 ** attempt) + random.uniform(0, 0.1)
            time.sleep(delay)

    raise last_exc  # type: ignore[misc]
