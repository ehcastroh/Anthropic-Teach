"""Secret loading from the environment.

Every notebook setup cell imports load_api_key from here.
API keys must never be written into notebook cells or committed to git.
"""

import os


class MissingSecretError(RuntimeError):
    """The named environment variable is unset or empty.

    Raised by load_api_key when the variable is required but absent.
    """


def load_api_key(name: str = "ANTHROPIC_API_KEY", *, allow_empty: bool = False) -> str:
    """Load a required secret from the environment.

    Reads the named environment variable. If the variable is unset or empty
    and allow_empty is False, raises MissingSecretError with a message that
    names the variable and points to the README setup section.

    Preconditions:
      - name is a valid environment variable name string.

    Postconditions:
      - Returns the environment variable value as a non-empty string
        when allow_empty is False.
      - Returns "" when allow_empty is True and the variable is unset or empty.

    Raises:
      - MissingSecretError: variable is unset or empty and allow_empty is False.
    """
    value = os.environ.get(name, "")
    if not value and not allow_empty:
        raise MissingSecretError(
            f"Environment variable '{name}' is not set or is empty. "
            "Set it before running this notebook (see README - Setup section)."
        )
    return value
