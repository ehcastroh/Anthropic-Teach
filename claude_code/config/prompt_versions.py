"""Prompt versioning: load prompts from versioned files in claude_code/config/prompts/.

Prompts live in claude_code/config/prompts/{name}/vN.txt.
latest.txt is a plain-text copy of the current version (no symlinks - cross-platform).

Treating prompts as versioned files means a prompt change is a git-diffable code change:
you roll back by overwriting latest.txt with the prior version, and your eval harness
runs vN vs vM on the same fixtures to measure the delta.
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def get_prompt(name: str, version: str = "latest") -> str:
    """Load a versioned prompt by name.

    Preconditions:
      - name is a non-empty string matching a subdirectory under claude_code/config/prompts/.
      - version is "latest" or a version tag like "v1", "v2".

    Postconditions:
      - Returns the stripped text content of the prompt file.

    Raises:
      - FileNotFoundError: prompt directory or version file does not exist.
    """
    prompt_dir = _PROMPTS_DIR / name
    if not prompt_dir.is_dir():
        raise FileNotFoundError(f"No prompt directory: {prompt_dir}")

    file_path = prompt_dir / f"{version}.txt"
    if not file_path.exists():
        raise FileNotFoundError(f"No prompt file: {file_path}")

    return file_path.read_text(encoding="utf-8").strip()


def list_versions(name: str) -> list[str]:
    """List available numeric versions for a prompt, sorted ascending.

    Postconditions:
      - Returns sorted list of version tags (e.g., ["v1", "v2"]).
      - Excludes "latest" from results.

    Raises:
      - FileNotFoundError: prompt directory does not exist.
    """
    prompt_dir = _PROMPTS_DIR / name
    if not prompt_dir.is_dir():
        raise FileNotFoundError(f"No prompt directory: {prompt_dir}")

    return [p.stem for p in sorted(prompt_dir.glob("v*.txt"))]


def current_version(name: str) -> str:
    """Return the version tag that latest.txt matches.

    Resolves by comparing latest content to each vN.txt by text equality.

    Preconditions:
      - name is a valid prompt name.
      - latest.txt exists.

    Postconditions:
      - Returns the matching version tag (e.g., "v2").
      - Returns "unknown" if no vN.txt matches latest.txt exactly.

    Raises:
      - FileNotFoundError: prompt directory or latest.txt does not exist.
    """
    latest_text = get_prompt(name, "latest")
    for version in list_versions(name):
        if get_prompt(name, version) == latest_text:
            return version
    return "unknown"


def promote(name: str, version: str) -> None:
    """Make version the current latest for a prompt.

    Overwrites latest.txt with the content of vN.txt.

    Preconditions:
      - name and version are valid (vN.txt exists).

    Postconditions:
      - latest.txt content equals vN.txt content.

    Raises:
      - FileNotFoundError: prompt directory or vN.txt does not exist.
    """
    content = get_prompt(name, version)
    latest_path = _PROMPTS_DIR / name / "latest.txt"
    latest_path.write_text(content + "\n", encoding="utf-8")
