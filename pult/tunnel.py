"""
Remote access: getting the computer past the edge of one Wi-Fi.

The problem is that opening a port on a home router does not work for
most people - carriers do not hand out public addresses. The way out
runs the other direction: the computer dials outward and holds a tunnel
open, and the phone connects to the tunnel's public address. That works
on any network and touches no router settings.

For now cloudflared's "quick tunnel" mode is used: no account, no
domain. A bonus is that the address carries a real certificate, so the
browser does not warn and WebCodecs works.

The catch: the address is new on every start. That is why it is
delivered in the Telegram message - one is sent when the computer comes
online anyway, so the address rides along with it.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import re
import stat
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("pult.tunnel")

# cloudflared prints the quick-tunnel address in this shape
URL_RE = re.compile(rb"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

# Hostnames on that domain which are not tunnels.
#
# cloudflared mentions its own API endpoint in the same log stream it
# announces the tunnel on, and the pattern above matches it just as
# happily. Taken as the public address it does real damage: the phone
# stores it as a way to reach this computer, and every later attempt
# loads Cloudflare's API instead of Pult - a blank page with a broken
# image on it, and no hint of why.
NOT_TUNNELS = {"api", "www"}

RELEASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"

# No console window may appear: the program runs in the background
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def asset_name() -> str | None:
    """The cloudflared file name for this system."""
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "cloudflared-windows-386.exe" if machine in ("i386", "x86") \
            else "cloudflared-windows-amd64.exe"
    if sys.platform.startswith("linux"):
        if machine in ("aarch64", "arm64"):
            return "cloudflared-linux-arm64"
        if machine.startswith("arm"):
            return "cloudflared-linux-arm"
        return "cloudflared-linux-amd64"
    # macOS ships an archive that would have to be unpacked - for now it
    # is simpler for the user to install it: brew install cloudflared
    return None


def find_binary(config_dir: Path, hint: str = "") -> Path | None:
    """Finds cloudflared: the configured path, then ours, then PATH."""
    if hint:
        p = Path(hint)
        if p.is_file():
            return p
        log.warning("cloudflared from the settings was not found: %s", p)

    name = asset_name()
    if name:
        local = config_dir / "bin" / name
        if local.is_file():
            return local

    from shutil import which

    found = which("cloudflared")
    return Path(found) if found else None


async def download(config_dir: Path) -> Path | None:
    """Downloads cloudflared.

    A one-time job: the file lands in the settings folder and is taken
    from there afterwards.
    """
    name = asset_name()
    if not name:
        log.warning("no automatic download for this system - install "
                    "cloudflared yourself")
        return None

    import aiohttp

    target = config_dir / "bin" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    url = f"{RELEASE}/{name}"

    # The file is around 50 MB. On a slow link that takes tens of
    # minutes, so there is no overall timeout - it would cut off a
    # download that is making progress. The limit is on waiting only:
    # it fires when no data arrives at all.
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)

    # An interrupted download must not start over: the partial file is
    # kept and the rest is requested. On a slow link that matters.
    have = tmp.stat().st_size if tmp.is_file() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    log.info("downloading cloudflared%s: %s",
             f" (resuming at {have / 1048576:.1f} MB)" if have else "", url)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, headers=headers) as r:
                if r.status == 416:
                    # The server says there is no such range - the file
                    # may already be complete; start over
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError("the partial file is unusable, try again")
                r.raise_for_status()
                resume = r.status == 206
                if not resume:
                    have = 0
                total = int(r.headers.get("Content-Length", 0)) + have
                done = have
                step = done
                with tmp.open("ab" if resume else "wb") as f:
                    async for chunk in r.content.iter_chunked(256 * 1024):
                        f.write(chunk)
                        done += len(chunk)
                        # Waiting with no sign of progress looks like the
                        # program has hung - log the state every 5 MB.
                        if done - step >= 5 * 1048576:
                            step = done
                            if total:
                                log.info("cloudflared: %.0f%% (%.1f/%.1f MB)",
                                         done * 100 / total,
                                         done / 1048576, total / 1048576)
                            else:
                                log.info("cloudflared: %.1f MB", done / 1048576)
        # Named only once written, so a partial file is never used
        tmp.replace(target)
        if sys.platform != "win32":
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        log.info("cloudflared ready: %s (%.1f MB)",
                 target, target.stat().st_size / 1048576)
        return target
    except Exception as exc:
        # The partial file is kept on purpose: the next attempt resumes
        log.warning("cloudflared download failed: %s: %s", type(exc).__name__, exc)
        return None


class Tunnel:
    """A running tunnel. Rebuilds itself when it drops."""

    def __init__(self, config_dir: Path, hint: str, local_url: str,
                 host_id: str = "") -> None:
        self.config_dir = config_dir
        self.hint = hint
        self.binary: Path | None = None
        self.local_url = local_url
        # Used to confirm that a candidate address really reaches us
        # before it is handed out. Empty skips the check.
        self.host_id = host_id
        self.url: str = ""
        self.ready = asyncio.Event()
        self._proc: subprocess.Popen | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="pult-tunnel")

    async def _run(self) -> None:
        delay = 3.0
        while True:
            try:
                await self._once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("tunnel error: %s", exc)
            # The tunnel dropped. The address no longer works, so it is
            # cleared - otherwise a dead address would look valid.
            self.url = ""
            self.ready.clear()
            log.info("tunnel dropped, retrying in %.0f seconds", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 1.8, 120)

    async def _ensure_binary(self) -> Path:
        """Finds or downloads cloudflared.

        This sits inside the retry loop: on a slow link the download can
        be interrupted, and then the next attempt resumes from the
        partial file. Downloading before startup would make the program
        wait for it.
        """
        if self.binary and self.binary.is_file():
            return self.binary
        found = find_binary(self.config_dir, self.hint)
        if found is None:
            found = await download(self.config_dir)
        if found is None:
            raise RuntimeError("cloudflared not found")
        self.binary = found
        return found

    async def _audit(self, url: str) -> None:
        """Withdraws an address that turns out to belong to someone else.

        Reading the tunnel address out of a log stream is guesswork, and
        a wrong guess is expensive: it goes to the phone, is stored
        there, and is tried on every later connection, giving a page
        that is not Pult and an error nobody can interpret. That is
        exactly what happened when cloudflared's own API endpoint was
        mistaken for the tunnel.

        /api/info answers without a key and names the computer, which is
        what tells our tunnel from anything else that replies.

        The rule is deliberately one-sided. Only a clear answer from the
        wrong computer withdraws the address; being unreachable never
        does, because that is also what a tunnel looks like in its first
        minutes, and dropping a good address costs more than keeping a
        doubtful one.
        """
        if not self.host_id:
            return

        import aiohttp

        for _ in range(10):
            await asyncio.sleep(6)
            if self.url != url:
                return
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as sess:
                    async with sess.get(f"{url}/api/info") as r:
                        if r.status != 200:
                            continue
                        data = await r.json()
            except Exception:
                # Not reachable yet, or not reachable from here. Says
                # nothing about whose address it is.
                continue

            if data.get("id") == self.host_id:
                log.info("tunnel address confirmed: %s", url)
                return
            log.warning("%s answers as a different computer - withdrawing it",
                        url)
            if self.url == url:
                self.url = ""
                self.ready.clear()
            return

    def _accept(self, url: str) -> None:
        if self.url:
            return
        self.url = url
        self.ready.set()
        log.info("public address: %s", url)

    async def _once(self) -> None:
        binary = await self._ensure_binary()
        cmd = [
            str(binary), "tunnel",
            "--url", self.local_url,
            # The local server uses a self-signed certificate
            "--no-tls-verify",
            "--no-autoupdate",
            # The address is read from this stream
            "--loglevel", "info",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW,
        )
        self._proc = proc
        log.info("cloudflared started (pid %s)", proc.pid)
        tried: set[str] = set()
        checks: list[asyncio.Task] = []
        try:
            assert proc.stderr
            async for line in proc.stderr:
                m = URL_RE.search(line)
                if not m or self.url:
                    continue
                found = m.group(0).decode("ascii")
                label = found.split("//", 1)[1].split(".", 1)[0]
                if label in NOT_TUNNELS:
                    log.debug("ignoring %s - not a tunnel address", found)
                    continue
                if found in tried:
                    continue
                tried.add(found)
                # Published straight away, then watched.
                #
                # Holding it back until it could be reached was tried and
                # was worse: a quick tunnel's name can take minutes to
                # resolve, so a perfectly good address was thrown away
                # and remote access went dead - a heavier failure than
                # the one being guarded against. Being unreachable for a
                # moment is what a new tunnel looks like; it is not
                # evidence of a wrong address.
                self._accept(found)
                checks.append(asyncio.create_task(self._audit(found)))
        finally:
            for t in checks:
                t.cancel()
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()

    async def wait_url(self, timeout: float = 90) -> str:
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("no tunnel address after %.0f seconds", timeout)
        return self.url

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.warning("error while stopping the tunnel", exc_info=True)
            self._task = None
        proc = self._proc
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass


async def create(cfg, config_dir: Path) -> Tunnel | None:
    """Opens a tunnel according to the settings. None when disabled."""
    mode = (cfg.remote.mode or "off").lower()
    if mode in ("off", "", "none"):
        return None
    if mode != "cloudflare":
        log.warning("unknown remote-access mode: %s", mode)
        return None

    scheme = "http" if cfg.tls == "off" else "https"
    tunnel = Tunnel(config_dir, cfg.remote.binary,
                    f"{scheme}://127.0.0.1:{cfg.port}", cfg.host_id)
    tunnel.start()
    return tunnel
