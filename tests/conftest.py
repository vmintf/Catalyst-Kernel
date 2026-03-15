"""Set the working directory to the repo root before the test session.

opcodes.py opens TOML files via relative paths ("./frontend/toml/..."),
so tests must run from the repository root.  This conftest handles that
automatically when pytest is invoked from any directory.
"""
import os
import pathlib

_REPO_ROOT = pathlib.Path(__file__).parent.parent
os.chdir(_REPO_ROOT)
