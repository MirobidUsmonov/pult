"""
Speech to text, so the phone can dictate instead of typing.

Typing on a phone to drive a computer is the weakest part of the whole
idea: the on-screen keyboard covers half the screen and every word costs
several taps. Speaking is far quicker, and for Uzbek in particular there
is a good offline model to hand.

The work is done by whisper.cpp, called directly through its C library
rather than through any application that ships it. That keeps this a
library call with no windows, no clipboard and no keyboard simulation in
the middle - the text comes back as a return value.

Nothing here reaches the network: the audio never leaves the computer.

The model is loaded on first use and released again once it has been
idle for a while, because it is large (the Uzbek one is about 800 MB)
and a remote control has no business holding that much memory all day.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("pult.stt")

# 16 kHz mono is what every Whisper model expects; anything else has to
# be resampled before it is handed over.
SAMPLE_RATE = 16000

# Below this peak amplitude a recording counts as silence and is never
# shown to the model. Speech from a phone held at arm's length sits well
# above it; room tone and a muted microphone sit well below.
SILENCE_PEAK = 0.012

# How certain the model has to be that nobody was speaking before its
# words for that stretch are thrown away. Set generously: losing a real
# word is worse than letting an occasional stray one through, and the
# text lands in a field the user can still edit.
NO_SPEECH_LIMIT = 0.7

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Offsets into whisper_full_params.
#
# The struct is long and its layout shifts between whisper.cpp releases,
# so it is never built by hand here: the library is asked for a
# default-initialised one and only these few fields are overwritten. The
# offsets were read back from the library in use and are checked at
# startup (_verify_layout) - if a future version moves them, loading
# fails loudly instead of writing into the wrong field.
_P_STRATEGY = 0          # int    0 = greedy
_P_N_THREADS = 4         # int
_P_TRANSLATE = 20        # bool
_P_NO_TIMESTAMPS = 22    # bool
_P_PRINT_SPECIAL = 24    # bool
_P_PRINT_PROGRESS = 25   # bool
_P_PRINT_REALTIME = 26   # bool
_P_PRINT_TIMESTAMPS = 27 # bool
_P_LANGUAGE = 104        # const char *


def default_library() -> Path | None:
    """Finds a whisper.cpp library already on this computer.

    Kotib (an offline Uzbek dictation program) ships one together with an
    Uzbek model, and if it is installed there is no reason to make the
    user download several hundred megabytes again.
    """
    for base in (
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Kotib",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Kotib",
    ):
        lib = base / "libwhisper.dll"
        if lib.is_file():
            return lib
    return None


def default_model() -> Path | None:
    """The model file next to the library."""
    lib = default_library()
    if lib is None:
        return None
    models = lib.parent / "models"
    if not models.is_dir():
        return None
    # Prefer an Uzbek-specific model, otherwise take whatever is there.
    found = sorted(models.glob("*.bin"))
    for p in found:
        if "rubaistt" in p.name.lower() or "uz" in p.stem.lower().split("-"):
            return p
    return found[0] if found else None


def available(cfg) -> bool:
    """Whether dictation can work at all, without loading anything."""
    return _paths(cfg) is not None


def _paths(cfg) -> tuple[Path, Path] | None:
    s = getattr(cfg, "stt", None)
    if s is not None and not s.enabled:
        return None

    lib = Path(s.library) if (s and s.library) else default_library()
    model = Path(s.model) if (s and s.model) else default_model()
    if lib and model and lib.is_file() and model.is_file():
        return lib, model
    return None


class Recogniser:
    """Holds the loaded model and turns audio into text.

    One instance per program. The calls into the library are serialised:
    a whisper context is not safe to use from two threads at once, and
    dictation is not something anyone does twice at the same moment.
    """

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._lib = None
        self._ctx = None
        self._model_path: Path | None = None
        self._last_used = 0.0
        self._language = b"uz"
        # Kept alive deliberately: the params struct stores a pointer to
        # this buffer rather than a copy of it, so letting it be
        # collected would leave the library reading freed memory.
        self._lang_buf = ctypes.create_string_buffer(self._language)

    # -- loading -----------------------------------------------------------

    def _load(self) -> None:
        paths = _paths(self.cfg)
        if paths is None:
            raise RuntimeError("dictation is not set up on this computer")
        lib_path, model_path = paths

        if self._ctx is not None and self._model_path == model_path:
            return
        self._unload()

        # The library sits next to its backends (ggml-vulkan and the
        # per-CPU builds) and loads them by name, so its folder has to be
        # searchable or the load fails with a bare "not found".
        os.add_dll_directory(str(lib_path.parent))
        _load_backends(lib_path.parent)
        lib = ctypes.CDLL(str(lib_path))

        lib.whisper_init_from_file.restype = ctypes.c_void_p
        lib.whisper_init_from_file.argtypes = [ctypes.c_char_p]
        lib.whisper_free.argtypes = [ctypes.c_void_p]
        lib.whisper_full_default_params_by_ref.restype = ctypes.c_void_p
        lib.whisper_full_default_params_by_ref.argtypes = [ctypes.c_int]
        lib.whisper_free_params.argtypes = [ctypes.c_void_p]
        lib.whisper_full.restype = ctypes.c_int
        lib.whisper_full.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_float), ctypes.c_int]
        lib.whisper_full_n_segments.restype = ctypes.c_int
        lib.whisper_full_n_segments.argtypes = [ctypes.c_void_p]
        lib.whisper_full_get_segment_text.restype = ctypes.c_char_p
        lib.whisper_full_get_segment_text.argtypes = [ctypes.c_void_p, ctypes.c_int]
        try:
            lib.whisper_full_get_segment_no_speech_prob.restype = ctypes.c_float
            lib.whisper_full_get_segment_no_speech_prob.argtypes = [ctypes.c_void_p,
                                                                   ctypes.c_int]
        except AttributeError:
            pass

        self._verify_layout(lib)
        _silence(lib)

        started = time.monotonic()
        ctx = lib.whisper_init_from_file(str(model_path).encode("utf-8"))
        if not ctx:
            raise RuntimeError(f"the model could not be loaded: {model_path}")

        self._lib = lib
        self._ctx = ctx
        self._model_path = model_path
        log.info("model loaded in %.1fs: %s", time.monotonic() - started, model_path.name)

    def _verify_layout(self, lib) -> None:
        """Checks that the params struct still looks the way we expect.

        Writing into a struct at hard-coded offsets is only safe as long
        as the offsets are right. Rather than trusting them, the
        defaults are read back and compared against what this version of
        whisper.cpp is documented to produce. A mismatch means the
        library changed, and it is far better to refuse than to quietly
        set the wrong field.
        """
        p = lib.whisper_full_default_params_by_ref(0)
        if not p:
            raise RuntimeError("the library did not return default parameters")
        try:
            raw = ctypes.string_at(p, 128)
            strategy = int.from_bytes(raw[_P_STRATEGY:_P_STRATEGY + 4], "little")
            lang_ptr = int.from_bytes(raw[_P_LANGUAGE:_P_LANGUAGE + 8], "little")
            lang = ctypes.string_at(lang_ptr, 4).split(b"\0")[0] if lang_ptr else b""
            if strategy != 0 or lang != b"en":
                raise RuntimeError(
                    "this whisper.cpp build has a different parameter layout "
                    f"(strategy={strategy}, language={lang!r}) - dictation "
                    "has been disabled rather than risk writing into the "
                    "wrong field"
                )
        finally:
            lib.whisper_free_params(p)

    def _unload(self) -> None:
        if self._ctx is not None and self._lib is not None:
            try:
                self._lib.whisper_free(self._ctx)
            except Exception:
                log.warning("the model was not released cleanly", exc_info=True)
        self._ctx = None
        self._lib = None
        self._model_path = None

    def release_if_idle(self) -> bool:
        """Frees the model once it has sat unused. Returns True if it did.

        The model is hundreds of megabytes and most of the day nobody is
        dictating, so holding it resident is pure waste. Loading it again
        costs about a second, which is fine for something that starts
        with a button press.
        """
        minutes = getattr(getattr(self.cfg, "stt", None), "idle_unload_minutes", 10)
        if minutes <= 0:
            return False
        with self._lock:
            if self._ctx is None or not self._last_used:
                return False
            if time.monotonic() - self._last_used < minutes * 60:
                return False
            log.info("releasing the model after %d idle minutes", minutes)
            self._unload()
            return True

    # -- recognition -------------------------------------------------------

    def transcribe(self, samples: bytes) -> str:
        """Turns 16 kHz mono float32 audio into text.

        Blocking, and meant to be called from a worker thread.
        """
        count = len(samples) // 4
        if count < SAMPLE_RATE // 4:
            # Under a quarter of a second is a slip of the finger, not
            # speech. Whisper would hallucinate a word out of it.
            return ""

        # Silence has to be turned away before it reaches the model.
        # Whisper does not answer "nothing was said" - given an empty
        # recording it invents a plausible phrase, and a stray press of
        # the button would drop a word nobody spoke into the text.
        if _peak(samples) < SILENCE_PEAK:
            log.info("the recording is silent, not recognising it")
            return ""

        with self._lock:
            self._load()
            lib, ctx = self._lib, self._ctx

            s = getattr(self.cfg, "stt", None)
            lang = (s.language if s and s.language else "uz").encode("ascii", "ignore")
            if lang != self._language:
                self._language = lang
                self._lang_buf = ctypes.create_string_buffer(lang)

            params = lib.whisper_full_default_params_by_ref(0)
            if not params:
                raise RuntimeError("the library did not return default parameters")
            try:
                threads = (s.threads if s and s.threads else 0) or min(8, os.cpu_count() or 4)
                _poke_int(params, _P_N_THREADS, threads)
                _poke_bool(params, _P_TRANSLATE, False)
                _poke_bool(params, _P_NO_TIMESTAMPS, True)
                # The library writes its progress to stdout, and a
                # program built with no console has nowhere to put it.
                _poke_bool(params, _P_PRINT_SPECIAL, False)
                _poke_bool(params, _P_PRINT_PROGRESS, False)
                _poke_bool(params, _P_PRINT_REALTIME, False)
                _poke_bool(params, _P_PRINT_TIMESTAMPS, False)
                _poke_ptr(params, _P_LANGUAGE, ctypes.addressof(self._lang_buf))

                buf = (ctypes.c_float * count).from_buffer_copy(samples)
                started = time.monotonic()
                rc = lib.whisper_full(ctx, params, buf, count)
                if rc != 0:
                    raise RuntimeError(f"recognition failed (code {rc})")

                parts = []
                for i in range(lib.whisper_full_n_segments(ctx)):
                    # The model's own judgement of whether anyone was
                    # speaking. Breathing, a door, music - anything that
                    # is not speech - still comes back as words, and
                    # this is what tells them apart.
                    try:
                        quiet = lib.whisper_full_get_segment_no_speech_prob(ctx, i)
                    except AttributeError:
                        quiet = 0.0
                    if quiet > NO_SPEECH_LIMIT:
                        log.info("segment %d dropped: no speech (%.2f)", i, quiet)
                        continue
                    chunk = lib.whisper_full_get_segment_text(ctx, i)
                    if chunk:
                        parts.append(chunk.decode("utf-8", "replace"))
                text = " ".join(p.strip() for p in parts).strip()
                log.info("recognised %.1fs of audio in %.1fs: %d characters",
                         count / SAMPLE_RATE, time.monotonic() - started, len(text))
            finally:
                lib.whisper_free_params(params)

            self._last_used = time.monotonic()
            return text


_LOG_CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)


def _relay_log(level: int, text: bytes, _user) -> None:
    """Sends the library's chatter to our log instead of stderr."""
    try:
        line = (text or b"").decode("utf-8", "replace").rstrip()
        if line:
            log.debug("whisper: %s", line)
    except Exception:
        pass


# Held for the lifetime of the process: the library keeps the pointer,
# so letting Python collect the callback would crash it later.
_log_cb = _LOG_CB(_relay_log)


def _silence(lib) -> None:
    """Stops the library writing to stderr.

    It is talkative - a model load alone is thirty lines - and the
    program is built with no console, so those writes go to a handle
    that is not there. Routing them into our own log keeps the detail
    available without the risk.
    """
    try:
        lib.whisper_log_set.argtypes = [_LOG_CB, ctypes.c_void_p]
        lib.whisper_log_set.restype = None
        lib.whisper_log_set(_log_cb, None)
    except AttributeError:
        pass


_backends_loaded: set[str] = set()


def _load_backends(folder: Path) -> None:
    """Registers the ggml compute backends found in a folder.

    ggml keeps its backends in separate libraries (Vulkan for the GPU,
    one per CPU instruction set) and discovers them at runtime. Left to
    itself it searches next to the running executable and in the current
    directory - neither of which is where these live when the library is
    borrowed from another program's installation. Without this the model
    loads and then dies on an assertion with "devices = 0".
    """
    key = str(folder).lower()
    if key in _backends_loaded:
        return

    ggml = folder / "ggml.dll"
    if not ggml.is_file():
        # Older builds keep the registry inside the main library and
        # find their backends on their own.
        _backends_loaded.add(key)
        return

    lib = ctypes.CDLL(str(ggml))
    try:
        fn = lib.ggml_backend_load_all_from_path
    except AttributeError:
        _backends_loaded.add(key)
        return
    fn.argtypes = [ctypes.c_char_p]
    fn.restype = None
    fn(str(folder).encode("utf-8"))

    try:
        lib.ggml_backend_reg_count.restype = ctypes.c_size_t
        log.info("ggml backends registered: %d", lib.ggml_backend_reg_count())
    except AttributeError:
        pass
    _backends_loaded.add(key)


def _peak(samples: bytes) -> float:
    """The loudest sample in the recording, 0.0 to 1.0.

    Every eighth sample is enough: this only has to tell silence from
    speech, and walking the whole array in Python would cost more than
    the answer is worth.
    """
    import array

    a = array.array("f")
    a.frombytes(samples[:len(samples) - len(samples) % 4])
    if not a:
        return 0.0
    step = max(1, len(a) // 4000)
    return max(abs(a[i]) for i in range(0, len(a), step))


def _poke_int(ptr: int, offset: int, value: int) -> None:
    ctypes.c_int.from_address(ptr + offset).value = value


def _poke_bool(ptr: int, offset: int, value: bool) -> None:
    ctypes.c_bool.from_address(ptr + offset).value = value


def _poke_ptr(ptr: int, offset: int, value: int) -> None:
    ctypes.c_void_p.from_address(ptr + offset).value = value


def to_samples(audio: bytes, ffmpeg: str) -> bytes:
    """Decodes whatever the phone recorded into 16 kHz mono float32.

    Phones record in whatever container their browser prefers - usually
    WebM/Opus, sometimes MP4/AAC - so rather than guess, the bytes are
    handed to ffmpeg, which is already part of the program.
    """
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error",
         "-i", "pipe:0",
         "-ar", str(SAMPLE_RATE), "-ac", "1",
         "-f", "f32le", "pipe:1"],
        input=audio, capture_output=True, creationflags=NO_WINDOW,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()[:200]
        raise RuntimeError(f"the audio could not be decoded: {detail}")
    return proc.stdout
