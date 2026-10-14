"""ccdvf.tools.dispatcher: registry and dispatch layer for tool_use blocks."""
from __future__ import annotations

from ccdvf.tools.schema import Tool


class Dispatcher:
    """Registry and dispatch layer for tool_use blocks emitted by the model.

    Owns:
        - Registering Tool instances by name.
        - Validating tool_use inputs against schema BEFORE calling the handler.
        - Constructing tool_result blocks matched by tool_use_id.

    Does NOT own:
        - Model call (see ccdvf.api.MessagesClient).
        - Loop control (see ccdvf.agents.AgentLoop).
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a Tool by its name.

        Preconditions: tool.name is a non-empty string.
        Postconditions: tool is retrievable by dispatch_one.
        """
        self._tools[tool.name] = tool

    def to_api_list(self) -> list[dict]:
        """Serialize all registered tools as the API 'tools' parameter.

        Postconditions: returns a list of dicts in Anthropic API tool block format.
        """
        return [t.to_api_dict() for t in self._tools.values()]

    def dispatch_one(self, tool_use_block: dict) -> dict:
        """Run one tool_use block and return the corresponding tool_result block.

        Preconditions:
            - tool_use_block has keys: type == 'tool_use', name, input, id.
            - A Tool with matching name is registered.
            - tool_use_block['input'] validates against the tool's input_schema.
        Postconditions:
            - Returns {'type': 'tool_result', 'tool_use_id': <id>, 'content': <handler output as str>}.
        Raises:
            KeyError: no tool registered with that name.
            jsonschema.ValidationError: input does not match schema.
        """
        name = tool_use_block["name"]
        if name not in self._tools:
            raise KeyError(f"No tool registered with name {name!r}")

        tool = self._tools[name]
        input_dict = tool_use_block.get("input", {})
        tool.validate_input(input_dict)

        result = tool.handler(**input_dict)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_block["id"],
            "content": str(result),
        }
