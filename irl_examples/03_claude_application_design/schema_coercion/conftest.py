"""Make `structured_triage` importable by name from within this directory.

This directory has no __init__.py and is not a Python package.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
