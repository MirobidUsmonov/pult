"""
O'rnatish: bitta faylni ochish bilan hammasini sozlash.

Maqsad - yangi kompyuterga Pult qo'yish uchun bitta fayldan boshqa
hech narsa kerak bo'lmasin. Python o'rnatish, ffmpeg izlash,
cloudflared yuklab olish, vazifa rejalashtiruvchisini ochish - bularning
hammasi shu yerda avtomatlashtirilgan.

Dastur o'zini bir marta doimiy joyga ko'chiradi va o'sha yerdan ishlaydi.
Ko'chirish shart: odam faylni Yuklamalar papkasidan ishga tushiradi,
keyin uni o'chiradi yoki ko'chiradi va avtomatik ishga tushirish
buziladi.

Sozlamalar dastur yonidagi "data" papkasida saqlanadi. Bu ataylab:
ba'zi muhitlarda AppData boshqa papkaga yo'naltiriladi va shunda
ikkita alohida sozlama paydo bo'lib, kalitlar mos kelmay qoladi.
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

# Konsol oynasi ochilmasligi kerak
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Pult"


def frozen_exe() -> Path | None:
    """Ishlab turgan .exe. Manba kodidan ishga tushirilgan bo'lsa - None."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def is_installed() -> bool:
    exe = frozen_exe()
    return exe is not None and exe.parent == install_dir()


def bundled(name: str) -> Path | None:
    """.exe ichiga qo'shilgan yordamchi faylni topadi."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    path = Path(base) / name
    return path if path.is_file() else None


def preset() -> dict:
    """Yig'ishda ichiga solingan tayyor sozlamalar.

    Birinchi kompyuterda sozlangan Telegram boti va tunnel rejimi
    shu yo'l bilan ikkinchi kompyuterga o'tadi - u yerda hech narsa
    sozlash kerak bo'lmaydi.
    """
    path = bundled("preset.json")
    if not path:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("preset.json o'qilmadi", exc_info=True)
        return {}


def message(text: str, title: str = "Pult", icon: int = 0x40) -> None:
    """Xabar oynasi. Konsol yo'q, shuning uchun print bermaydi."""
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


# ------------------------------------------------------------ vazifa

def _powershell(script: str) -> subprocess.CompletedProcess:
    """PowerShell buyrug'ini konsolsiz bajaradi.

    -Command ishlatiladi, skript fayli emas: skript fayllariga
    qo'yiladigan ishga tushirish siyosati bu yo'lga tegmaydi.
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
    """Kirganda avtomatik ishga tushirishni qo'shadi.

    Vazifa ataylab oddiy foydalanuvchi huquqi bilan va "kirganda"
    yaratiladi. Sababi Windows'da sichqoncha va klaviatura hodisalarini
    yuborish uchun dastur foydalanuvchi seansida ishlashi shart. Xizmat
    sifatida qo'yilsa u 0-seansda qoladi va ish stoliga umuman ta'sir
    qilolmaydi - bu ko'p odam qoqiladigan joy.

    Register-ScheduledTask ishlatiladi, schtasks.exe emas: sinovda
    schtasks "Access is denied" berdi, PowerShell orqali esa o'sha
    vazifa muammosiz yaratildi. schtasks baribir zaxira yo'l sifatida
    qoldirilgan - boshqa kompyuterda teskarisi bo'lishi mumkin.
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
        # Tarmoq ko'tarilishini kutamiz: dastur IP manzil berilmasidan
        # oldin ishga tushsa, sertifikatni noto'g'ri manzil bilan yasaydi
        "$t.Delay='PT15S';"
        "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries"
        " -DontStopIfGoingOnBatteries -StartWhenAvailable"
        " -ExecutionTimeLimit ([TimeSpan]::Zero)"
        " -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1);"
        "$p=New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive"
        " -RunLevel Limited;"
        f"Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $a"
        " -Trigger $t -Settings $s -Principal $p"
        " -Description 'Pult - telefondan kompyuterni boshqarish agenti'"
        " -Force | Out-Null"
    )
    r = _powershell(script)
    if r.returncode == 0:
        return True
    log.warning("vazifa PowerShell orqali yaratilmadi: %s",
                (r.stderr or r.stdout).strip()[:300])

    fallback = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", f'"{exe}"',
         "/SC", "ONLOGON", "/RL", "LIMITED", "/F"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW,
    )
    if fallback.returncode == 0:
        return True
    log.warning("vazifa schtasks orqali ham yaratilmadi: %s",
                (fallback.stderr or fallback.stdout).strip()[:300])
    return False


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


# ------------------------------------------------------------ o'rnatish

def existing_config() -> Path | None:
    """Shu kompyuterda ilgari sozlangan Pult'ni topadi.

    Bu muhim: kalit va sertifikat kompyuterning "shaxsi" hisoblanadi va
    telefonda saqlangan. Agar o'rnatgich yangisini yaratsa, telefon
    o'sha kompyuterni tanimay qoladi - ulanish 401 beradi, sertifikat
    esa "o'zgargan" deb ko'rinadi. Buni foydalanuvchi tushunolmaydi:
    tashqaridan hech narsa o'zgarmagan, lekin ishlamay qolgan.
    """
    candidates: list[Path] = []

    # Avvalgi avtomatik ishga tushirish qayerni ko'rsatayotganini so'raymiz
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
            # pythonw.exe bo'lsa yonida sozlama yo'q - argumentdagi
            # loyiha papkasini bilmaymiz, shuning uchun o'tkazamiz
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
    """Eski sozlamadan kompyuterning "shaxsini" ko'chiradi.

    Faqat kalit, raqam, nom va sertifikat ko'chiriladi - qolgan
    sozlamalar yangisiniki bo'lib qolaveradi.
    """
    try:
        old = json.loads((source / "config.json").read_text(encoding="utf-8"))
    except Exception:
        log.warning("eski sozlama o'qilmadi: %s", source, exc_info=True)
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

    # Sertifikat ham o'sha bo'lishi kerak: telefon uning izini saqlagan
    for name in ("cert.pem", "key.pem"):
        src = source / name
        if src.is_file():
            shutil.copy2(src, data / name)
    return True


def stop_running(target_dir: Path) -> None:
    """O'sha papkadan ishlab turgan hamma narsani to'xtatadi.

    Windows ishlab turgan .exe ustiga yozishga ruxsat bermaydi, shuning
    uchun qayta o'rnatish oldin uni to'xtatmasa "Access is denied"
    beradi. Bu qayta o'rnatishda doim uchraydigan holat: dastur
    avtomatik ishga tushgan va o'sha payt ishlab turibdi.

    cloudflared ham to'xtatiladi - u dastur ishga tushirgan yordamchi
    va yangi nusxa o'zinikini ochadi.
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
    """Dastur faylini almashtiradi, band bo'lsa ham.

    Jarayon to'xtatilgach ham fayl bir necha yuz millisekund band
    qolishi mumkin, shuning uchun bir necha marta urinamiz. Baribir
    bo'lmasa eskisini chetga suramiz: Windows ishlab turgan faylni
    o'chirishga ruxsat bermaydi, lekin NOMINI O'ZGARTIRISHGA beradi.
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

    old = target.with_name(target.name + ".eski")
    try:
        old.unlink(missing_ok=True)
    except OSError:
        # Oldingi o'rnatishdan qolgan va hali band bo'lishi mumkin
        old = target.with_name(f"{target.name}.eski-{os.getpid()}")
    target.rename(old)
    shutil.copy2(src, target)
    log.info("eski fayl chetga surildi: %s", old)
    if last:
        log.info("(band edi: %s)", last)


def install(quiet: bool = False) -> tuple[bool, str]:
    """Dasturni doimiy joyga o'rnatadi. (muvaffaqiyat, xabar)"""
    exe = frozen_exe()
    if exe is None:
        return False, ("O'rnatish faqat yig'ilgan .exe uchun ishlaydi.\n"
                       "Manba kodidan: python -m pult")

    target_dir = install_dir()
    target = target_dir / EXE_NAME
    data = target_dir / "data"

    # Qayta o'rnatishda eski nusxa ishlab turgan bo'ladi - uni
    # to'xtatmasak fayl band bo'lib qoladi
    if target.exists():
        stop_running(target_dir)

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        data.mkdir(exist_ok=True)
        (data / "bin").mkdir(exist_ok=True)

        # O'rnatgich ichida yengil Pult.exe bo'lsa - o'shani qo'yamiz.
        # Bo'lmasa dastur o'zini ko'chiradi.
        _replace_exe(bundled(EXE_NAME) or exe, target)

        # Yordamchi dasturlar .exe ichida bo'lsa - yoniga chiqaramiz.
        # Ular har ishga tushganda vaqtinchalik papkaga ochilmasin:
        # cloudflared 50 MB dan ortiq va bu sezilarli kechikish.
        for name in ("cloudflared-windows-amd64.exe", "ffmpeg.exe"):
            src = bundled(name)
            if src and not (data / "bin" / name).exists():
                shutil.copy2(src, data / "bin" / name)
    except Exception as exc:
        return False, (
            f"Fayllarni ko'chirib bo'lmadi:\n{type(exc).__name__}: {exc}\n\n"
            f"Papka: {target_dir}\n\n"
            "Pult ishlab tursa uni treydan chiqaring va qaytadan urinib "
            "ko'ring."
        )

    # Sozlamalar shu yerda yaratilsin
    os.environ["PULT_CONFIG_DIR"] = str(data)
    from . import config as cfgmod

    # Shu kompyuterda Pult ilgari sozlangan bo'lsa - kalitini va
    # sertifikatini olamiz, aks holda telefon uni tanimay qoladi
    adopted = ""
    if not (data / "config.json").is_file():
        source = existing_config()
        if source and source.resolve() != data.resolve():
            if adopt(source, data):
                adopted = str(source)
                log.info("eski sozlama olindi: %s", source)

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
    # Yo'l aniq ko'rsatiladi: config_dir() muhit o'zgaruvchisiga
    # tayanadi va uni shu jarayonda o'zgartirganimiz keyinroq
    # chalkashlik tug'dirishi mumkin
    cfgmod.save(cfg, data / "config.json")

    # Oldingi almashtirishdan qolgan fayllar. Endi ular band emas,
    # shuning uchun shu payt o'chirish mumkin.
    for stale in target_dir.glob(EXE_NAME + ".eski*"):
        try:
            stale.unlink()
        except OSError:
            pass

    ok_task = register_task(target)

    lines = [f"Pult o'rnatildi: {target_dir}"]
    if adopted:
        lines.append("Avvalgi sozlama olindi - telefonni qayta ulash "
                     "kerak emas.")
    lines.append("Kirganda avtomatik ishga tushadi."
                 if ok_task else
                 "Avtomatik ishga tushirishni qo'shib bo'lmadi - "
                 "uni qo'lda sozlash kerak bo'ladi.")
    if cfg.telegram.enabled:
        lines.append("Kompyuter yonganda Telegramga xabar keladi.")
    if (cfg.update.mode or "off").lower() not in ("off", "", "none"):
        lines.append("Yangi versiya chiqsa o'zi yangilanadi.")
    if cfg.remote.mode == "cloudflare":
        if (data / "bin" / "cloudflared-windows-amd64.exe").is_file():
            lines.append("Tashqi kirish tayyor - har qanday tarmoqdan ishlaydi.")
        else:
            lines.append("Tashqi kirish yoqildi - cloudflared birinchi ishga "
                         "tushganda yuklab olinadi.")
    return True, "\n".join(lines)


def uninstall() -> str:
    remove_task()
    return ("Avtomatik ishga tushirish o'chirildi.\n\n"
            f"Fayllar shu yerda qoldi: {install_dir()}\n"
            "Sozlamalar ham o'sha papkada - kerak bo'lmasa qo'lda o'chiring.")


def launch(exe: Path, pair: bool = True) -> None:
    """O'rnatilgan nusxani ishga tushiradi va telefonni ulash sahifasini ochadi."""
    from .update import clean_env

    try:
        # Muhit tozalanadi: PyInstaller o'zgaruvchilari meros bo'lib
        # o'tsa yangi nusxa o'zini bola jarayon deb o'ylab, xato beradi
        subprocess.Popen([str(exe)], cwd=str(exe.parent),
                         creationflags=NO_WINDOW, env=clean_env())
    except Exception:
        log.exception("ishga tushirib bo'lmadi")
        return
    if pair:
        open_pairing(exe.parent / "data")


def open_pairing(data: Path) -> None:
    """Telefonni ulash sahifasini (QR kod) brauzerda ochadi.

    O'rnatishning oxirgi qadami shu bo'lishi kerak: aks holda odam
    dastur ishga tushganini ko'radi-yu, telefonni qanday ulashni
    bilmay qoladi.
    """
    import time

    from . import config as cfgmod
    from . import window

    try:
        cfg = cfgmod.load(data / "config.json")
        port = cfg.port if cfg.tls == "off" else (cfg.local_port or cfg.port + 1)
        url = f"http://127.0.0.1:{port}/pair?k={cfg.token}"
        # Server ko'tarilishini kutamiz. Sertifikat birinchi marta
        # yasalgani uchun bu bir necha soniya olishi mumkin.
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if _port_open(port):
                window.open_url(url, size=(560, 780))
                return
            time.sleep(0.5)
        log.warning("server ko'tarilmadi, ulash sahifasi ochilmadi")
    except Exception:
        log.exception("ulash sahifasini ochib bo'lmadi")


def _port_open(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def first_run() -> bool:
    """Birinchi ochilishda o'rnatishni taklif qiladi.

    True qaytarsa - dastur o'rnatilgan nusxaga topshirdi va bu
    jarayon chiqishi kerak.
    """
    exe = frozen_exe()
    if exe is None or is_installed():
        return False

    if not ask(
        "Pult shu kompyuterga o'rnatilsinmi?\n\n"
        "• dastur doimiy papkaga ko'chiriladi\n"
        "• kirganda o'zi ishga tushadi (terminal ochilmaydi)\n"
        "• telefondan ulanish uchun QR kod ochiladi\n\n"
        "Keyinroq o'chirish: Pult.exe --uninstall"
    ):
        # Rad etilsa ham dastur ishlayveradi, shunchaki o'rnatilmagan
        # holda: odam avval sinab ko'rmoqchi bo'lishi mumkin
        return False

    ok, text = install()
    if not ok:
        message(text, "Pult o'rnatilmadi", 0x10)
        return False

    target = install_dir() / EXE_NAME
    message(text + "\n\nEndi ishga tushiryapman.")
    launch(target)
    return True
