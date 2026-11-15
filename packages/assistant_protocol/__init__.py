# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Process-free protocol primitives and execution-disabled admission audit."""
from .preflight import ExecutionPreflight, initialize

__all__ = ['ExecutionPreflight', 'initialize']
