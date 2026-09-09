"""
APK'ni Gradle'siz yig'adi.

Nega: Gradle 130 MB yuklab olishni talab qiladi va sekin ulanishda bu
yarim soatga cho'ziladi. Android SDK'ning o'zida esa yig'ish uchun kerak
bo'lgan hamma narsa bor - aapt2, d8, zipalign, apksigner. Ilova kichik
bo'lgani uchun ularni to'g'ridan-to'g'ri chaqirish mumkin.

Natija Gradle yig'ganidan farq qilmaydi: xuddi shu APK, xuddi shu
imzo turi (debug kaliti bilan).

Ishlatish:
    python scripts/build_apk_direct.py
    python scripts/build_apk_direct.py --tools E:\\dev-tools
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "android"
APP = PROJECT / "app"
SRC = APP / "src" / "main"

PACKAGE = "uz.pult.app"
MIN_SDK = 26
TARGET_SDK = 34
VERSION_CODE = 1
VERSION_NAME = "1.0"
BUILD_TOOLS = "34.0.0"
PLATFORM = "android-34"

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Tools:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.jdk = root / "jdk"
        self.sdk = root / "android-sdk"
        self.bt = self.sdk / "build-tools" / BUILD_TOOLS
        self.android_jar = self.sdk / "platforms" / PLATFORM / "android.jar"
        self.exe = ".exe" if os.name == "nt" else ""
        self.bat = ".bat" if os.name == "nt" else ""

    def check(self) -> list[str]:
        missing = []
        for name, path in {
            "java": self.jdk / "bin" / f"java{self.exe}",
            "javac": self.jdk / "bin" / f"javac{self.exe}",
            "keytool": self.jdk / "bin" / f"keytool{self.exe}",
            "aapt2": self.bt / f"aapt2{self.exe}",
            "d8": self.bt / f"d8{self.bat}",
            "zipalign": self.bt / f"zipalign{self.exe}",
            "apksigner": self.bt / f"apksigner{self.bat}",
            "android.jar": self.android_jar,
        }.items():
            if not path.exists():
                missing.append(f"{name}  ->  {path}")
        return missing

    def javac(self) -> Path:
        return self.jdk / "bin" / f"javac{self.exe}"

    def keytool(self) -> Path:
        return self.jdk / "bin" / f"keytool{self.exe}"

    def aapt2(self) -> Path:
        return self.bt / f"aapt2{self.exe}"

    def d8(self) -> Path:
        return self.bt / f"d8{self.bat}"

    def zipalign(self) -> Path:
        return self.bt / f"zipalign{self.exe}"

    def apksigner(self) -> Path:
        return self.bt / f"apksigner{self.bat}"


def say(text: str) -> None:
    """Konsolga chiqaradi. Konsol kodlashiga sig'magan belgilar
    skriptni yiqitmasin - aks holda asl xato ko'rinmay qoladi."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, "replace").decode(enc, "replace"))


def run(args: list, env: dict | None = None, step: str = "") -> None:
    printable = " ".join(str(a) for a in args)
    result = subprocess.run(
        [str(a) for a in args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        print(f"\nXATO ({step or 'buyruq'}):")
        print(f"  {printable[:400]}")
        for line in (result.stdout or "").splitlines()[-25:]:
            print("  " + line)
        for line in (result.stderr or "").splitlines()[-25:]:
            print("  " + line)
        raise SystemExit(1)
    # Ogohlantirishlar foydali, lekin ular orasida javac'ning odatiy
    # "deprecated" shovqini ko'p - faqat muhimlarini ko'rsatamiz
    for line in (result.stderr or "").splitlines():
        if "error" in line.lower():
            say("  " + line)


def prepare_manifest(build_dir: Path) -> Path:
    """Manifestga package atributini qo'shib, nusxasini yasaydi.

    aapt2 bu atributni talab qiladi, Gradle'ning yangi versiyalari esa
    aksincha - uni manifestda ko'rsa xato beradi va paket nomini
    build.gradle'dagi namespace'dan oladi. Ikkala yo'l bilan ham
    yig'ilishi uchun asl manifest tegilmasdan qoladi, nusxasiga esa
    atribut qo'shiladi.
    """
    text = (SRC / "AndroidManifest.xml").read_text(encoding="utf-8")
    if "package=" not in text.split(">", 1)[0]:
        text = text.replace(
            "<manifest ", f'<manifest package="{PACKAGE}" ', 1)
    out = build_dir / "AndroidManifest.xml"
    out.write_text(text, encoding="utf-8")
    return out


def write_build_config(gen: Path) -> Path:
    """BuildConfig.java - odatda Gradle yaratadi.

    Ilova undan faqat DEBUG bayrog'ini oladi (nosozlik izlash uchun
    WebView'ni kompyuter brauzeriga ulash), shuning uchun qo'lda yozish
    yetarli.
    """
    pkg_dir = gen / Path(*PACKAGE.split("."))
    pkg_dir.mkdir(parents=True, exist_ok=True)
    path = pkg_dir / "BuildConfig.java"
    path.write_text(
        f"package {PACKAGE};\n\n"
        "public final class BuildConfig {\n"
        "    public static final boolean DEBUG = true;\n"
        f'    public static final String APPLICATION_ID = "{PACKAGE}";\n'
        f'    public static final String VERSION_NAME = "{VERSION_NAME}";\n'
        f"    public static final int VERSION_CODE = {VERSION_CODE};\n"
        "}\n",
        encoding="utf-8",
    )
    return path


def ensure_debug_keystore(tools: Tools, path: Path) -> None:
    """Imzo kaliti. Android imzosiz APK'ni o'rnatmaydi.

    Debug kaliti Android'da odatiy: parollari ham hammaga ma'lum. U
    faqat "bu fayl o'zgartirilmagan" degan kafolat beradi, do'konga
    chiqarish uchun emas.
    """
    if path.exists():
        return
    print("  imzo kaliti yasalmoqda...")
    path.parent.mkdir(parents=True, exist_ok=True)
    run([
        tools.keytool(), "-genkeypair", "-v",
        "-keystore", path,
        "-storepass", "android", "-keypass", "android",
        "-alias", "androiddebugkey",
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-dname", "CN=Pult Debug, OU=Pult, O=Pult, C=UZ",
    ], step="keytool")


def build(tools: Tools, out_dir: Path) -> Path:
    build_dir = PROJECT / "build-direct"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
    gen = build_dir / "gen"
    classes = build_dir / "classes"
    dex = build_dir / "dex"
    for d in (gen, classes, dex):
        d.mkdir(parents=True, exist_ok=True)

    # 1. Resurslar
    print("  resurslar kompilyatsiya qilinmoqda...")
    res_zip = build_dir / "res.zip"
    run([tools.aapt2(), "compile", "--dir", SRC / "res", "-o", res_zip],
        step="aapt2 compile")

    # 2. Bog'lash: manifest + resurslar -> APK asosi, va R.java
    print("  resurslar bog'lanmoqda...")
    base_apk = build_dir / "base.apk"
    manifest = prepare_manifest(build_dir)
    run([
        tools.aapt2(), "link",
        "-o", base_apk,
        "-I", tools.android_jar,
        "--manifest", manifest,
        "--java", gen,
        "--min-sdk-version", str(MIN_SDK),
        "--target-sdk-version", str(TARGET_SDK),
        "--version-code", str(VERSION_CODE),
        "--version-name", VERSION_NAME,
        "--auto-add-overlay",
        res_zip,
    ], step="aapt2 link")

    # 3. Java kodi
    print("  java kompilyatsiya qilinmoqda...")
    write_build_config(gen)
    sources = sorted(str(p) for p in list(gen.rglob("*.java")) + list((SRC / "java").rglob("*.java")))
    run([
        tools.javac(),
        # release 11: android.jar sinflari classpath'dan olinadi, java.*
        # esa JDK'ning o'zidan. 11 - Android qo'llab-quvvatlaydigan daraja.
        "--release", "11",
        "-encoding", "UTF-8",
        "-nowarn",
        "-cp", str(tools.android_jar),
        "-d", str(classes),
        *sources,
    ], step="javac")

    # 4. dex - Android bayt-kodi
    print("  dex yasalmoqda...")
    class_files = sorted(str(p) for p in classes.rglob("*.class"))
    run([
        tools.d8(),
        "--lib", tools.android_jar,
        "--min-api", str(MIN_SDK),
        "--output", dex,
        *class_files,
    ], step="d8")

    # 5. dex ni APK ichiga qo'shamiz
    print("  APK yig'ilmoqda...")
    with zipfile.ZipFile(base_apk, "a", zipfile.ZIP_DEFLATED) as z:
        for d in sorted(dex.glob("*.dex")):
            z.write(d, d.name)

    # 6. Tekislash: Android xotiraga to'g'ridan-to'g'ri joylashi uchun
    aligned = build_dir / "aligned.apk"
    run([tools.zipalign(), "-f", "-p", "4", base_apk, aligned], step="zipalign")

    # 7. Imzo
    print("  imzolanmoqda...")
    keystore = tools.root / "debug.keystore"
    ensure_debug_keystore(tools, keystore)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"Pult-{VERSION_NAME}.apk"
    shutil.copy2(aligned, final)
    run([
        tools.apksigner(), "sign",
        "--ks", keystore,
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--ks-key-alias", "androiddebugkey",
        final,
    ], step="apksigner")

    run([tools.apksigner(), "verify", final], step="apksigner verify")
    return final


def send_to_telegram(apk: Path) -> None:
    """Yig'ilgan APK'ni Telegram'ga yuboradi.

    Bot sozlamalari Pult'nikidan olinadi (python -m pult --telegram bilan
    sozlanadi), shuning uchun alohida sozlash kerak emas.

    Diqqat: bot Telegram'dagi "Saqlangan xabarlar"ga yoza olmaydi - u
    foydalanuvchining o'z akkaunti chati va botlar unga kira olmaydi.
    Fayl bot bilan bo'lgan chatga tushadi, u yerdan bir bosishda
    Saqlanganlarga yuborish mumkin.
    """
    sys.path.insert(0, str(ROOT))
    try:
        from pult import config as cfgmod
    except Exception as exc:
        say(f"  Telegram: sozlamalarni o'qib bo'lmadi ({exc})")
        return

    cfg = cfgmod.load()
    t = cfg.telegram
    if not (t.bot_token and t.chat_id):
        say("  Telegram sozlanmagan - o'tkazib yuborildi")
        say("  Sozlash uchun:  python -m pult --telegram <BOT_TOKEN>")
        return

    import urllib.request
    import uuid

    boundary = uuid.uuid4().hex
    data = apk.read_bytes()
    caption = f"Pult {VERSION_NAME} — {len(data) / 1024:.0f} KB"

    sep = "\r\n"
    parts = []
    for name, value in (("chat_id", str(t.chat_id)), ("caption", caption)):
        parts.append(
            (f"--{boundary}{sep}"
             f'Content-Disposition: form-data; name="{name}"{sep}{sep}'
             f"{value}{sep}").encode("utf-8")
        )
    parts.append(
        (f"--{boundary}{sep}"
         f'Content-Disposition: form-data; name="document"; filename="{apk.name}"{sep}'
         f"Content-Type: application/vnd.android.package-archive{sep}{sep}").encode("utf-8")
    )
    parts.append(data)
    parts.append(f"{sep}--{boundary}--{sep}".encode("utf-8"))
    body = b"".join(parts)

    url = f"https://api.telegram.org/bot{t.bot_token}/sendDocument"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            ok = b'"ok":true' in response.read()
        say("  Telegram: yuborildi" if ok else "  Telegram: qabul qilinmadi")
    except Exception as exc:
        # Yuborilmagani yig'ishni buzmasligi kerak
        say(f"  Telegram: yuborilmadi ({exc})")


def main() -> int:
    ap = argparse.ArgumentParser(description="Pult APK - Gradle'siz yig'ish")
    ap.add_argument("--tools", default="E:\\dev-tools", help="asboblar papkasi")
    ap.add_argument("--out", default=str(PROJECT / "dist"), help="natija papkasi")
    ap.add_argument("--telegram", action="store_true",
                    help="yig'ilgach APK'ni Telegram'ga yuborish")
    ap.add_argument("--desktop", action="store_true",
                    help="yig'ilgach ish stoliga nusxalash")
    args = ap.parse_args()

    tools = Tools(Path(args.tools))

    # d8, apksigner va boshqa .bat fayllar java'ni PATH yoki JAVA_HOME
    # orqali qidiradi. Ko'chma o'rnatishda ular tizimda yo'q, shuning
    # uchun muhitni shu yerda sozlaymiz - bola jarayonlar meros oladi.
    os.environ["JAVA_HOME"] = str(tools.jdk)
    os.environ["PATH"] = str(tools.jdk / "bin") + os.pathsep + os.environ.get("PATH", "")

    missing = tools.check()
    if missing:
        print("Kerakli asboblar topilmadi:")
        for m in missing:
            print("  " + m)
        print("\nAvval: powershell -File scripts\\android_toolchain.ps1")
        return 2

    print("Pult APK yig'ilmoqda (Gradle'siz)")
    apk = build(tools, Path(args.out))
    size = apk.stat().st_size / 1024 / 1024
    print()
    print(f"Tayyor: {apk}")
    print(f"Hajmi:  {size:.1f} MB")

    if args.desktop:
        try:
            desktop = Path.home() / "Desktop"
            if desktop.is_dir():
                shutil.copy2(apk, desktop / apk.name)
                say(f"  Ish stoliga nusxalandi: {desktop / apk.name}")
        except Exception as e:
            say(f"  Ish stoliga nusxalanmadi ({e})")

    if args.telegram:
        send_to_telegram(apk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
