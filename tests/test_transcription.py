"""Local speech-to-text: one interface, a test stub, two local backends, and no network."""

from __future__ import annotations

import os
import socket
import stat
import sys
import types
from pathlib import Path

import pytest
from fixtures import FamilyExports
from fixtures.builders.media import wav

from chattr import transcription
from chattr.config import Settings
from chattr.transcription import (
    FasterWhisperTranscriber,
    NetworkBlocked,
    StubTranscriber,
    Transcript,
    TranscriptionUnavailable,
    WhisperCppTranscriber,
    make_transcriber,
    offline_only,
)

needs_ffmpeg = pytest.mark.skipif(
    not __import__("shutil").which("ffmpeg"), reason="ffmpeg is not installed"
)


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "1.amr"  # the extension lies: the payload is WAV
    path.write_bytes(wav(seconds=0.4))
    return path


# --- The interface and the stub -------------------------------------------------------------


def test_the_stub_returns_what_it_was_given(audio: Path) -> None:
    stub = StubTranscriber("hello there", language="en")
    assert stub.transcribe(audio) == Transcript("hello there", "en", "stub")
    assert stub.calls == [audio]


def test_the_stub_can_answer_per_file(tmp_path: Path) -> None:
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    a.write_bytes(wav())
    b.write_bytes(wav())
    stub = StubTranscriber({"a.wav": "first", "b.wav": "second"})
    assert [stub.transcribe(p).text for p in (a, b)] == ["first", "second"]


# --- The offline guard ----------------------------------------------------------------------


def test_the_guard_blocks_network_connections_and_restores_them() -> None:
    original = socket.socket.connect
    with offline_only():
        with pytest.raises(NetworkBlocked):
            socket.create_connection(("example.com", 80), timeout=1)
        with pytest.raises(NetworkBlocked):
            socket.socket().connect(("93.184.216.34", 80))
        assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert socket.socket.connect is original


def test_the_guard_restores_everything_after_an_error() -> None:
    original = socket.socket.connect
    with pytest.raises(RuntimeError):
        with offline_only():
            raise RuntimeError("boom")
    assert socket.socket.connect is original


def test_the_guard_does_not_block_local_sockets() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    with offline_only():
        client = socket.socket()
        client.connect(server.getsockname())  # a local connection is not "the network"
        client.close()
    server.close()


def test_the_environment_variables_are_put_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    with offline_only():
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["HF_HUB_OFFLINE"] == "0" and "TRANSFORMERS_OFFLINE" not in os.environ


# --- whisper.cpp ----------------------------------------------------------------------------


def _fake_whisper(tmp_path: Path, output: str = "hello from the model", code: int = 0) -> Path:
    script = tmp_path / "whisper-cli"
    script.write_text(
        "#!/bin/sh\n"
        'while [ $# -gt 0 ]; do case "$1" in -m) m="$2"; shift;; -f) f="$2"; shift;; '
        '-l) l="$2"; shift;; esac; shift; done\n'
        '[ -f "$m" ] || { echo "no model" >&2; exit 3; }\n'
        '[ -f "$f" ] || { echo "no audio" >&2; exit 4; }\n'
        'case "$f" in *.wav) ;; *) echo "not a wav" >&2; exit 5;; esac\n'
        f'echo "  {output}"; echo "language=$l" >&2; exit {code}\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@needs_ffmpeg
def test_whisper_cpp_converts_then_runs_the_local_cli(tmp_path: Path, audio: Path) -> None:
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"model")
    backend = WhisperCppTranscriber(model, executable=_fake_whisper(tmp_path), language="pt")
    result = backend.transcribe(audio)
    assert result.text == "hello from the model"
    assert result.language == "pt" and result.model == "ggml-base.bin"


@needs_ffmpeg
def test_whisper_cpp_detects_the_language_when_none_is_given(tmp_path: Path, audio: Path) -> None:
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    result = WhisperCppTranscriber(model, executable=_fake_whisper(tmp_path)).transcribe(audio)
    assert result.language is None


def test_whisper_cpp_needs_its_pieces(tmp_path: Path, audio: Path) -> None:
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    with pytest.raises(TranscriptionUnavailable, match="model"):
        WhisperCppTranscriber(tmp_path / "missing.bin", executable=_fake_whisper(tmp_path))
    with pytest.raises(TranscriptionUnavailable, match="whisper"):
        WhisperCppTranscriber(model, executable=tmp_path / "not-installed")
    backend = WhisperCppTranscriber(
        model, executable=_fake_whisper(tmp_path), ffmpeg="no-ffmpeg-here"
    )
    with pytest.raises(TranscriptionUnavailable, match="ffmpeg"):
        backend.transcribe(audio)


@needs_ffmpeg
def test_a_failing_cli_is_a_transcription_error(tmp_path: Path, audio: Path) -> None:
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    backend = WhisperCppTranscriber(model, executable=_fake_whisper(tmp_path, code=1))
    with pytest.raises(transcription.TranscriptionError):
        backend.transcribe(audio)


@needs_ffmpeg
def test_unreadable_audio_is_a_transcription_error(tmp_path: Path) -> None:
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    junk = tmp_path / "junk.amr"
    junk.write_bytes(b"this is not audio")
    with pytest.raises(transcription.TranscriptionError):
        WhisperCppTranscriber(model, executable=_fake_whisper(tmp_path)).transcribe(junk)


# --- faster-whisper -------------------------------------------------------------------------


def _fake_faster_whisper(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    seen: list[tuple] = []

    class Segment:
        def __init__(self, text: str) -> None:
            self.text = text

    class Info:
        language = "en"

    class WhisperModel:
        def __init__(self, path: str, **kwargs: object) -> None:
            seen.append(("load", path, kwargs))

        def transcribe(self, audio: str, **kwargs: object):
            seen.append(("transcribe", audio, kwargs))
            return iter([Segment(" first part"), Segment(" second part")]), Info()

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return seen


def test_faster_whisper_loads_only_a_local_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audio: Path
) -> None:
    seen = _fake_faster_whisper(monkeypatch)
    folder = tmp_path / "model"
    folder.mkdir()
    result = FasterWhisperTranscriber(folder).transcribe(audio)
    assert result == Transcript("first part second part", "en", "model")
    assert seen[0][2]["local_files_only"] is True  # never fetches weights
    assert seen[1][1] == str(audio)  # it decodes the file itself, whatever the extension


def test_faster_whisper_errors_are_clear(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # as if not installed
    folder = tmp_path / "model"
    folder.mkdir()
    with pytest.raises(TranscriptionUnavailable, match="faster-whisper"):
        FasterWhisperTranscriber(folder)
    _fake_faster_whisper(monkeypatch)
    with pytest.raises(TranscriptionUnavailable, match="model"):
        FasterWhisperTranscriber(tmp_path / "missing")


# --- Settings -------------------------------------------------------------------------------


def _settings(chattr_root: Path, **preferences: object) -> Settings:
    import json

    path = chattr_root / "Services" / "Voicemail.json"
    data = json.loads(path.read_text())
    data["preferences"]["transcription"].update(preferences)
    path.write_text(json.dumps(data))
    return Settings.load(chattr_root)


def test_disabled_or_unconfigured_gives_no_transcriber(chattr_root: Path) -> None:
    assert make_transcriber(Settings.load(chattr_root)) is None
    assert make_transcriber(_settings(chattr_root, enabled=True, model_path="")) is None


def test_an_unknown_provider_is_an_error(chattr_root: Path, tmp_path: Path) -> None:
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    settings = _settings(chattr_root, enabled=True, provider="cloud-api", model_path=str(model))
    with pytest.raises(TranscriptionUnavailable, match="provider"):
        make_transcriber(settings)


def test_the_factory_builds_the_configured_backend(chattr_root: Path, tmp_path: Path) -> None:
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    settings = _settings(
        chattr_root,
        enabled=True,
        provider="whisper.cpp",
        model_path=str(model),
        executable=str(_fake_whisper(tmp_path)),
        language="en",
    )
    backend = make_transcriber(settings)
    assert isinstance(backend, WhisperCppTranscriber) and backend.language == "en"
    legacy = _settings(chattr_root, provider="whisper")  # the name older defaults used
    assert isinstance(make_transcriber(legacy), WhisperCppTranscriber)


def test_transcribe_all_runs_offline_and_isolates_failures(tmp_path: Path) -> None:
    good, bad = tmp_path / "good.wav", tmp_path / "bad.wav"
    good.write_bytes(wav())
    bad.write_bytes(wav())

    class Backend:
        def transcribe(self, audio: Path) -> Transcript:
            socket.create_connection(("example.com", 80), timeout=1)  # a backend that phones home
            return Transcript("never", None, "x")

    outcomes = transcription.transcribe_all(Backend(), [good, bad])
    assert all(o.transcript is None for o in outcomes.values())
    assert all("network" in (o.error or "").lower() for o in outcomes.values())

    stub = StubTranscriber({"good.wav": "fine"})
    outcomes = transcription.transcribe_all(stub, [good])
    assert outcomes[good].transcript and outcomes[good].transcript.text == "fine"


def test_the_fixture_voicemails_transcribe_with_the_network_off(
    fixture_family: FamilyExports,
) -> None:
    files = sorted(fixture_family.paths["iphone_voicemail"].glob("*.amr"))
    assert len(files) == 2

    class Offline:
        def transcribe(self, audio: Path) -> Transcript:
            with pytest.raises(NetworkBlocked):
                socket.create_connection(("example.com", 80), timeout=1)
            return Transcript(f"heard {audio.name}", "en", "offline-test")

    outcomes = transcription.transcribe_all(Offline(), files)
    assert [o.transcript.text for o in outcomes.values() if o.transcript] == [
        f"heard {f.name}" for f in files
    ]


# --- Many recordings in one whisper.cpp run -----------------------------------------------------------


def _batch_whisper(tmp_path: Path) -> Path:
    """A stand-in `whisper-cli` that takes many `-f`/`-of` pairs, like the real one: it "loads the
    model" once per run (a line in runs.log) and writes `<of>.txt` for each recording.
    $FAKE_SKIP: the index of a recording it fails on. $FAKE_CRASH: the index it dies at."""
    script = tmp_path / "whisper-cli"
    script.write_text(
        "#!" + sys.executable + "\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "a = sys.argv[1:]\n"
        "files = [a[i + 1] for i, x in enumerate(a) if x == '-f']\n"
        "outs = [a[i + 1] for i, x in enumerate(a) if x == '-of']\n"
        "model = a[a.index('-m') + 1]\n"
        "assert Path(model).is_file()\n"
        "if '-otxt' not in a:  # one recording, answered on standard output\n"
        "    with open(Path(model).parent / 'runs.log', 'a') as log:\n"
        "        log.write('1\\n')\n"
        "    if os.environ.get('FAKE_ALONE_FAILS'):\n"
        "        sys.stderr.write('cannot decode'); sys.exit(2)\n"
        "    print('  words alone'); sys.exit(0)\n"
        "assert len(files) == len(outs)\n"
        "with open(Path(model).parent / 'runs.log', 'a') as log:\n"
        "    log.write(f'{len(files)}\\n')\n"
        "skip, crash = os.environ.get('FAKE_SKIP'), os.environ.get('FAKE_CRASH')\n"
        "failed = 0\n"
        "for n, (f, out) in enumerate(zip(files, outs)):\n"
        "    assert f.endswith('.wav') and Path(f).is_file()\n"
        "    if crash is not None and n == int(crash) and len(files) > 1:\n"
        "        os._exit(139)\n"
        "    if skip is not None and n == int(skip) and len(files) > 1:\n"
        "        failed += 1\n"
        "        continue\n"
        "    Path(out + '.txt').write_text(f' words {n} of {len(files)}\\n')\n"
        "sys.exit(11 if failed else 0)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _recordings(tmp_path: Path, count: int) -> list[Path]:
    made = []
    for n in range(count):
        path = tmp_path / f"{n}.amr"
        path.write_bytes(wav(seconds=0.2))
        made.append(path)
    return made


def _batch_backend(tmp_path: Path, **options: object) -> WhisperCppTranscriber:
    model = tmp_path / "ggml-large-v3-turbo.bin"
    model.write_bytes(b"model")
    return WhisperCppTranscriber(model, executable=_batch_whisper(tmp_path), **options)


@needs_ffmpeg
def test_many_recordings_load_the_model_once(tmp_path: Path) -> None:
    files = _recordings(tmp_path, 5)
    backend = _batch_backend(tmp_path, language="pt")
    outcomes = transcription.transcribe_all(backend, files)
    assert [outcomes[f].transcript.text for f in files] == [f"words {n} of 5" for n in range(5)]
    assert outcomes[files[0]].transcript.model == "ggml-large-v3-turbo.bin"
    assert (tmp_path / "runs.log").read_text().split() == ["5"]  # one run, not five


@needs_ffmpeg
def test_results_arrive_one_by_one_while_the_run_goes_on(tmp_path: Path) -> None:
    files = _recordings(tmp_path, 3)
    seen = [audio.name for audio, _ in _batch_backend(tmp_path).transcribe_many(files)]
    assert seen == ["0.amr", "1.amr", "2.amr"]


@needs_ffmpeg
def test_a_long_list_is_split_into_runs_of_a_set_size(tmp_path: Path) -> None:
    files = _recordings(tmp_path, 5)
    outcomes = transcription.transcribe_all(_batch_backend(tmp_path, batch=2), files)
    assert all(o.transcript for o in outcomes.values())
    assert (tmp_path / "runs.log").read_text().split() == ["2", "2", "1"]


@needs_ffmpeg
def test_one_recording_whisper_fails_on_does_not_cost_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SKIP", "1")
    monkeypatch.setenv("FAKE_ALONE_FAILS", "1")
    files = _recordings(tmp_path, 3)
    outcomes = transcription.transcribe_all(_batch_backend(tmp_path), files)
    assert outcomes[files[0]].transcript and outcomes[files[2]].transcript
    assert outcomes[files[1]].transcript is None and "1.amr" in (outcomes[files[1]].error or "")


@needs_ffmpeg
def test_a_run_that_dies_part_way_is_finished_one_recording_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CRASH", "1")
    files = _recordings(tmp_path, 3)
    outcomes = transcription.transcribe_all(_batch_backend(tmp_path), files)
    assert [bool(outcomes[f].transcript) for f in files] == [True, True, True]
    assert (tmp_path / "runs.log").read_text().split() == ["3", "1", "1"]


@needs_ffmpeg
def test_audio_that_cannot_be_read_is_reported_and_the_rest_go_on(tmp_path: Path) -> None:
    files = _recordings(tmp_path, 2)
    junk = tmp_path / "junk.amr"
    junk.write_bytes(b"this is not audio")
    outcomes = transcription.transcribe_all(_batch_backend(tmp_path), [files[0], junk, files[1]])
    assert outcomes[junk].transcript is None and "Could not read" in (outcomes[junk].error or "")
    assert outcomes[files[0]].transcript and outcomes[files[1]].transcript
    assert (tmp_path / "runs.log").read_text().split() == ["2"]
