"""ccdvf.api.errors: custom exception types for the API layer."""
from __future__ import annotations


class BudgetExceededError(RuntimeError):
    """Raised before an API call would exceed the configured budget cap."""


class MalformedResponseError(RuntimeError):
    """Raised when the API returned a shape the client cannot parse."""
