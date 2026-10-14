"""Locate the repository root, so notebooks can use root-relative paths.

Every data path in this repo is written relative to the repository root
(`fixtures/...`, `assets/...`, `claude_code/config/prompts`). A Jupyter kernel's
working directory is the *notebook's own* directory, so once the notebooks moved
into `lessons/`, `homework/`, and `scenarios/`, every one of those paths began
resolving against the wrong directory - `lessons/fixtures/...` does not exist.

Nothing about this fails loudly at import time; it surfaces much later as a
FileNotFoundError in the middle of a lesson. Calling `use_repo_root()` in a
notebook's setup cell makes root-relative paths mean what they say, whichever
directory the notebook lives in.
"""

from __future__ import annotations

import os
from pathlib import Path

# The file that marks the repository root. Chosen because it is guaranteed to
# exist exactly once, at the top: it is what makes this repo a Python project.
_ROOT_MARKER = "pyproject.toml"


def repo_root() -> Path:
    """Absolute path to the repository root.

    Resolves from this module's own location first, which is correct regardless
    of the caller's working directory (the project is installed in editable
    mode, so this file lives inside the repo). Falls back to walking up from the
    working directory if the layout is ever unexpected.
    """
    candidate = Path(__file__).resolve().parent.parent
    if (candidate / _ROOT_MARKER).is_file():
        return candidate

    probe = Path.cwd().resolve()
    for directory in (probe, *probe.parents):
        if (directory / _ROOT_MARKER).is_file():
            return directory

    raise RuntimeError(
        f"Could not locate the repository root: no {_ROOT_MARKER} found from "
        f"{Path(__file__).resolve()} or {probe}."
    )


def use_repo_root() -> Path:
    """Change the working directory to the repository root and return it.

    Safe to call more than once and from any directory, so re-running a setup
    cell is harmless.
    """
    root = repo_root()
    if Path.cwd().resolve() != root:
        os.chdir(root)
    return root
