"""
Platform-specific parts. All this module does is pick the right backend.

To add a system, write the matching module and extend the check below -
the layers above stay unchanged.
"""
from __future__ import annotations

import platform

_system = platform.system()

if _system == "Windows":
    from . import win_input as inp  # noqa: F401
else:  # pragma: no cover - only Windows is fully supported for now
    inp = None  # type: ignore[assignment]


def input_backend():
    """The input module. Fails with a clear message when missing."""
    if inp is None:
        raise RuntimeError(
            f"the input backend for {_system} has not been written yet "
            "(Windows is supported for now)"
        )
    return inp


def system_name() -> str:
    return _system
