"""Local speech-to-text for voicemail.

Everything runs on this machine. A `Transcriber` turns an audio file into text; this module has
the interface, a deterministic stub for tests, and two local backends:

- `WhisperCppTranscriber`: converts the audio with ffmpeg to 16 kHz mono WAV and runs the local
  `whisper-cli` (whisper.cpp) with a local model file;
- `FasterWhisperTranscriber`: the optional `faster-whisper` package with a local model folder.

Neither ever downloads a model: the model must already be on disk. Transcription also runs inside
`offline_only()`, which makes any network connection from this process raise `NetworkBlocked`
and sets the Hugging Face offline variables, so a backend that tried to fetch something would fail
loudly instead of quietly phoning home. (A separate program started by a backend, such as
`whisper-cli`, is outside that guard; whisper.cpp does not use the network.)
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Protocol, Union

from chattr.config import Settings

PathLike = Union[str, Path]
OFFLINE_VARIABLES = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


class TranscriptionError(Exception):
    """Transcribing one file failed (unreadable audio, the engine failed)."""


class TranscriptionUnavailable(TranscriptionError):
    """Transcription can't run at all: a missing model, program or package, or a bad setting."""


class NetworkBlocked(OSError):
    """A network connection was attempted while transcribing."""


@dataclass(frozen=True)
class Transcript:
    text: str
    language: Optional[str]
    model: str


@dataclass(frozen=True)
class Outcome:
    """The result for one file: a transcript, or why there isn't one."""

    transcript: Optional[Transcript] = None
    error: Optional[str] = None


class Transcriber(Protocol):
    def transcribe(self, audio: Path) -> Transcript: ...


class StubTranscriber:
    """A fixed or per-file-name answer, for tests. Records the files it was asked about."""

    def __init__(
        self, text: Union[str, dict[str, str]] = "", language: Optional[str] = None
    ) -> None:
        self.text = text
        self.language = language
        self.calls: list[Path] = []

    def transcribe(self, audio: Path) -> Transcript:
        self.calls.append(audio)
        text = self.text.get(Path(audio).name, "") if isinstance(self.text, dict) else self.text
        return Transcript(text, self.language, "stub")


# --- No network -----------------------------------------------------------------------------


def _is_local(host: Any) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if host in (None, "", "localhost", "ip6-localhost"):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


@contextlib.contextmanager
def offline_only() -> Iterator[None]:
    """Make network connections from this process raise `NetworkBlocked` inside the block.

    Local (loopback and Unix) sockets still work. Everything is restored afterwards, even on error.
    """
    own = {name: socket.socket.__dict__.get(name) for name in ("connect", "connect_ex")}
    originals = {name: getattr(socket.socket, name) for name in own}
    original_getaddrinfo = socket.getaddrinfo
    previous = {name: os.environ.get(name) for name in OFFLINE_VARIABLES}

    def blocked(method: Any) -> Any:
        def guard(self: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
            if self.family in (socket.AF_INET, socket.AF_INET6):
                host = address[0] if isinstance(address, tuple) else address
                if not _is_local(host):
                    raise NetworkBlocked("network access is blocked during transcription")
            return method(self, address, *args, **kwargs)

        return guard

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _is_local(host):
            raise NetworkBlocked("network access is blocked during transcription")
        return original_getaddrinfo(host, *args, **kwargs)

    for name, method in originals.items():
        setattr(socket.socket, name, blocked(method))
    socket.getaddrinfo = getaddrinfo
    for name in OFFLINE_VARIABLES:
        os.environ[name] = "1"
    try:
        yield
    finally:
        for name, saved in own.items():
            if saved is None:
                delattr(socket.socket, name)
            else:
                setattr(socket.socket, name, saved)
        socket.getaddrinfo = original_getaddrinfo
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def transcribe_all(transcriber: Transcriber, files: Iterable[Path]) -> dict[Path, Outcome]:
    """Transcribe each file with the network blocked; one failure never stops the rest."""
    outcomes: dict[Path, Outcome] = {}
    for audio in files:
        try:
            with offline_only():
                outcomes[audio] = Outcome(transcript=transcriber.transcribe(audio))
        except NetworkBlocked as error:
            outcomes[audio] = Outcome(error=f"transcription tried to use the network: {error}")
        except Exception as error:  # an engine can fail in many ways; report, don't abort
            outcomes[audio] = Outcome(error=str(error) or type(error).__name__)
    return outcomes


# --- Backends -------------------------------------------------------------------------------


class WhisperCppTranscriber:
    """Runs a local whisper.cpp `whisper-cli` on a local model file."""

    def __init__(
        self,
        model_path: PathLike,
        executable: PathLike = "whisper-cli",
        language: Optional[str] = None,
        ffmpeg: str = "ffmpeg",
        timeout: int = 900,
    ) -> None:
        self.model = Path(model_path).expanduser()
        if not self.model.is_file():
            raise TranscriptionUnavailable(
                f"Speech-to-text model not found: {self.model}. Chattr never downloads models; "
                "put a whisper.cpp model file there."
            )
        found = shutil.which(str(executable))
        if found is None:
            raise TranscriptionUnavailable(
                f"The whisper.cpp program '{executable}' was not found. Install whisper.cpp "
                "and set services.voicemail.preferences.transcription.executable."
            )
        self.executable = found
        self.language = language or None
        self.ffmpeg = ffmpeg
        self.timeout = timeout

    def transcribe(self, audio: Path) -> Transcript:
        ffmpeg = shutil.which(self.ffmpeg)
        if ffmpeg is None:
            raise TranscriptionUnavailable(
                "ffmpeg is required to prepare audio for whisper.cpp but was not found."
            )
        with tempfile.TemporaryDirectory(prefix="chattr-stt-") as folder:
            prepared = Path(folder) / "audio.wav"
            converted = subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(audio), "-ar", "16000",
                 "-ac", "1", "-c:a", "pcm_s16le", str(prepared)],
                capture_output=True,
                text=True,
                check=False,
            )  # fmt: skip
            if converted.returncode != 0 or not prepared.is_file():
                raise TranscriptionError(
                    f"Could not read the audio {audio.name}: {converted.stderr.strip()[:200]}"
                )
            command = [self.executable, "-m", str(self.model), "-f", str(prepared), "-nt", "-np",
                       "-l", self.language or "auto"]  # fmt: skip
            try:
                run = subprocess.run(
                    command, capture_output=True, text=True, check=False, timeout=self.timeout
                )
            except subprocess.TimeoutExpired as error:
                raise TranscriptionError(f"whisper.cpp timed out on {audio.name}") from error
        if run.returncode != 0:
            raise TranscriptionError(
                f"whisper.cpp failed on {audio.name} (exit {run.returncode}): "
                f"{run.stderr.strip()[:200]}"
            )
        text = " ".join(line.strip() for line in run.stdout.splitlines() if line.strip())
        return Transcript(text, self.language, self.model.name)


class FasterWhisperTranscriber:
    """The optional `faster-whisper` package, loading a model folder that is already on disk."""

    def __init__(
        self,
        model_dir: PathLike,
        language: Optional[str] = None,
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        try:
            import faster_whisper
        except ImportError as error:
            raise TranscriptionUnavailable(
                "The faster-whisper package is not installed (pip install faster-whisper)."
            ) from error
        self.model_dir = Path(model_dir).expanduser()
        if not self.model_dir.is_dir():
            raise TranscriptionUnavailable(
                f"Speech-to-text model folder not found: {self.model_dir}. Chattr never "
                "downloads models."
            )
        self.language = language or None
        self._model = faster_whisper.WhisperModel(
            str(self.model_dir), device=device, compute_type=compute_type, local_files_only=True
        )

    def transcribe(self, audio: Path) -> Transcript:
        segments, info = self._model.transcribe(str(audio), language=self.language)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return Transcript(text, getattr(info, "language", self.language), self.model_dir.name)


# --- Settings -------------------------------------------------------------------------------

_WHISPER_CPP = {"whisper", "whisper.cpp", "whispercpp", "whisper-cpp"}
_FASTER_WHISPER = {"faster-whisper", "faster_whisper", "fasterwhisper"}


def make_transcriber(
    settings: Settings,
    enabled: Optional[bool] = None,
    model_path: Optional[PathLike] = None,
    language: Optional[str] = None,
) -> Optional[Transcriber]:
    """The transcriber the Voicemail settings ask for, or None when transcription is off.

    Arguments override the settings (the command-line flags use them). Returns None when it is
    off or no model is configured; raises `TranscriptionUnavailable` when what is configured can't
    run.
    """
    prefix = "services.voicemail.preferences.transcription"
    on = settings.get(f"{prefix}.enabled", False) if enabled is None else enabled
    model = model_path or settings.get_expanded_path(f"{prefix}.model_path")
    if not on or not model:
        return None
    lang = language if language is not None else settings.get(f"{prefix}.language", "")
    provider = str(settings.get(f"{prefix}.provider", "whisper.cpp")).strip().lower()
    if provider in _WHISPER_CPP:
        return WhisperCppTranscriber(
            model,
            executable=settings.get_expanded_path(f"{prefix}.executable") or "whisper-cli",
            language=lang or None,
        )
    if provider in _FASTER_WHISPER:
        return FasterWhisperTranscriber(model, language=lang or None)
    raise TranscriptionUnavailable(
        f"Unknown transcription provider '{provider}'. Use 'whisper.cpp' or 'faster-whisper'."
    )
