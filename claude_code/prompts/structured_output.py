"""claude_code.prompts.structured_output: validator for Claude JSON text output."""
from __future__ import annotations

import json

import jsonschema


class StructuredOutputValidator:
    """Parse and validate Claude's text response as structured JSON.

    Two structured-output strategies exist: prompt-guidance (this class) and
    tool_choice=specific (see claude_code.tools.tool_choice). Use this class when
    you want to parse text output; use tool_choice_specific when you need
    a guaranteed schema enforced by the API.

    Args:
        schema: JSON Schema dict that the parsed output must satisfy.

    Example:
        >>> v = StructuredOutputValidator({"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]})
        >>> v.validate('{"n": 42}')
        {'n': 42}
    """

    def __init__(self, schema: dict) -> None:
        self._schema = schema

    def validate(self, text: str) -> dict:
        """Parse text as JSON and validate against the schema.

        Args:
            text: Raw text from a Claude response expected to contain JSON.

        Returns:
            Parsed dict if valid.

        Raises:
            ValueError: If text is not valid JSON.
            jsonschema.ValidationError: If the parsed object fails schema validation.
        """
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValueError(f"Response is not valid JSON: {e}") from e
        jsonschema.validate(instance=data, schema=self._schema)
        return data
