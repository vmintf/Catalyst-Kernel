# Catalyst Kernel - A bare-metal UEFI kernel using Python DSL and Zig AOT compilation.
# Copyright (C) 2026  Skystarry.xyz
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# See LICENSE for details.

"""Set the working directory to the repo root before the test session.

opcodes.py opens TOML files via relative paths ("./frontend/toml/..."),
so tests must run from the repository root.  This conftest handles that
automatically when pytest is invoked from any directory.
"""
import os
import pathlib

_REPO_ROOT = pathlib.Path(__file__).parent.parent
os.chdir(_REPO_ROOT)
