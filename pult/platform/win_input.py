"""
Windows kiritish backend'i - SendInput orqali.

Tashqi kutubxona ishlatilmaydi (pyautogui va boshqalar shart emas): ctypes
bilan to'g'ridan-to'g'ri user32.SendInput chaqiriladi. Bu eng tez va eng
ishonchli yo'l - skan-kodlar bilan yuborilgani uchun o'yinlar va past
darajali kiritishni kutadigan dasturlar ham qabul qiladi.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Iterable, Sequence

user32 = ctypes.WinDLL("user32", use_last_error=True)


def _enable_dpi_awareness() -> None:
    """DPI masshtablangan ekranlarda koordinatalar to'g'ri bo'lishi uchun.

    Buni ekran o'lchamlari so'ralishidan OLDIN chaqirish shart, aks holda
    Windows bizga masshtablangan (soxta) o'lchamlarni qaytaradi va barcha
    bosishlar siljib ketadi.
    """
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()

ULONG_PTR = wintypes.WPARAM

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

WHEEL_DELTA = 120

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def _send(events: Sequence[INPUT]) -> None:
    """Hodisalarni bitta chaqiruvda yuboradi.

    Bitta SendInput ichida yuborilgan hodisalar atomar: orasiga foydalanuvchining
    haqiqiy sichqonchasi kirib ketolmaydi. Shuning uchun bosish (down+up) va
    kombinatsiya (ctrl+c) doim bitta ro'yxat bo'lib yuboriladi.
    """
    if not events:
        return
    n = len(events)
    arr = (INPUT * n)(*events)
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        err = ctypes.get_last_error()
        if err == 5:  # ERROR_ACCESS_DENIED
            # Windows'ning UIPI himoyasi: administrator huquqi bilan
            # ochilgan oyna faol bo'lsa, oddiy huquqli dastur unga
            # kiritish yubora olmaydi. Kursor ekranda harakatlanadi,
            # lekin bosishlar o'sha oynaga yetib bormaydi.
            raise PermissionError(
                "Faol oyna administrator huquqi bilan ishlayapti - Windows "
                "unga kiritishga ruxsat bermaydi. Boshqa oynani tanlang yoki "
                "Pult'ni ham administrator sifatida ishga tushiring."
            )
        raise ctypes.WinError(err)


def _mouse(dx: int, dy: int, data: int, flags: int) -> INPUT:
    ev = INPUT(type=INPUT_MOUSE)
    ev.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)
    return ev


def _key(scan: int, flags: int, vk: int = 0) -> INPUT:
    ev = INPUT(type=INPUT_KEYBOARD)
    ev.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return ev


# --------------------------------------------------------------------------
# Ekranlar
# --------------------------------------------------------------------------


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM
)

MONITORINFOF_PRIMARY = 0x00000001


def list_monitors() -> list[dict]:
    """Ekranlar ro'yxati, chapdan o'ngga tartiblangan.

    Tartib ataylab chapdan-o'ngga: telefonda "1-ekran / 2-ekran" ko'rsatilganda
    bu jismoniy joylashuvga mos tushadi. ddagrab'ning output_idx raqami odatda
    shu tartibga to'g'ri keladi, lekin kafolat yo'q - shuning uchun sozlamalarda
    uni qo'lda almashtirish mumkin.
    """
    found: list[dict] = []

    def _cb(hmon, hdc, lprect, lparam):  # noqa: ANN001 - WinAPI qayta chaqiruvi
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found.append(
                {
                    "device": info.szDevice,
                    "x": r.left,
                    "y": r.top,
                    "w": r.right - r.left,
                    "h": r.bottom - r.top,
                    "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
                }
            )
        return True

    user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    found.sort(key=lambda m: (m["x"], m["y"]))
    for i, m in enumerate(found):
        m["index"] = i
    return found


def virtual_screen() -> tuple[int, int, int, int]:
    """Barcha ekranlarni qamrab oluvchi to'rtburchak: (x, y, kenglik, balandlik)."""
    g = user32.GetSystemMetrics
    return (
        g(SM_XVIRTUALSCREEN),
        g(SM_YVIRTUALSCREEN),
        g(SM_CXVIRTUALSCREEN),
        g(SM_CYVIRTUALSCREEN),
    )


# --------------------------------------------------------------------------
# Sichqoncha
# --------------------------------------------------------------------------

_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
    "back": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1),
    "forward": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2),
}


def _abs_event(x: int, y: int) -> INPUT:
    """Mutlaq koordinatani 0..65535 oralig'iga o'tkazadi.

    VIRTUALDESK bayrog'i bilan koordinatalar butun virtual ish stoliga nisbatan
    hisoblanadi - shuning uchun ikkinchi monitorga ham to'g'ri tushadi.
    """
    vx, vy, vw, vh = virtual_screen()
    nx = int(round((x - vx) * 65535.0 / max(vw - 1, 1)))
    ny = int(round((y - vy) * 65535.0 / max(vh - 1, 1)))
    nx = max(0, min(65535, nx))
    ny = max(0, min(65535, ny))
    return _mouse(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)


def move_to(x: int, y: int) -> None:
    _send([_abs_event(x, y)])


def move_by_raw(dx: int, dy: int) -> None:
    """Windows'ning o'z nisbiy harakati.

    Diqqat: bunga tizimning kursor tezligi va tezlashuvi qo'llanadi, ya'ni
    so'ralgan 40 piksel amalda 28 ham bo'lishi mumkin va har kompyuterda
    boshqacha chiqadi. Trackpad uchun move_by() ishlating.
    """
    _send([_mouse(int(dx), int(dy), 0, MOUSEEVENTF_MOVE)])


def move_by(dx: float, dy: float) -> None:
    """Aniq nisbiy harakat: kursorni o'qib, mutlaq qilib qaytib qo'yamiz.

    Tizimning tezlashuvini butunlay chetlab o'tadi, shuning uchun telefondagi
    barmoq harakati har kompyuterda bir xil masofa beradi. Sezgirlikni
    boshqarishni yuqori qatlamga qoldiramiz.
    """
    x, y = cursor_pos()
    vx, vy, vw, vh = virtual_screen()
    nx = max(vx, min(vx + vw - 1, int(round(x + dx))))
    ny = max(vy, min(vy + vh - 1, int(round(y + dy))))
    move_to(nx, ny)


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def button(action: str, name: str = "left", x: int | None = None, y: int | None = None) -> None:
    """action: 'down' | 'up' | 'click'. x,y berilsa avval o'sha nuqtaga boradi."""
    down, up, data = _BUTTON_FLAGS[name]
    events: list[INPUT] = []
    if x is not None and y is not None:
        events.append(_abs_event(x, y))
    if action in ("down", "click"):
        events.append(_mouse(0, 0, data, down))
    if action in ("up", "click"):
        events.append(_mouse(0, 0, data, up))
    _send(events)


def scroll(dy: float = 0, dx: float = 0) -> None:
    """dy > 0 - yuqoriga. Birlik: bitta "tirqish" = 120."""
    events: list[INPUT] = []
    if dy:
        events.append(_mouse(0, 0, int(dy * WHEEL_DELTA), MOUSEEVENTF_WHEEL))
    if dx:
        events.append(_mouse(0, 0, int(dx * WHEEL_DELTA), MOUSEEVENTF_HWHEEL))
    _send(events)


# --------------------------------------------------------------------------
# Klaviatura
# --------------------------------------------------------------------------

# Brauzerdagi KeyboardEvent.code -> (skan-kod, kengaytirilganmi).
# Skan-kod jismoniy tugma o'rnini bildiradi, shuning uchun kompyuterdagi
# klaviatura tili (uz/ru/en) qanday bo'lishidan qat'i nazar bir xil ishlaydi.
SCANCODES: dict[str, tuple[int, bool]] = {
    "Escape": (0x01, False),
    "Digit1": (0x02, False), "Digit2": (0x03, False), "Digit3": (0x04, False),
    "Digit4": (0x05, False), "Digit5": (0x06, False), "Digit6": (0x07, False),
    "Digit7": (0x08, False), "Digit8": (0x09, False), "Digit9": (0x0A, False),
    "Digit0": (0x0B, False),
    "Minus": (0x0C, False), "Equal": (0x0D, False), "Backspace": (0x0E, False),
    "Tab": (0x0F, False),
    "KeyQ": (0x10, False), "KeyW": (0x11, False), "KeyE": (0x12, False),
    "KeyR": (0x13, False), "KeyT": (0x14, False), "KeyY": (0x15, False),
    "KeyU": (0x16, False), "KeyI": (0x17, False), "KeyO": (0x18, False),
    "KeyP": (0x19, False),
    "BracketLeft": (0x1A, False), "BracketRight": (0x1B, False), "Enter": (0x1C, False),
    "ControlLeft": (0x1D, False),
    "KeyA": (0x1E, False), "KeyS": (0x1F, False), "KeyD": (0x20, False),
    "KeyF": (0x21, False), "KeyG": (0x22, False), "KeyH": (0x23, False),
    "KeyJ": (0x24, False), "KeyK": (0x25, False), "KeyL": (0x26, False),
    "Semicolon": (0x27, False), "Quote": (0x28, False), "Backquote": (0x29, False),
    "ShiftLeft": (0x2A, False), "Backslash": (0x2B, False),
    "KeyZ": (0x2C, False), "KeyX": (0x2D, False), "KeyC": (0x2E, False),
    "KeyV": (0x2F, False), "KeyB": (0x30, False), "KeyN": (0x31, False),
    "KeyM": (0x32, False),
    "Comma": (0x33, False), "Period": (0x34, False), "Slash": (0x35, False),
    "ShiftRight": (0x36, False),
    "NumpadMultiply": (0x37, False),
    "AltLeft": (0x38, False), "Space": (0x39, False), "CapsLock": (0x3A, False),
    "F1": (0x3B, False), "F2": (0x3C, False), "F3": (0x3D, False), "F4": (0x3E, False),
    "F5": (0x3F, False), "F6": (0x40, False), "F7": (0x41, False), "F8": (0x42, False),
    "F9": (0x43, False), "F10": (0x44, False),
    "NumLock": (0x45, False), "ScrollLock": (0x46, False),
    "Numpad7": (0x47, False), "Numpad8": (0x48, False), "Numpad9": (0x49, False),
    "NumpadSubtract": (0x4A, False),
    "Numpad4": (0x4B, False), "Numpad5": (0x4C, False), "Numpad6": (0x4D, False),
    "NumpadAdd": (0x4E, False),
    "Numpad1": (0x4F, False), "Numpad2": (0x50, False), "Numpad3": (0x51, False),
    "Numpad0": (0x52, False), "NumpadDecimal": (0x53, False),
    "IntlBackslash": (0x56, False),
    "F11": (0x57, False), "F12": (0x58, False),
    # Kengaytirilgan (E0 prefiksli) klavishlar
    "NumpadEnter": (0x1C, True),
    "ControlRight": (0x1D, True),
    "NumpadDivide": (0x35, True),
    "PrintScreen": (0x37, True),
    "AltRight": (0x38, True),
    "Home": (0x47, True),
    "ArrowUp": (0x48, True),
    "PageUp": (0x49, True),
    "ArrowLeft": (0x4B, True),
    "ArrowRight": (0x4D, True),
    "End": (0x4F, True),
    "ArrowDown": (0x50, True),
    "PageDown": (0x51, True),
    "Insert": (0x52, True),
    "Delete": (0x53, True),
    "MetaLeft": (0x5B, True),
    "MetaRight": (0x5C, True),
    "ContextMenu": (0x5D, True),
    # Media klavishlari - klaviaturada bo'lmasa ham tizim ularni qabul qiladi
    "AudioVolumeMute": (0x20, True),
    "AudioVolumeDown": (0x2E, True),
    "AudioVolumeUp": (0x30, True),
    "MediaPlayPause": (0x22, True),
    "MediaStop": (0x24, True),
    "MediaTrackNext": (0x19, True),
    "MediaTrackPrevious": (0x10, True),
    "LaunchMail": (0x6C, True),
    "BrowserHome": (0x32, True),
    "BrowserBack": (0x6A, True),
    "BrowserForward": (0x69, True),
}

# Qisqa nomlar - tugmalar paneli va AI agent shulardan foydalanadi.
ALIASES = {
    "ctrl": "ControlLeft", "control": "ControlLeft",
    "shift": "ShiftLeft", "alt": "AltLeft",
    "win": "MetaLeft", "meta": "MetaLeft", "cmd": "MetaLeft",
    "esc": "Escape", "enter": "Enter", "return": "Enter",
    "tab": "Tab", "space": "Space", "backspace": "Backspace", "del": "Delete",
    "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
    "pgup": "PageUp", "pgdn": "PageDown",
    "volup": "AudioVolumeUp", "voldown": "AudioVolumeDown", "mute": "AudioVolumeMute",
    "playpause": "MediaPlayPause", "next": "MediaTrackNext", "prev": "MediaTrackPrevious",
}


def resolve_key(name: str) -> tuple[int, bool]:
    code = ALIASES.get(name.lower(), name)
    if code not in SCANCODES and len(name) == 1:
        # bitta belgi berilgan bo'lsa: "a" -> "KeyA", "5" -> "Digit5"
        if name.isalpha():
            code = "Key" + name.upper()
        elif name.isdigit():
            code = "Digit" + name
    if code not in SCANCODES:
        raise KeyError(f"noma'lum klavish: {name!r}")
    return SCANCODES[code]


def _key_events(name: str, down: bool) -> list[INPUT]:
    scan, extended = resolve_key(name)
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if not down:
        flags |= KEYEVENTF_KEYUP
    return [_key(scan, flags)]


def key(name: str, action: str = "tap") -> None:
    """action: 'down' | 'up' | 'tap'."""
    events: list[INPUT] = []
    if action in ("down", "tap"):
        events += _key_events(name, True)
    if action in ("up", "tap"):
        events += _key_events(name, False)
    _send(events)


def combo(keys: Iterable[str]) -> None:
    """Masalan combo(["ctrl", "shift", "Escape"]) - hammasi birga bosiladi."""
    names = list(keys)
    events: list[INPUT] = []
    for n in names:
        events += _key_events(n, True)
    for n in reversed(names):
        events += _key_events(n, False)
    _send(events)


def type_text(text: str) -> None:
    """Matnni Unicode sifatida kiritadi.

    Skan-kod emas, Unicode ishlatilgani muhim: shunda kompyuterdagi klaviatura
    tili qanday bo'lishidan qat'i nazar o'zbekcha, ruscha, emoji - hammasi
    to'g'ri yoziladi. Telefonning o'z klaviaturasidan kelgan matn shu yerga
    tushadi.
    """
    events: list[INPUT] = []
    # UTF-16 birliklariga bo'lamiz: emoji kabi belgilar ikkita birlikdan iborat
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        unit = int.from_bytes(data[i : i + 2], "little")
        events.append(_key(unit, KEYEVENTF_UNICODE))
        events.append(_key(unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        # SendInput navbati cheksiz emas - uzun matnni bo'lib yuboramiz
        if len(events) >= 200:
            _send(events)
            events = []
    _send(events)
