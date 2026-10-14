"""ccdvf.agents.loop: simple multi-turn agent loop with optional tool-result pruning."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentLoop:
    """Manage message history for a multi-turn agent interaction.

    Attributes:
        prune_tool_results: When True, drop old tool_use/tool_result pairs
            once history exceeds max_turns_before_prune entries.
        max_turns_before_prune: Threshold for pruning (number of messages).
    """

    prune_tool_results: bool = False
    max_turns_before_prune: int = 20
    _messages: list[dict] = field(default_factory=list, repr=False)

    def add(self, role: str, content: Any) -> None:
        """Append a turn to the message history.

        Args:
            role: "user" or "assistant".
            content: The content value (str or list of content blocks).
        """
        self._messages.append({"role": role, "content": content})
        if self.prune_tool_results:
            self._maybe_prune()

    @property
    def messages(self) -> list[dict]:
        """Return a copy of the current message history."""
        return list(self._messages)

    def token_estimate(self) -> int:
        """Return a rough token estimate (4 chars ~= 1 token) for the current history."""
        total_chars = sum(len(str(m["content"])) for m in self._messages)
        return total_chars // 4

    def _maybe_prune(self) -> None:
        if len(self._messages) <= self.max_turns_before_prune:
            return
        keep_from = max(1, len(self._messages) - self.max_turns_before_prune)
        self._messages = self._messages[:1] + self._messages[keep_from:]
