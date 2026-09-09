"""
Settings: loading, saving, creating them on first run.

The settings file lives in the user's folder rather than next to the
program, so updating or moving the program does not lose them.
"""
from __future__ import annotations

import json
import os
import platform
import secrets
import socket
import sys
import uuid
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints


def program_dir() -> Path:
    """The folder the program lives in (next to the .exe when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


def config_dir() -> Path:
    """The settings folder.

    Three paths, in this order:

    1. The PULT_CONFIG_DIR environment variable - for tests and special
       cases.
    2. A "data" folder next to the program, if it exists - "portable
       mode". You create it by hand and the program writes there.
       Handy for running from a USB stick, but the real benefit is a
       different one: however the program is started (from a terminal,
       from the task scheduler, from inside another program) it sees a
       single place. In some environments AppData is redirected
       elsewhere, and then two separate settings files appear and the
       keys stop matching.
    3. The system's default location.
    """
    override = os.environ.get("PULT_CONFIG_DIR")
    if override:
        return Path(override)

    portable = program_dir() / "data"
    if portable.is_dir():
        return portable

    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Pult"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Pult"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pult"


def config_path() -> Path:
    return config_dir() / "config.json"


def log_path() -> Path:
    return config_dir() / "pult.log"


@dataclass
class StreamSettings:
    monitor: int = 0
    fps: int = 30
    width: int = 1280          # 0 = the screen's own width
    bitrate_kbps: int = 4000
    cursor: bool = True
    encoder: str | None = None  # None = pick automatically
    # Maps a screen number onto a capture source. Empty means straight
    # through (0->0, 1->1). When the order of the graphics card's
    # outputs does not match the system list, the user can swap this
    # from the app.
    monitor_map: list[int] = field(default_factory=list)
    # Whether the cursor may roam between screens. When on, moving the
    # cursor to another screen moves the view there too, so the cursor
    # always stays visible. When off, the cursor cannot leave the
    # screen being watched.
    follow_cursor: bool = True


@dataclass
class HubSettings:
    """Hub - an optional relay server.

    When enabled, the computer dials out to the hub and the phone
    reaches it through the hub, so neither side needs a public IP.
    When disabled, the connection is direct.
    """
    enabled: bool = False
    url: str = ""
    key: str = ""


@dataclass
class RemoteSettings:
    """Remote access - for getting past the edge of one Wi-Fi.

    Opening a port on a home router does not work for most people:
    carriers hand out addresses behind CGNAT, so there is nothing to
    forward to. Instead the computer dials outward and holds a tunnel
    open, which works on any network.

    "off"        - local network only.
    "cloudflare" - a cloudflared quick tunnel. Needs no account and no
                   domain, and the certificate is real, so the browser
                   stops warning. The catch: the address is new on
                   every start, which is why it is delivered over
                   Telegram.
    """
    mode: str = "off"
    # The cloudflared binary. When empty the program looks for it on the
    # system and downloads it if it is missing.
    binary: str = ""


@dataclass
class UpdateSettings:
    """Updating itself.

    The point is that a new version should require nothing from the
    user. On startup the program checks the source and, if it differs,
    replaces itself and restarts.

    "off"    - no updates.
    "folder" - compares against Pult.exe in the source folder, which may
               be local or a network share.
    "url"    - downloads from the source address.

    The comparison is by file hash: there are no version numbers to
    maintain, and whatever the source holds is what gets used.
    """
    mode: str = "off"
    source: str = ""
    # Also checked while running. An update is only applied when nobody
    # is connected, so a live stream is never cut short.
    check_minutes: int = 15


@dataclass
class TelegramSettings:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    on_start: bool = True       # notify when the computer comes online


@dataclass
class SecuritySettings:
    # Whether system commands (sleep, shut down, launch a program) are
    # allowed. Anyone who only wants to watch can turn this off.
    allow_commands: bool = True
    allow_input: bool = True


@dataclass
class Config:
    host_id: str = ""
    host_name: str = ""
    token: str = ""
    bind: str = "0.0.0.0"
    port: int = 8787
    # HTTP port for the computer itself, bound to 127.0.0.1 only.
    # 0 means port + 1. No certificate is needed here, so the window
    # opens on the computer without any warning.
    local_port: int = 0
    # "auto" - HTTPS with a self-signed certificate. Required for the
    #          phone: browsers only grant video decoding in a secure
    #          context.
    # "off"  - plain HTTP. Only use it behind a tunnel or proxy that
    #          terminates HTTPS in front.
    tls: str = "auto"
    # The public address the tunnel handed out. When set, it is used in
    # notifications and on the pairing page instead of the local IP.
    public_url: str = ""
    ffmpeg_path: str | None = None
    stream: StreamSettings = field(default_factory=StreamSettings)
    remote: RemoteSettings = field(default_factory=RemoteSettings)
    update: UpdateSettings = field(default_factory=UpdateSettings)
    hub: HubSettings = field(default_factory=HubSettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def _fill(cls, data: dict) -> Any:
    """Turns a dict into a dataclass, ignoring keys it does not know.

    Dropping unknown keys is deliberate: a settings file written by an
    older version must not stop the new program from starting.

    get_type_hints is needed because "from __future__ import
    annotations" at the top of the file makes f.type a plain string,
    which cannot be checked as a dataclass directly.
    """
    try:
        hints = get_type_hints(cls)
    except Exception:
        hints = {}
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        ftype = hints.get(f.name, f.type)
        if is_dataclass(ftype) and isinstance(value, dict):
            kwargs[f.name] = _fill(ftype, value)
        else:
            kwargs[f.name] = value
    return cls(**kwargs)


def default_host_name() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "computer"


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    cfg = Config()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cfg = _fill(Config, data)
        except Exception:
            # A corrupted settings file must not stop the program from
            # starting: keep a backup and start over from defaults.
            try:
                path.rename(path.with_suffix(".json.broken"))
            except Exception:
                pass
            cfg = Config()

    changed = False
    if not cfg.host_id:
        cfg.host_id = uuid.uuid4().hex
        changed = True
    if not cfg.host_name:
        cfg.host_name = default_host_name()
        changed = True
    if not cfg.token:
        cfg.token = secrets.token_urlsafe(24)
        changed = True
    if changed:
        save(cfg, path)
    return cfg


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(cfg.to_json(), encoding="utf-8")
    tmp.replace(path)
    if platform.system() != "Windows":
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass


def local_addresses(port: int, scheme: str = "https") -> list[str]:
    """Addresses the phone can reach this computer on.

    The address on the route out is asked for as well: that is more
    reliable than gethostbyname, because with a VPN or several network
    cards it still finds the address the phone actually sees.
    """
    addrs: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        addrs.append(ip)
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in addrs and not ip.startswith("127."):
                addrs.append(ip)
    except Exception:
        pass
    return [f"{scheme}://{a}:{port}" for a in addrs] or [f"{scheme}://127.0.0.1:{port}"]
