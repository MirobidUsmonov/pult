"""
Builds the APK without Gradle.

Why: Gradle wants a 130 MB download, which on a slow connection stretches
to half an hour. The Android SDK already contains everything a build
needs - aapt2, d8, zipalign, apksigner - and the app is small enough to
call them directly.

The result is no different from a Gradle build: the same APK, signed the
same way (with the debug key).

Usage:
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
    """Prints to the console. Characters the console encoding cannot
    handle must not bring the script down - otherwise the real error
    would never be seen."""
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
        print(f"\nERROR ({step or 'command'}):")
        print(f"  {printable[:400]}")
        for line in (result.stdout or "").splitlines()[-25:]:
            print("  " + line)
        for line in (result.stderr or "").splitlines()[-25:]:
            print("  " + line)
        raise SystemExit(1)
    # Warnings are useful, but javac's routine "deprecated" noise
    # drowns them out - only show the ones that matter
    for line in (result.stderr or "").splitlines():
        if "error" in line.lower():
            say("  " + line)


def prepare_manifest(build_dir: Path) -> Path:
    """Copies the manifest with a package attribute added.

    aapt2 requires that attribute, while newer Gradle versions do the
    opposite - seeing it in the manifest is an error, and they take the
    package name from the namespace in build.gradle. So that both routes
    build, the original manifest is left alone and the attribute is
    added to the copy.
    """
    text = (SRC / "AndroidManifest.xml").read_text(encoding="utf-8")
    if "package=" not in text.split(">", 1)[0]:
        text = text.replace(
            "<manifest ", f'<manifest package="{PACKAGE}" ', 1)
    out = build_dir / "AndroidManifest.xml"
    out.write_text(text, encoding="utf-8")
    return out


def write_build_config(gen: Path) -> Path:
    """BuildConfig.java, which Gradle normally generates.

    The app only takes the DEBUG flag from it (to attach the WebView to
    a desktop browser for troubleshooting), so writing it by hand is
    enough.
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
    """The signing key. Android will not install an unsigned APK.

    A debug key is the norm on Android, passwords and all - they are
    public knowledge. It only guarantees "this file was not modified";
    it is not meant for release to a store.
    """
    if path.exists():
        return
    print("  generating the signing key...")
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

    # 1. Resources
    print("  compiling resources...")
    res_zip = build_dir / "res.zip"
    run([tools.aapt2(), "compile", "--dir", SRC / "res", "-o", res_zip],
        step="aapt2 compile")

    # 2. Linking: manifest + resources -> the APK base, and R.java
    print("  linking resources...")
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

    # 3. The Java code
    print("  compiling java...")
    write_build_config(gen)
    sources = sorted(str(p) for p in list(gen.rglob("*.java")) + list((SRC / "java").rglob("*.java")))
    run([
        tools.javac(),
        # release 11: the android.jar classes come from the classpath
        # and java.* from the JDK itself. 11 is the level Android
        # supports.
        "--release", "11",
        "-encoding", "UTF-8",
        "-nowarn",
        "-cp", str(tools.android_jar),
        "-d", str(classes),
        *sources,
    ], step="javac")

    # 4. dex, the Android bytecode
    print("  generating dex...")
    class_files = sorted(str(p) for p in classes.rglob("*.class"))
    run([
        tools.d8(),
        "--lib", tools.android_jar,
        "--min-api", str(MIN_SDK),
        "--output", dex,
        *class_files,
    ], step="d8")

    # 5. Put the dex inside the APK
    print("  assembling the APK...")
    with zipfile.ZipFile(base_apk, "a", zipfile.ZIP_DEFLATED) as z:
        for d in sorted(dex.glob("*.dex")):
            z.write(d, d.name)

    # 6. Alignment, so Android can map it straight into memory
    aligned = build_dir / "aligned.apk"
    run([tools.zipalign(), "-f", "-p", "4", base_apk, aligned], step="zipalign")

    # 7. Signing
    print("  signing...")
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


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values


def bot_credentials() -> tuple[str, str] | None:
    """Finds the bot token and the receiving chat.

    They come from Pult's own settings, set up with
    `python -m pult --telegram`.

    The bot route is more dependable than an account session: the token
    does not expire and no login code is needed. An account session
    demands a fresh code whenever Telegram revokes it - and if the code
    does not arrive, everything stops.
    """
    sys.path.insert(0, str(ROOT))
    try:
        from pult import config as cfgmod

        t = cfgmod.load().telegram
        if t.bot_token and t.chat_id:
            return t.bot_token, str(t.chat_id)
    except Exception:
        pass
    return None


def send_to_telegram(apk: Path) -> None:
    """Sends the built APK to Telegram.

    The bot settings come from Pult's own configuration (set up with
    `python -m pult --telegram`), so nothing else has to be configured.

    Note: a bot cannot write to Telegram's "Saved Messages" - that is the
    user's own account chat and bots have no access to it. The file lands
    in the chat with the bot, from where one tap forwards it to Saved
    Messages.
    """
    creds = bot_credentials()
    if creds is None:
        say("  Telegram is not set up - skipped")
        say("  To set it up:  python -m pult --telegram <BOT_TOKEN>")
        return
    bot_token, chat_id = creds

    import urllib.request
    import uuid

    boundary = uuid.uuid4().hex
    data = apk.read_bytes()
    caption = f"Pult {VERSION_NAME} — {len(data) / 1024:.0f} KB"

    sep = "\r\n"
    parts = []
    for name, value in (("chat_id", chat_id), ("caption", caption)):
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

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            ok = b'"ok":true' in response.read()
        say("  Telegram: sent" if ok else "  Telegram: not accepted")
    except Exception as exc:
        # A failed send must not break the build
        say(f"  Telegram: not sent ({exc})")


def main() -> int:
    ap = argparse.ArgumentParser(description="Pult APK - a build without Gradle")
    ap.add_argument("--tools", default="E:\\dev-tools", help="the toolchain folder")
    ap.add_argument("--out", default=str(PROJECT / "dist"), help="the output folder")
    ap.add_argument("--telegram", action="store_true",
                    help="send the APK to Telegram once it is built")
    ap.add_argument("--desktop", action="store_true",
                    help="copy it to the desktop once it is built")
    args = ap.parse_args()

    tools = Tools(Path(args.tools))

    # d8, apksigner and the other .bat files look for java on PATH or
    # through JAVA_HOME. In a portable install neither is set on the
    # system, so the environment is set up here - child processes
    # inherit it.
    os.environ["JAVA_HOME"] = str(tools.jdk)
    os.environ["PATH"] = str(tools.jdk / "bin") + os.pathsep + os.environ.get("PATH", "")

    missing = tools.check()
    if missing:
        print("Required tools not found:")
        for m in missing:
            print("  " + m)
        print("\nRun first: powershell -File scripts\\android_toolchain.ps1")
        return 2

    print("Building the Pult APK (without Gradle)")
    apk = build(tools, Path(args.out))
    size = apk.stat().st_size / 1024 / 1024
    print()
    print(f"Ready: {apk}")
    print(f"Size:  {size:.1f} MB")

    if args.desktop:
        try:
            desktop = Path.home() / "Desktop"
            if desktop.is_dir():
                shutil.copy2(apk, desktop / apk.name)
                say(f"  Copied to the desktop: {desktop / apk.name}")
        except Exception as e:
            say(f"  Not copied to the desktop ({e})")

    if args.telegram:
        send_to_telegram(apk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
