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
import time
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
    """Transcribe each file with the network blocked; one failure never stops the rest.

    A transcriber that has an `expect(count)` method is told how many files are coming, so one
    that reports progress can say "3 of 40".
    """
    outcomes: dict[Path, Outcome] = {}
    files = list(files)
    expect = getattr(transcriber, "expect", None)
    if callable(expect):
        expect(len(files))
    many = getattr(transcriber, "transcribe_many", None)
    if callable(many):  # an engine that takes them all at once (the model is loaded once)
        try:
            with offline_only():
                for audio, result in many(files):
                    outcomes[audio] = (
                        Outcome(transcript=result)
                        if isinstance(result, Transcript)
                        else Outcome(error=str(result) or type(result).__name__)
                    )
        except Exception as error:  # the run itself could not go on
            for audio in files:
                outcomes.setdefault(audio, Outcome(error=str(error) or type(error).__name__))
        return outcomes
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
    """Runs a local whisper.cpp `whisper-cli` on a local model file.

    Several recordings go to one run of the program (`transcribe_many`), so the model, which for a
    large one takes longer to load than a short voicemail takes to transcribe, is loaded once and
    not once per recording. A long list is split into runs of `batch` recordings.
    """

    def __init__(
        self,
        model_path: PathLike,
        executable: PathLike = "whisper-cli",
        language: Optional[str] = None,
        ffmpeg: str = "ffmpeg",
        timeout: int = 900,
        batch: int = 100,
    ) -> None:
        self.batch = max(1, batch)
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

    def _ffmpeg(self) -> str:
        ffmpeg = shutil.which(self.ffmpeg)
        if ffmpeg is None:
            raise TranscriptionUnavailable(
                "ffmpeg is required to prepare audio for whisper.cpp but was not found."
            )
        return ffmpeg

    @staticmethod
    def _prepare(ffmpeg: str, audio: Path, prepared: Path) -> None:
        """Write `audio` as the 16 kHz mono WAV whisper.cpp reads."""
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

    def transcribe_many(
        self, files: Iterable[Path], poll: float = 0.1
    ) -> Iterator[tuple[Path, Union[Transcript, Exception]]]:
        """Transcribe the files in as few runs of whisper.cpp as possible, yielding each result
        as soon as the program has written it (so progress can be shown while a run goes on).

        A recording the run did not finish (the program failed on it, or died) is tried again
        on its own, which also gives it an error message of its own.
        """
        ffmpeg = self._ffmpeg()
        files = list(files)
        for start in range(0, len(files), self.batch):
            yield from self._run(ffmpeg, files[start : start + self.batch], poll)

    def _run(
        self, ffmpeg: str, files: list[Path], poll: float
    ) -> Iterator[tuple[Path, Union[Transcript, Exception]]]:
        with tempfile.TemporaryDirectory(prefix="chattr-stt-") as folder:
            ready: list[tuple[Path, Path, Path]] = []  # (recording, its WAV, where its text goes)
            for number, audio in enumerate(files):
                prepared = Path(folder) / f"{number:05d}.wav"
                try:
                    self._prepare(ffmpeg, audio, prepared)
                except TranscriptionError as error:
                    yield audio, error
                    continue
                ready.append((audio, prepared, Path(folder) / f"{number:05d}"))
            if len(ready) < 2:
                for audio, _, _ in ready:
                    yield audio, self._alone(audio)
                return
            command = [self.executable, "-m", str(self.model), "-nt", "-np", "-otxt",
                       "-l", self.language or "auto"]  # fmt: skip
            for _, prepared, text in ready:
                command += ["-f", str(prepared), "-of", str(text)]
            deadline = time.monotonic() + self.timeout * len(ready)
            done = 0
            with open(Path(folder) / "whisper.log", "wb") as log:
                process = subprocess.Popen(command, stdout=log, stderr=log)
                try:
                    while done < len(ready):
                        written = self._written(ready[done])
                        if written is not None:
                            time.sleep(min(poll, 0.05))  # let the program finish writing it
                            yield ready[done][0], self._transcript(written)
                            done += 1
                        elif process.poll() is not None:
                            break
                        elif time.monotonic() > deadline:
                            process.kill()
                            break
                        else:
                            time.sleep(poll)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
            for entry in ready[done:]:  # what the run left: written late, or not at all
                written = self._written(entry)
                yield entry[0], (
                    self._transcript(written) if written is not None else self._alone(entry[0])
                )

    @staticmethod
    def _written(entry: tuple[Path, Path, Path]) -> Optional[Path]:
        """The text file whisper.cpp wrote for this recording, if it has (`-of` names it; a
        version that ignores `-of` names it after the WAV)."""
        _, prepared, text = entry
        for candidate in (text.with_name(text.name + ".txt"), Path(str(prepared) + ".txt")):
            if candidate.is_file():
                return candidate
        return None

    def _transcript(self, written: Path) -> Transcript:
        lines = written.read_text(encoding="utf-8", errors="replace").splitlines()
        text = " ".join(line.strip() for line in lines if line.strip())
        return Transcript(text, self.language, self.model.name)

    def _alone(self, audio: Path) -> Union[Transcript, Exception]:
        try:
            return self.transcribe(audio)
        except Exception as error:
            return error

    def transcribe(self, audio: Path) -> Transcript:
        ffmpeg = self._ffmpeg()
        with tempfile.TemporaryDirectory(prefix="chattr-stt-") as folder:
            prepared = Path(folder) / "audio.wav"
            self._prepare(ffmpeg, audio, prepared)
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
