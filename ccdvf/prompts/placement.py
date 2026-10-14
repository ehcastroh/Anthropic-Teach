"""ccdvf.prompts.placement: helpers for placing trusted instructions vs untrusted content."""
from __future__ import annotations


def place_instructions(system: str, user_content: str) -> tuple[str, str]:
    """Return (system_prompt, user_message) enforcing the instruction-content boundary.

    Trusted developer instructions belong in the system prompt; task content
    (user-supplied or external) belongs in the user message. Keeping them
    separate is the primary defense against prompt injection.

    Args:
        system: Trusted instruction text for the system parameter.
        user_content: Task content for the user turn (may be untrusted).

    Returns:
        Tuple of (system_prompt, user_message) ready for client.messages.create.
    """
    return system.strip(), user_content.strip()
