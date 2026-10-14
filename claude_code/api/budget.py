"""claude_code.api.budget: per-session cost tracking and cap enforcement."""
from __future__ import annotations

from dataclasses import dataclass, field

from claude_code.config.model_registry import cost_for_call


@dataclass
class BudgetTracker:
    """Track cumulative API spend and enforce an optional hard cap.

    Preconditions:
        - cap_usd > 0 when provided.
        - model_key must be a key in claude_code.config.model_registry.MODELS.
    Postconditions:
        - would_exceed returns True iff adding projected_* tokens would push spent_usd over cap_usd.
        - record accumulates costs exactly once per successful call.
    """

    cap_usd: float
    model_key: str
    spent_usd: float = field(default=0.0)
    call_count: int = field(default=0)

    def would_exceed(
        self,
        projected_input_tokens: int,
        projected_output_tokens: int,
    ) -> bool:
        """Return True if adding these tokens would exceed cap_usd.

        Preconditions: projected_* >= 0.
        Postconditions: does not mutate self.
        """
        projected = cost_for_call(
            self.model_key,
            input_tokens=projected_input_tokens,
            output_tokens=projected_output_tokens,
        )
        return (self.spent_usd + projected) > self.cap_usd

    def record(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> None:
        """Accumulate cost for one completed call and increment call_count.

        Preconditions: called AFTER a successful API response.
        Postconditions: spent_usd increases by the exact cost of this call; call_count += 1.
        """
        self.spent_usd += cost_for_call(
            self.model_key,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_read_tokens=cache_read_tokens,
        )
        self.call_count += 1
