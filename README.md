<div align="center">

<img src="web/icons/icon-192.png" width="88" alt="Pult">

# Pult

**Control your computer from your phone — live screen, real keyboard and mouse.**

No account, no cloud, no third-party server. Your phone talks to your PC directly.

</div>

---

## What it is

Pult turns any phone into a full remote control for your computer. Open a link,
and you get your desktop streaming live at 30–60 fps with a trackpad, a real
keyboard, and system controls. It runs quietly in the tray — no terminal window,
no visible process.

It is built for the case where existing tools are more than you need: you don't
want to create an account somewhere, you don't want your screen going through
someone else's relay, and you'd rather have something small you can read the
source of.

## How it works

```
   ┌──────────────────────────────┐
   │  PC agent (tray, no console) │
   │                              │
   │  screen ──► ffmpeg ──► H.264 │
   │  input  ◄── SendInput        │
   └────────────┬─────────────────┘
                │  HTTPS + WebSocket
                │
   ┌────────────▼─────────────────┐
   │  Phone (web app / PWA)       │
   │  H.264 ──► WebCodecs ──► screen
   │  touch  ──► mouse + keyboard │
   └──────────────────────────────┘
```

The screen is captured with the GPU (Desktop Duplication API), encoded to H.264
by the GPU encoder, and sent over a WebSocket. The phone decodes it with
**WebCodecs**, which hands the stream to the phone's hardware decoder. That is
why it stays smooth without draining the battery.

On an NVIDIA GTX 1650 capturing 1080p at 30 fps, the agent uses about **3% CPU**.

## Features

- **Live screen** — H.264, 15–60 fps, adjustable quality, multi-monitor
- **Real input** — absolute and trackpad pointer modes, full keyboard, scroll,
  drag, right-click, middle-click, back/forward buttons
- **Any keyboard layout** — text is injected as Unicode, so Uzbek, Russian,
  emoji all work regardless of the layout set on the PC
- **Dictation** — hold the microphone and speak instead of typing; recognition
  runs on your own PC through whisper.cpp and the audio never leaves it
- **System commands** — lock, sleep, shut down, turn the display off, launch
  a program
- **Hardware encoder auto-detection** — NVENC → Quick Sync → AMF → Media
  Foundation → x264 fallback, so it works on any machine
- **Idle-quiet** — capture only runs while someone is watching; nothing is
  encoded when the app is closed
- **Installable** — add to home screen and it opens like a native app

## Gestures

Two pointer modes, switchable from the toolbar.

**Trackpad** — the phone behaves like a laptop touchpad; the cursor moves
relative to your finger. **Direct** — you tap where you want to click.

| Gesture | Action |
| --- | --- |
| One finger drag | Move the cursor |
| Tap | Left click |
| Double tap | Double click |
| **Press and hold ~0.8 s, then move** | **Grab and drag — the reliable way** |
| Double tap, then hold and move | Drag (trackpad idiom, if you prefer it) |
| Two-finger tap | Right click |
| Long press (direct mode) | Right click |
| Two-finger drag | Scroll (either axis when the view is rotated) |
| The 2× button | Double click, when tapping twice is fiddly |
| Pinch | Zoom the view, then drag to pan |
| **Three-finger swipe left/right** | **Switch windows — Alt held down, follows the swipe** |
| Three-finger swipe up | Task View |
| Three-finger swipe down | Show desktop |
| Swipe the black band above the picture | Switch monitors (buttons are easier) |

### Several monitors

With more than one screen, the toolbar carries a numbered button per monitor
instead of the left/right click buttons — those are redundant anyway, since a
tap is a left click and a two-finger tap is a right click. Screens are numbered
the way they physically sit on your desk, not by their system numbers, so "2"
is the one actually on the right.

You can also swipe the black band above the picture to move between screens,
though on a phone that band can be under 20 pixels tall and is easy to miss —
the buttons are the reliable way.

Move the cursor past the edge of one screen and it crosses to the next, and the
view follows it — so the cursor is never on a screen you cannot see. This
matters because in trackpad mode a click carries no coordinates: it lands
wherever the cursor happens to be, and a cursor left behind on another monitor
means clicking blind. If you would rather keep the pointer on one screen, turn
off *Let the cursor move between screens* and it stays put.

**If the video shows one screen while the mouse moves on another**, press
*The screens are swapped* in the settings. Screen capture numbers monitors in
the graphics adapter's output order, which is not something the system exposes;
Pult orders them by device number (`\.\DISPLAY1`, `DISPLAY2`, …), which is
usually right but cannot be guaranteed. The button rotates the mapping and
saves it.

## Fitting a desktop onto a phone

A 16:9 desktop shown upright on a portrait phone becomes a thin strip — small
enough that text is unreadable. So the view rotates: when the phone is
portrait, the desktop is turned sideways and fills the screen. On a 375-wide
phone that is 78% larger than the upright fit. Rotation is automatic, and the
⟲ button cycles upright → left → right if you prefer to turn the phone the
other way. ⛶ goes fullscreen and asks the browser to lock landscape.

Pinch to zoom up to 8×, then drag with two fingers to pan; the scale indicator
in the top bar resets it. Taps stay accurate at any rotation and zoom because
the pointer mapping is computed from the view transform rather than read back
from the element's bounding box — a rotated element's bounding box is its outer
rectangle, which would put every click in the wrong place.

The window switcher works the way a laptop trackpad does: Alt is pressed and
stays down while your fingers are on the screen, so the switcher panel remains
open and moves with the swipe. It commits when you lift your fingers. Should
the connection drop mid-gesture, the host releases every key that session was
holding — a stuck Alt would otherwise make the computer unusable.

Dragging has two ways in because the trackpad idiom — double tap, keep the
second tap down, then move — turns out to be hard to perform on glass. Pressing
and holding for about 0.8 s grabs whatever is under the pointer, a green ring
follows your finger while you hold it, and lifting drops it. Anything shorter
than the hold is an ordinary click, so there is no duration that does nothing.

Two details that matter more than they look. The second tap of a double click
is sent at the **first tap's** position, because a finger never lands twice on
exactly the same pixel and Windows only counts two clicks as a double click if
they are close enough together. And a second tap does not immediately begin a
drag — the decision waits ~320 ms: lift quickly and it is a double click, keep
holding and it becomes a drag.

## Requirements

- Windows 10/11 (Linux and macOS backends are stubbed, not finished)
- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) on `PATH`
- A phone with Chrome (Android) or Safari on iOS 17+
- Optional, for dictation: a whisper.cpp build and a model (see below)

## Dictation

Typing on a phone to drive a computer is the slowest part of using it, so
the text field has a microphone next to it: tap it, speak, and it stops on
its own when you stop talking (or tap again). The recording goes to your
PC, is recognised there, and the words land in the field for you to check
before they are sent.

Recognition runs entirely on your own machine — the audio is not uploaded
anywhere. Pult does not ship a model; it uses one already installed. If you
have [Kotib](https://github.com/mirqobilov/kotib) (offline Uzbek dictation)
it is found automatically, along with its `libwhisper.dll` and Uzbek model.
Any whisper.cpp build and `ggml-*.bin` model will do:

```json
"stt": {
  "enabled": true,
  "library": "C:/path/to/libwhisper.dll",
  "model": "C:/path/to/ggml-model.bin",
  "language": "uz",
  "idle_unload_minutes": 10
}
```

The model is loaded on first use and released again once it has been idle,
so it does not hold several hundred megabytes all day. On a GTX 1650 a
phrase comes back in about a second, whatever its length.

Without a model the microphone button simply does not appear.

Nothing is installed on the phone for this — the model and the recognition
both stay on the PC. The phone only records. In the Android app the
microphone needs Android's own permission, which it asks for the first
time you tap the button; until the app holds it, the WebView reports no
microphone at all rather than prompting.

Recognition uses the GPU, so it will not work while a full-screen game
has it. Rather than hanging, it says so.

## Install

```bash
git clone https://github.com/<you>/pult
cd pult
pip install -r requirements.txt
python tests/selftest.py        # verify it works on your machine
```

Start it:

```bash
pythonw -m pult                 # tray, no console window
python -m pult --console        # with logs in the terminal
```

Right-click the tray icon → **Connect a phone (QR)** → scan the code with your
phone.

### Build a standalone `.exe`

```bash
python scripts/build.py                 # dist/Pult.exe, ~20 MB
python scripts/build.py --with-ffmpeg   # bundle ffmpeg too, ~100 MB
```

The result is a single file with no console window. Python is not required on
the machine that runs it.

### Get a message when the computer comes online

Create a bot with [@BotFather](https://t.me/BotFather), then:

```bash
python -m pult --telegram <BOT_TOKEN>
```

Send `/start` to your bot and the agent picks up the chat id by itself — you
never have to look it up. From then on, every time the machine boots and
reaches the internet, you get a message with the link.

```bash
python -m pult --test-notify    # send one now to check
```

### Android app

The phone app is a thin shell around the same web interface, so the two never
drift apart. It exists for one reason that matters and a few that are nice:

**It remembers the certificate.** A browser warns about the self-signed
certificate on every visit and refuses to install the page as a real app. The
app asks once, shows you the fingerprint to compare against the one the agent
prints, and pins it — from then on it connects silently and accepts *only* that
certificate, which is stricter than clicking through a browser warning.

It also holds a list of computers, opens straight into the last one when there
is only a single entry, runs fullscreen without browser chrome, keeps the screen
awake, and opens in landscape.

```bash
powershell -ExecutionPolicy Bypass -File scriptsndroid_toolchain.ps1   # once, ~700 MB
powershell -ExecutionPolicy Bypass -File scriptsuild_apk.ps1
```

The toolchain script installs a JDK and the Android SDK into a single folder
(`E:\dev-tools` by default) without touching `PATH`, the registry, or requiring
administrator rights — delete the folder and nothing remains. The build produces
`android/app/build/outputs/apk/debug/Pult-debug-1.0.apk`.

To pair, open **Connect a phone** from the tray icon and scan the QR labelled
*Pult ilovasi* — it opens the app directly and adds the computer. The *Brauzer*
tab has the plain link for phones without the app.

### The other direction: controlling the phone from the PC

The phone can also register itself as a *source* — it streams its own screen to
the agent and executes input coming back. Several phones can be connected at
once; the browser picks one from a list. No separate system was needed for
this: the protocol was transport- and role-agnostic from the start, so a phone
simply connects with a different role and the same messages flow the other way.

The phone's screen is captured with MediaProjection and encoded by the phone's
own hardware H.264 encoder, so the CPU is barely touched. The frames arrive in
the same shape as the PC's own stream, which is why the desktop side needed no
new code for them.

**Input requires a permission you must grant by hand.** Android does not let an
ordinary app tap on other apps — the only sanctioned path is an accessibility
service, which the user enables in system settings. There is no way around it,
by design. Screen sharing alone works without it, so you can watch the phone
without granting anything.

Tap ⇧ on a computer's card in the app to start sharing; a notification stays up
while it runs and stops it with one tap.

**The red "screen is being shared" indicator cannot be removed.** While the
phone is sharing, Android draws a privacy indicator in the status bar. That is
not something Pult puts there and not something Pult can take away: it is a
compatibility requirement tied to `MediaProjection`, with no opt-out at any API
level for an ordinary app. The one way to *avoid* it is to not use
`MediaProjection` at all and capture the screen as the `shell` user instead —
which is exactly why scrcpy shows no indicator. Doing that without root means
routing through Shizuku, which needs a one-time setup on the phone (wireless
debugging on, Shizuku started). That was considered and deliberately not done
for now; the indicator stays. What Pult *does* guarantee is that the indicator
never lingers falsely — sharing stops, and the indicator with it, as soon as the
connection to the computer drops.

To watch the phone on the computer's own monitor, use the tray menu — **Pult
window**. It opens the same web UI with `#view=phone`, which selects
the connected phone as soon as one appears and keeps waiting if none has. On a
mouse-and-keyboard screen the page drops its touch controls: clicks map
straight to taps at that point, the wheel scrolls, and the physical keyboard is
forwarded, so you type into the phone with your real keyboard.

### Reaching the computer from outside the Wi-Fi

By default the agent is only reachable on the local network. Opening a router
port is not a real option for most people — carriers hand out addresses behind
CGNAT, so there is nothing to forward to. The way out is for the computer to
dial *outward* and hold a tunnel open:

```bash
python -m pult --remote cloudflare
```

On the next start the agent fetches `cloudflared` once (~52 MB, into the config
directory) and opens a quick tunnel. That needs no Cloudflare account and no
domain. Three things come with it:

- the address works from any network, mobile data included;
- the certificate is real, so the browser stops warning and WebCodecs is happy
  without installing anything on the phone;
- nothing is exposed on your router.

The catch is that a quick tunnel's address is new on every start. That is why
it is delivered by the Telegram message the agent already sends when the
computer comes online — set that up (`--telegram <TOKEN>`) and the fresh link
arrives on your phone by itself. The tray's *Havolani nusxalash* and the QR
page also switch to the public address once the tunnel is up.

If the tunnel drops, it is rebuilt with a backoff, and the stored address is
cleared while it is down rather than left looking valid. The download resumes
from where it stopped if it is interrupted — on a slow link the file takes a
while and starting over each time would be painful.

Turn it back off with `python -m pult --remote off`.

### Getting the built APK to your phone

```bash
python scripts/build_apk_direct.py --desktop --telegram
```

`--desktop` drops a copy on the desktop. `--telegram` tries two routes in
order: your own Telegram account's *Saved Messages* (via a Telethon session, if
one is configured), falling back to a bot chat. A bot cannot write to Saved
Messages — that chat belongs to your account, and bots have no access to it —
so the account route is the only one that lands there.

If it uses an account session, note the hazard the sender guards against: two
clients sharing one Telegram auth key make Telegram raise
`AuthKeyDuplicatedError` and revoke *both*. The script checks whether the
session file is locked and refuses to connect rather than risk it.

### Start automatically at login

```powershell
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove
```

This registers a Task Scheduler entry that runs **at logon**, as your own user.
That is deliberate and not an oversight: on Windows, injecting mouse and
keyboard events requires running inside the interactive user session. Installed
as a service it would sit in session 0 and be unable to touch the desktop at
all — a common way to get this wrong.

### Adding another computer: one file, nothing to configure

```bash
python scripts/build.py --setup
```

This produces two things in `dist/`: the agent itself (`Pult.exe`, ~20 MB) and
`Pult-Setup.exe` (~82 MB), which carries the agent, ffmpeg, cloudflared and the
Telegram settings from the machine that built it. Copy that single file to the
new computer and open it. It copies the agent into `%LOCALAPPDATA%\Pult`, drops
the helper binaries beside it, writes a config with the tunnel enabled and
Telegram already set, registers the logon task, starts the agent and opens the
pairing QR. Nothing is typed and nothing is downloaded.

The split matters. If the fat file *were* the installed agent, a one-file
PyInstaller build would unpack ~82 MB into a temp directory on every boot. The
installer exists once; what stays behind is the slim agent with its helpers
sitting as plain files next to it.

Each computer generates its **own key and id** — those are deliberately not
copied, otherwise the phone could not tell the machines apart. The Telegram bot
and the tunnel mode are copied, since those are what you would otherwise have
to set up by hand.

Two things to know:

- with `--setup` the installer embeds your bot token, so treat that file as
  private — build with `--no-settings` if it will leave your hands;
- task registration goes through PowerShell's `Register-ScheduledTask`.
  `schtasks.exe` is tried as a fallback, but on the machine this was developed
  on it returned *Access is denied* while the PowerShell path worked, so the
  order is not arbitrary.

Running the slim `Pult.exe` directly also offers to install itself, so the
installer is a convenience rather than a requirement. `Pult.exe --uninstall`
removes the logon task.

### Updating itself

Re-running an installer for every change gets old fast. Point the agent at a
source and it keeps itself current:

```bash
python -m pult --update "\\\\server\\share\\pult"     # a folder, local or on the network
python -m pult --update "https://example.com/Pult.exe" # or a URL
python -m pult --update off
```

It checks at startup — before the server binds, so the new copy gets the port —
and every 15 minutes while running, applying an update only when nobody is
connected. Interrupting a live stream to install a new build is worse than
waiting.

There are no version numbers. The agent compares the SHA-256 of the source
against its own file: different means newer. That way a build doesn't have to
remember to bump anything, and re-pointing at an older build rolls back.

The swap leans on a Windows quirk: a running `.exe` cannot be overwritten, but
it *can* be renamed. So the old file is moved aside, the new one takes its
name, and the replacement is deleted on the next run. If copying the new file
fails, the old one is moved back — the machine is never left without an agent.

`--setup` carries the update source into the installer, so a second computer
inherits it. A local path obviously won't resolve on another machine; a network
share or a URL will.

## Why HTTPS with a self-signed certificate

Browsers only expose video decoding (WebCodecs), service workers and wake-lock
in a **secure context**. `localhost` counts; a LAN address over plain HTTP does
not. Serving `http://192.168.x.x` leaves `VideoDecoder` undefined and nothing
renders.

So the agent generates a self-signed certificate on first run and serves HTTPS.
Your phone shows a warning once — **Advanced → Proceed** — and after that the
page is a secure context and everything works. The certificate is stored in the
config directory and regenerated automatically if your LAN address changes.

If you put the agent behind a tunnel that terminates TLS for you (Cloudflare
Tunnel, Tailscale Serve, a reverse proxy), set `"tls": "off"` in the config and
let the tunnel provide the real certificate — then there is no warning at all.

## Security

The link contains a token that grants **full control of the machine**. Treat it
like a password.

- Every connection must present the token; there is no anonymous access
- Tokens are compared in constant time
- `/api/info` returns only the machine name without a token, so a phone can see
  which of its saved computers are online without exposing anything else
- Keyboard and mouse injection (`security.allow_input`) and system commands
  (`security.allow_commands`) can each be disabled in the config
- To revoke access, delete `token` from the config and restart — a new one is
  generated

Do not expose the port straight to the internet. Use a tunnel with its own
authentication in front of it.

## Configuration

`%APPDATA%\Pult\config.json` on Windows, `~/.config/pult/config.json` elsewhere.

**Portable mode:** create a folder named `data` next to the program and Pult
will keep everything there instead. Besides USB installs, this solves a real
problem: if the agent is launched in different ways — from a shell, from Task
Scheduler, from inside another application — the OS may hand each one a
different `AppData`, and you end up with two configs and two different tokens,
so the link on your phone stops working. A fixed `data` folder removes the
ambiguity. `PULT_CONFIG_DIR` overrides both.

The config directory in use is written to the log on every start.

```jsonc
{
  "host_name": "workstation",
  "port": 8787,
  "tls": "auto",              // "off" behind a TLS-terminating tunnel
  "stream": {
    "monitor": 0,
    "fps": 30,
    "width": 1280,            // 0 = native resolution
    "bitrate_kbps": 4000,
    "cursor": true,
    "encoder": null           // null = auto-detect
  },
  "security": { "allow_input": true, "allow_commands": true }
}
```

## Project layout

```
pult/
├── platform/      OS-specific: input injection, screen capture, system commands
├── host/          server, session protocol, pairing page
├── capture.py     ffmpeg process, H.264 access-unit splitting
├── config.py      settings
├── tls.py         self-signed certificate
└── tray.py        tray icon, background mode
web/               phone app (PWA)
tests/selftest.py  end-to-end verification
```

The protocol is transport-agnostic on purpose: a *controller* is anything that
speaks it — the phone, another program, or an AI agent. Adding a new kind of
client does not require touching the host.

## Roadmap

- [x] Telegram notification when the machine comes online
- [x] Single `.exe` build (PyInstaller), so Python is not required
- [x] Start at login
- [ ] Tunnel support — a real certificate and access from anywhere
- [ ] Hub mode — one phone, several computers, no port forwarding on either end
- [ ] Android APK wrapper around the web app
- [ ] AI controller — describe what you want, it drives the machine
- [ ] Linux and macOS capture/input backends
- [ ] UI translations (the phone interface is currently Uzbek)

## License

MIT
