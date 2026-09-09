"""
Installation: one file to open, everything set up.

The goal is that putting Pult on a new computer needs nothing but a
single file. Installing Python, hunting for ffmpeg, downloading
cloudflared, opening Task Scheduler - all of it is automated here.

The program copies itself once into a permanent place and runs from
there. Copying is necessary: people start the file from Downloads, then
delete or move it, and automatic startup breaks.

Settings live in a "data" folder next to the program. That is
deliberate: in some environments AppData is redirected elsewhere, and
then two separate settings appear and the keys no longer match.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("pult.setup")

TASK_NAME = "Pult"
EXE_NAME = "Pult.exe"

# No console window may appear
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Pult"


def frozen_exe() -> Path | None:
    """The running .exe, or None when started from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def is_installed() -> bool:
    exe = frozen_exe()
    return exe is not None and exe.parent == install_dir()


def bundled(name: str) -> Path | None:
    """Finds a helper file bundled inside the .exe."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    path = Path(base) / name
    return path if path.is_file() else None


def preset() -> dict:
    """Ready-made settings baked in at build time.

    The Telegram bot and tunnel mode set up on the first computer travel
    to the second one this way, so nothing has to be configured there.
    """
    path = bundled("preset.json")
    if not path:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("preset.json could not be read", exc_info=True)
        return {}


def message(text: str, title: str = "Pult", icon: int = 0x40) -> None:
    """A message box. There is no console, so print would go nowhere."""
    if sys.platform == "win32":
        import ctypes

        ctypes.WinDLL("user32").MessageBoxW(None, text, title, icon)
    else:
        print(f"{title}: {text}")


def ask(text: str, title: str = "Pult") -> bool:
    if sys.platform != "win32":
        return True
    import ctypes

    # MB_OKCANCEL | MB_ICONQUESTION
    return ctypes.WinDLL("user32").MessageBoxW(None, text, title, 0x21) == 1


# ------------------------------------------------------- scheduled task

def _powershell(script: str) -> subprocess.CompletedProcess:
    """Runs a PowerShell command with no console.

    -Command is used rather than a script file: the execution policy
    that applies to script files does not get in the way here.
    """
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
         "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW,
    )


def _ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def register_task(exe: Path) -> bool:
    """Adds automatic startup at logon.

    The task is deliberately created with ordinary user rights and an
    "at logon" trigger. The reason is that on Windows a program must run
    inside the user's session to send mouse and keyboard events. Set up
    as a service it stays in session 0 and cannot touch the desktop at
    all - a place many people trip over.

    Register-ScheduledTask is used rather than schtasks.exe: in testing
    schtasks returned "Access is denied" while the very same task went
    in fine through PowerShell. schtasks is still kept as a fallback -
    another computer may behave the other way round.
    """
    if sys.platform != "win32":
        return False

    script = (
        "$ErrorActionPreference='Stop';"
        f"$exe={_ps_quote(exe)};"
        f"$dir={_ps_quote(exe.parent)};"
        "$u=\"$env:USERDOMAIN\\$env:USERNAME\";"
        "$a=New-ScheduledTaskAction -Execute $exe -WorkingDirectory $dir;"
        "$t=New-ScheduledTaskTrigger -AtLogOn -User $u;"
        # Wait for the network: started before an IP address is
        # assigned, the program builds its certificate for a wrong
        # address
        "$t.Delay='PT15S';"
        "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries"
        " -DontStopIfGoingOnBatteries -StartWhenAvailable"
        " -ExecutionTimeLimit ([TimeSpan]::Zero)"
        " -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1);"
        "$p=New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive"
        " -RunLevel Limited;"
        f"Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $a"
        " -Trigger $t -Settings $s -Principal $p"
        " -Description 'Pult - control your computer from your phone'"
        " -Force | Out-Null"
    )
    r = _powershell(script)
    if r.returncode == 0:
        return True
    log.warning("task not created through PowerShell: %s",
                (r.stderr or r.stdout).strip()[:300])

    fallback = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", f'"{exe}"',
         "/SC", "ONLOGON", "/RL", "LIMITED", "/F"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW,
    )
    if fallback.returncode == 0:
        return True
    log.warning("task not created through schtasks either: %s",
                (fallback.stderr or fallback.stdout).strip()[:300])
    return False


def create_shortcut(exe: Path) -> bool:
    """Puts a shortcut to the program on the desktop.

    The shortcut takes the installer's place: the installer is needed
    once, the program always. Clicking it opens the Pult window - the
    program is already running in the background, so no second copy
    starts, the window simply appears.
    """
    if sys.platform != "win32":
        return False
    script = (
        "$ErrorActionPreference='Stop';"
        "$desk=[Environment]::GetFolderPath('Desktop');"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
        "  (Join-Path $desk 'Pult.lnk'));"
        f"$s.TargetPath={_ps_quote(exe)};"
        f"$s.WorkingDirectory={_ps_quote(exe.parent)};"
        f"$s.IconLocation={_ps_quote(exe)};"
        "$s.Description='Pult - your phone and your computer';"
        "$s.Save()"
    )
    r = _powershell(script)
    if r.returncode != 0:
        log.warning("shortcut not created: %s", (r.stderr or r.stdout).strip()[:200])
        return False
    return True


def remove_task() -> None:
    if sys.platform != "win32":
        return
    r = _powershell(
        f"Unregister-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} "
        "-Confirm:$false -ErrorAction Stop"
    )
    if r.returncode != 0:
        subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                       capture_output=True, creationflags=NO_WINDOW)


# ---------------------------------------------------------- installation

def existing_config() -> Path | None:
    """Finds a Pult already configured on this computer.

    This matters: the key and the certificate are the computer's
    identity, and the phone has them stored. If the installer made new
    ones, the phone would stop recognising that computer - the
    connection returns 401 and the certificate looks "changed". The user
    has no way to make sense of that: nothing changed on the outside,
    yet it stopped working.
    """
    candidates: list[Path] = []

    # Ask where the previous startup entry points
    if sys.platform == "win32":
        r = _powershell(
            f"(Get-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} "
            "-ErrorAction SilentlyContinue).Actions.Execute"
        )
        for line in (r.stdout or "").splitlines():
            line = line.strip().strip('"')
            if not line:
                continue
            exe = Path(line)
            # With pythonw.exe there is no settings folder beside it -
            # we do not know the project folder from the arguments, so
            # skip it
            if exe.name.lower().startswith("python"):
                continue
            candidates.append(exe.parent / "data")

    base = os.environ.get("APPDATA")
    if base:
        candidates.append(Path(base) / "Pult")

    for d in candidates:
        if (d / "config.json").is_file():
            return d
    return None


def adopt(source: Path, data: Path) -> bool:
    """Copies the computer's identity out of the old settings.

    Only the key, the id, the name and the certificate are carried over;
    every other setting stays as the new one has it.
    """
    try:
        old = json.loads((source / "config.json").read_text(encoding="utf-8"))
    except Exception:
        log.warning("old settings could not be read: %s", source, exc_info=True)
        return False

    target = data / "config.json"
    try:
        new = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    except Exception:
        new = {}

    for field in ("token", "host_id", "host_name", "port", "local_port"):
        if old.get(field):
            new[field] = old[field]
    target.write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")

    # The certificate has to stay the same too: the phone stored its fingerprint
    for name in ("cert.pem", "key.pem"):
        src = source / name
        if src.is_file():
            shutil.copy2(src, data / name)
    return True


def stop_running(target_dir: Path) -> None:
    """Stops everything running out of that folder.

    Windows will not let a running .exe be overwritten, so a reinstall
    that does not stop it first gets "Access is denied". This happens on
    every reinstall: the program started automatically and is running
    right then.

    cloudflared is stopped too - it is a helper the program started, and
    the new copy opens its own.
    """
    if sys.platform != "win32":
        return

    _powershell(
        f"Stop-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} "
        "-ErrorAction SilentlyContinue"
    )
    _powershell(
        f"$dir={_ps_quote(str(target_dir))};"
        "Get-CimInstance Win32_Process |"
        " Where-Object { $_.ExecutablePath -and"
        " $_.ExecutablePath.StartsWith($dir, 'OrdinalIgnoreCase') } |"
        " ForEach-Object { Stop-Process -Id $_.ProcessId -Force"
        " -ErrorAction SilentlyContinue }"
    )


def _replace_exe(src: Path, target: Path) -> None:
    """Replaces the program file, even while it is busy.

    A file can stay locked for a few hundred milliseconds after its
    process is stopped, so we retry a number of times. Failing that, the
    old one is moved aside: Windows will not let a running file be
    deleted, but it does allow it to be RENAMED.
    """
    import time

    if src.resolve() == target:
        return

    last: Exception | None = None
    for _ in range(10):
        try:
            shutil.copy2(src, target)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(0.4)

    old = target.with_name(target.name + ".old")
    try:
        old.unlink(missing_ok=True)
    except OSError:
        # Left over from an earlier install and possibly still locked
        old = target.with_name(f"{target.name}.old-{os.getpid()}")
    target.rename(old)
    shutil.copy2(src, target)
    log.info("old file moved aside: %s", old)
    if last:
        log.info("(it was locked: %s)", last)


def install(quiet: bool = False) -> tuple[bool, str]:
    """Installs the program into its permanent place. (ok, message)"""
    exe = frozen_exe()
    if exe is None:
        return False, ("Installing only works for a built .exe.\n"
                       "From source: python -m pult")

    target_dir = install_dir()
    target = target_dir / EXE_NAME
    data = target_dir / "data"

    # On a reinstall the old copy is running - without stopping it the
    # file stays locked
    if target.exists():
        stop_running(target_dir)

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        data.mkdir(exist_ok=True)
        (data / "bin").mkdir(exist_ok=True)

        # If the installer carries a slim Pult.exe inside, install that
        # one; otherwise the program copies itself.
        _replace_exe(bundled(EXE_NAME) or exe, target)

        # Unpack any bundled helper programs next to the exe, so they
        # are not extracted into a temp folder on every start:
        # cloudflared is over 50 MB and that is a noticeable delay.
        for name in ("cloudflared-windows-amd64.exe", "ffmpeg.exe"):
            src = bundled(name)
            if src and not (data / "bin" / name).exists():
                shutil.copy2(src, data / "bin" / name)
    except Exception as exc:
        return False, (
            f"The files could not be copied:\n{type(exc).__name__}: {exc}\n\n"
            f"Folder: {target_dir}\n\n"
            "If Pult is running, quit it from the tray and try again."
        )

    # Have the settings created here
    os.environ["PULT_CONFIG_DIR"] = str(data)
    from . import config as cfgmod

    # If Pult was configured on this computer before, take its key and
    # certificate; otherwise the phone stops recognising it
    adopted = ""
    if not (data / "config.json").is_file():
        source = existing_config()
        if source and source.resolve() != data.resolve():
            if adopt(source, data):
                adopted = str(source)
                log.info("adopted the old settings: %s", source)

    cfg = cfgmod.load(data / "config.json")
    p = preset()
    cfg.remote.mode = p.get("remote_mode", "cloudflare")
    tg = p.get("telegram") or {}
    if tg.get("bot_token") and tg.get("chat_id"):
        cfg.telegram.enabled = True
        cfg.telegram.on_start = True
        cfg.telegram.bot_token = tg["bot_token"]
        cfg.telegram.chat_id = str(tg["chat_id"])
    upd = p.get("update") or {}
    if upd.get("mode"):
        cfg.update.mode = upd["mode"]
        cfg.update.source = upd.get("source", "")
        if upd.get("check_minutes"):
            cfg.update.check_minutes = int(upd["check_minutes"])

    ff = data / "bin" / "ffmpeg.exe"
    if ff.is_file():
        cfg.ffmpeg_path = str(ff)
    # The path is given explicitly: config_dir() reads an environment
    # variable, and changing that inside this process could cause
    # confusion later
    cfgmod.save(cfg, data / "config.json")

    # Files left over from an earlier replacement. They are no longer
    # locked, so now is the time to remove them.
    for pattern in (".old*", ".eski*"):
        for stale in target_dir.glob(EXE_NAME + pattern):
            try:
                stale.unlink()
            except OSError:
                pass

    ok_task = register_task(target)
    create_shortcut(target)

    lines = [f"Pult installed: {target_dir}"]
    if adopted:
        lines.append("The previous settings were adopted - there is no need "
                     "to pair the phone again.")
    lines.append("It starts automatically when you log in."
                 if ok_task else
                 "Automatic startup could not be added - it will have to be "
                 "set up by hand.")
    if cfg.telegram.enabled:
        lines.append("Telegram gets a message when the computer comes online.")
    if (cfg.update.mode or "off").lower() not in ("off", "", "none"):
        lines.append("It updates itself when a new version comes out.")
    if cfg.remote.mode == "cloudflare":
        if (data / "bin" / "cloudflared-windows-amd64.exe").is_file():
            lines.append("Remote access is ready - it works from any network.")
        else:
            lines.append("Remote access is on - cloudflared is downloaded on "
                         "the first start.")
    return True, "\n".join(lines)


def uninstall() -> str:
    remove_task()
    return ("Automatic startup has been removed.\n\n"
            f"The files were left here: {install_dir()}\n"
            "The settings are in that folder too - delete them by hand if "
            "you do not need them.")


def launch(exe: Path, pair: bool = True) -> None:
    """Starts the installed copy and opens the pairing page."""
    from .update import clean_env

    try:
        # The environment is cleaned: inheriting PyInstaller's variables
        # makes the new copy think it is a child process and fail
        subprocess.Popen([str(exe)], cwd=str(exe.parent),
                         creationflags=NO_WINDOW, env=clean_env())
    except Exception:
        log.exception("could not start")
        return
    if pair:
        open_pairing(exe.parent / "data")


def open_pairing(data: Path) -> None:
    """Opens the pairing page (the QR code) in the browser.

    This has to be the last step of the install: otherwise people see
    that the program started but have no idea how to connect the phone.
    """
    import time

    from . import config as cfgmod
    from . import window

    try:
        cfg = cfgmod.load(data / "config.json")
        port = cfg.port if cfg.tls == "off" else (cfg.local_port or cfg.port + 1)
        url = f"http://127.0.0.1:{port}/pair?k={cfg.token}"
        # Wait for the server to come up. The certificate is generated
        # for the first time, which can take a few seconds.
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if _port_open(port):
                window.open_url(url, size=(560, 780))
                return
            time.sleep(0.5)
        log.warning("the server did not come up, the pairing page was not opened")
    except Exception:
        log.exception("could not open the pairing page")


def _port_open(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def first_run() -> bool:
    """Offers to install on the first run.

    Returning True means the program handed over to the installed copy
    and this process should exit.
    """
    exe = frozen_exe()
    if exe is None or is_installed():
        return False

    if not ask(
        "Install Pult on this computer?\n\n"
        "\u2022 the program moves into a permanent folder\n"
        "\u2022 it starts itself at logon (no terminal window)\n"
        "\u2022 a QR code opens so the phone can connect\n\n"
        "To remove it later: Pult.exe --uninstall"
    ):
        # Declined, the program still runs, simply uninstalled: someone
        # may want to try it out first
        return False

    ok, text = install()
    if not ok:
        message(text, "Pult was not installed", 0x10)
        return False

    target = install_dir() / EXE_NAME
    message(text + "\n\nStarting it now.")
    launch(target)
    return True
