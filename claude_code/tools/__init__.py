"""claude_code.tools: function schema builders, tool dispatcher, and tool_choice helpers."""

from claude_code.tools.approval import max_tools_per_role, recommend_approval_pattern
from claude_code.tools.dispatcher import Dispatcher
from claude_code.tools.schema import Tool
from claude_code.tools.tool_choice import (
    tool_choice_any,
    tool_choice_auto,
    tool_choice_none,
    tool_choice_specific,
)

__all__ = [
    "Dispatcher",
    "Tool",
    "max_tools_per_role",
    "recommend_approval_pattern",
    "tool_choice_any",
    "tool_choice_auto",
    "tool_choice_none",
    "tool_choice_specific",
]
