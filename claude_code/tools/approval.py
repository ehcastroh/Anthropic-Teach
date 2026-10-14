"""claude_code.tools.approval: Decision-matrix helpers for tool approval patterns."""
from __future__ import annotations

_SWEET_SPOT = 5
_SPLIT_THRESHOLD = 7


def recommend_approval_pattern(
    tool_names: list[str],
    sensitive_ops: list[str],
) -> dict:
    """Return the recommended approval pattern for a given tool set.

    Three shapes:
    - 'auto-approve': no sensitive operations; all tools are read-only or low-risk.
      SDK permission mode: 'bypassPermissions'.
    - 'human-in-the-loop': at least one sensitive operation requires explicit approval.
      SDK permission mode: 'plan'.
    - When total tool count exceeds 7, also flags a 'split-into-subagents' recommendation
      regardless of the primary pattern.

    Preconditions:
        - tool_names is a list of strings (may be empty).
        - sensitive_ops is a list of tool names that perform sensitive or irreversible
          operations (subset of tool_names).

    Postconditions:
        - Returns a dict with keys: pattern (str), rationale (str), mode (str).
        - If len(tool_names) > 7, the dict also contains
          'also_consider': 'split-into-subagents'.

    Raises:
        ValueError: If any name in sensitive_ops is not in tool_names.

    # TODO: verify permission mode names ('bypassPermissions', 'plan') against
    # https://docs.anthropic.com/en/docs/claude-code/settings on next review
    """
    unknown = [s for s in sensitive_ops if s not in tool_names]
    if unknown:
        raise ValueError(f"sensitive_ops contains names not in tool_names: {unknown!r}")

    if sensitive_ops:
        result: dict = {
            "pattern": "human-in-the-loop",
            "rationale": (
                f"Tools {sensitive_ops!r} perform sensitive or irreversible operations. "
                "Require explicit human approval before execution."
            ),
            "mode": "plan",
        }
    else:
        result = {
            "pattern": "auto-approve",
            "rationale": (
                "No sensitive operations detected. All tools are read-only or low-risk; "
                "auto-approval maximises throughput."
            ),
            "mode": "bypassPermissions",
        }

    if len(tool_names) > _SPLIT_THRESHOLD:
        result["also_consider"] = "split-into-subagents"

    return result


def max_tools_per_role() -> int:
    """Return the recommended maximum tool count per agent role (5).

    Postconditions:
        - Returns 5.

    The reliability-degradation threshold is ~8 tools; 3-5 is the documented
    sweet spot for reliable tool selection.
    # TODO: verify '3-5 tool sweet spot' claim against Anthropic engineering blog
    """
    return _SWEET_SPOT
