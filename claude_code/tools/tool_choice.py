"""claude_code.tools.tool_choice: helpers that construct the API tool_choice parameter."""
from __future__ import annotations


def tool_choice_auto(disable_parallel_tool_use: bool = False) -> dict:
    """Return the 'auto' tool_choice block.

    Verified against platform.claude.com/docs/en/docs/tool-use on 2026-09-02.
    """
    d: dict = {"type": "auto"}
    if disable_parallel_tool_use:
        d["disable_parallel_tool_use"] = True
    return d


def tool_choice_specific(tool_name: str, disable_parallel_tool_use: bool = False) -> dict:
    """Return a tool_choice block that forces the model to call the named tool."""
    d: dict = {"type": "tool", "name": tool_name}
    if disable_parallel_tool_use:
        d["disable_parallel_tool_use"] = True
    return d


def tool_choice_any(disable_parallel_tool_use: bool = False) -> dict:
    """Return the 'any' tool_choice block (forces at least one tool call)."""
    d: dict = {"type": "any"}
    if disable_parallel_tool_use:
        d["disable_parallel_tool_use"] = True
    return d


def tool_choice_none() -> dict:
    """Return the 'none' tool_choice block (prevents any tool call)."""
    return {"type": "none"}
