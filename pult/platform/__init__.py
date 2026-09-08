"""
Tizimga bog'liq qismlar. Bu yerda faqat to'g'ri backend tanlanadi.

Yangi tizim qo'shish uchun mos modul yozib, quyidagi shartga qo'shish
kifoya - yuqori qatlamlar o'zgarmaydi.
"""
from __future__ import annotations

import platform

_system = platform.system()

if _system == "Windows":
    from . import win_input as inp  # noqa: F401
else:  # pragma: no cover - hozircha faqat Windows to'liq qo'llab-quvvatlanadi
    inp = None  # type: ignore[assignment]


def input_backend():
    """Kiritish moduli. Yo'q bo'lsa tushunarli xato beradi."""
    if inp is None:
        raise RuntimeError(
            f"{_system} uchun kiritish moduli hali yozilmagan "
            "(hozircha Windows qo'llab-quvvatlanadi)"
        )
    return inp


def system_name() -> str:
    return _system
