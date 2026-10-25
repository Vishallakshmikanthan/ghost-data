"""Ghost Data Still Exists - Engineering Foundation Package.

Provides core packages for deletion-audit architecture while preserving
compatibility with standard library tools (e.g. pdb, pytest) that import 'code'.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Safe bridge for standard-library 'code' symbols if needed by pdb/pytest
try:
    for _p in sys.path:
        if _p and ("Python" in _p or "lib" in _p.lower()) and not _p.endswith("ghost-data"):
            _cand = Path(_p) / "code.py"
            if _cand.exists():
                _spec = importlib.util.spec_from_file_location("_stdlib_code", str(_cand))
                if _spec and _spec.loader:
                    _mod = importlib.util.module_from_spec(_spec)
                    _spec.loader.exec_module(_mod)
                    InteractiveConsole = getattr(_mod, "InteractiveConsole", None)
                    InteractiveInterpreter = getattr(_mod, "InteractiveInterpreter", None)
                    compile_command = getattr(_mod, "compile_command", None)
                    interact = getattr(_mod, "interact", None)
                    break
except Exception:
    pass
