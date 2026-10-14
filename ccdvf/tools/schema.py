"""ccdvf.tools.schema: Tool dataclass and JSON Schema helpers."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import jsonschema


@dataclass(frozen=True, slots=True)
class Tool:
    """A callable exposed to Claude via function calling.

    Attributes:
        name: exact function name Claude sees in tool_use blocks.
        description: prose the model reads to decide when to call this tool.
        input_schema: JSON Schema dict describing the tool's input shape.
        handler: sync callable taking **input_dict and returning str (or JSON-serializable value).
    """

    name: str
    description: str
    input_schema: dict
    handler: Callable[..., str]

    def to_api_dict(self) -> dict:
        """Serialize as the Anthropic API tool block: {name, description, input_schema}.

        Verified against platform.claude.com/docs/en/docs/tool-use on 2026-09-02.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def validate_input(self, input_dict: dict) -> None:
        """Raise jsonschema.ValidationError if input_dict does not match input_schema.

        Preconditions: input_dict is a dict.
        Postconditions: returns None if valid; raises ValidationError otherwise.
        """
        jsonschema.validate(instance=input_dict, schema=self.input_schema)
