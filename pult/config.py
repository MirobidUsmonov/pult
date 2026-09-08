"""
Sozlamalar: yuklash, saqlash, birinchi ishga tushirishda yaratish.

Sozlama fayli foydalanuvchi papkasida turadi, dastur papkasida emas -
shunda dasturni yangilash yoki boshqa joyga ko'chirish sozlamalarni
yo'qotmaydi.
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
    """Dastur joylashgan papka (.exe bo'lsa uning yonidagi)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


def config_dir() -> Path:
    """Sozlamalar papkasi.

    Uchta yo'l, shu tartibda:

    1. PULT_CONFIG_DIR muhit o'zgaruvchisi - testlar va maxsus holatlar uchun.
    2. Dastur yonidagi "data" papkasi, agar u mavjud bo'lsa - "ko'chma rejim".
       Uni qo'lda yaratasiz va dastur o'sha yerga yozadi. USB'dan ishlatish
       uchun qulay, lekin asosiy foydasi boshqa: dastur turli usullar bilan
       (terminaldan, vazifa rejalashtiruvchisidan, boshqa dastur ichidan)
       ishga tushirilganda ham bitta joyni ko'radi. Ba'zi muhitlarda
       AppData boshqa papkaga yo'naltiriladi va shunda ikkita alohida
       sozlama paydo bo'lib, kalitlar mos kelmay qoladi.
    3. Tizimning odatiy joyi.
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
    width: int = 1280          # 0 = ekranning o'z kengligi
    bitrate_kbps: int = 4000
    cursor: bool = True
    encoder: str | None = None  # None = avtomatik
    # Ekran raqamini ekran olish manbasiga bog'lash. Bo'sh bo'lsa
    # to'g'ridan-to'g'ri (0->0, 1->1). Videokarta chiqishlarining tartibi
    # tizim ro'yxatiga mos kelmasa, foydalanuvchi buni ilovadan
    # almashtira oladi.
    monitor_map: list[int] = field(default_factory=list)
    # Kursor ekranlar orasida erkin yursinmi. Yoqilgan bo'lsa kursor
    # boshqa ekranga o'tganda ko'rinish ham o'sha ekranga ko'chadi -
    # shunda kursor doim ko'rinib turadi. O'chirilgan bo'lsa kursor
    # ko'rilayotgan ekrandan chiqmaydi.
    follow_cursor: bool = True


@dataclass
class HubSettings:
    """Hub - ixtiyoriy oraliq server.

    Yoqilgan bo'lsa, kompyuter hubga o'zi chiqib ulanadi va telefon
    hub orqali unga yetadi. Bu ikki tomonda ham oq IP kerak emasligini
    anglatadi. O'chirilgan bo'lsa - to'g'ridan-to'g'ri ulanish.
    """
    enabled: bool = False
    url: str = ""
    key: str = ""


@dataclass
class TelegramSettings:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    on_start: bool = True       # kompyuter yonganda xabar berish


@dataclass
class SecuritySettings:
    # Tizim buyruqlari (uxlatish, o'chirish, dastur ochish) ruxsat etilganmi.
    # Faqat kuzatish uchun ishlatmoqchi bo'lganlar buni o'chirib qo'yishi mumkin.
    allow_commands: bool = True
    allow_input: bool = True


@dataclass
class Config:
    host_id: str = ""
    host_name: str = ""
    token: str = ""
    bind: str = "0.0.0.0"
    port: int = 8787
    # "auto" - o'z-o'zini imzolagan sertifikat bilan HTTPS (telefon uchun shart:
    # brauzer video dekodlashni faqat xavfsiz kontekstda beradi).
    # "off"  - oddiy HTTP. Faqat oldida HTTPS beruvchi tunnel yoki proksi
    #          turgan bo'lsa ishlating.
    tls: str = "auto"
    # Tashqi manzil (tunnel bergan). Berilgan bo'lsa xabarnomalarda va
    # ulash sahifasida mahalliy IP o'rniga shu ishlatiladi.
    public_url: str = ""
    ffmpeg_path: str | None = None
    stream: StreamSettings = field(default_factory=StreamSettings)
    hub: HubSettings = field(default_factory=HubSettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def _fill(cls, data: dict) -> Any:
    """dict'ni dataclass'ga aylantiradi, notanish kalitlarni e'tiborsiz qoldiradi.

    Notanish kalitlarni tashlab yuborish ataylab: eski versiyada yozilgan
    sozlama fayli yangi dasturni ishga tushirishga xalaqit bermasligi kerak.

    get_type_hints kerak, chunki fayl boshida "from __future__ import
    annotations" turgani uchun f.type oddiy satr bo'lib keladi va uni
    to'g'ridan-to'g'ri dataclass sifatida tekshirib bo'lmaydi.
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
        return "kompyuter"


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    cfg = Config()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cfg = _fill(Config, data)
        except Exception:
            # Buzilgan sozlama fayli dasturni ishga tushirmay qo'ymasligi kerak:
            # zaxira nusxa qoldirib, yangisidan boshlaymiz.
            try:
                path.rename(path.with_suffix(".json.buzilgan"))
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
    """Telefondan kirish uchun mahalliy manzillar ro'yxati.

    Chiqish yo'nalishidagi manzilni ham so'raymiz: bu usul gethostbyname'dan
    ishonchliroq, chunki VPN yoki bir nechta tarmoq kartasi bo'lganda ham
    telefon haqiqatda ko'radigan manzilni topadi.
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
