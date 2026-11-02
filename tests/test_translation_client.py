"""The translation client: an OpenAI-compatible endpoint, local by default, key from the environment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from stub_server import StubServer

from chattr.config import Settings
from chattr.translation import (
    OpenAICompatibleTranslator,
    TranslationError,
    TranslationUnavailable,
    make_translator,
)


@pytest.fixture
def server() -> Iterator[StubServer]:
    srv = StubServer()
    yield srv
    srv.close()


def test_a_request_goes_to_chat_completions_with_the_text_and_the_model(server: StubServer) -> None:
    translator = OpenAICompatibleTranslator(server.endpoint, "llama3", target_language="English")
    result = translator.translate("Cheguei! Vamos jantar.")
    assert result.text == "I arrived! Let us have dinner."
    assert (result.tokens_in, result.tokens_out) == (31, 9)
    (request,) = server.requests
    assert request["path"] == "/v1/chat/completions"
    body = request["json"]
    assert body["model"] == "llama3" and body["temperature"] == 0
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "English" in body["messages"][0]["content"]
    assert body["messages"][1]["content"] == "Cheguei! Vamos jantar."


def test_a_trailing_slash_on_the_endpoint_is_fine(server: StubServer) -> None:
    OpenAICompatibleTranslator(server.endpoint + "/", "m").translate("x y z")
    assert server.requests[0]["path"] == "/v1/chat/completions"


def test_the_key_is_sent_only_when_there_is_one(server: StubServer) -> None:
    OpenAICompatibleTranslator(server.endpoint, "m").translate("a b c")
    OpenAICompatibleTranslator(server.endpoint, "m", api_key="sekret").translate("a b c")
    without, with_key = server.requests
    assert "Authorization" not in without["headers"]
    assert with_key["headers"]["Authorization"] == "Bearer sekret"


@pytest.mark.parametrize(
    ("status", "raw", "reply"),
    [
        (500, None, {"error": "boom"}),
        (200, b"not json at all", None),
        (200, None, {"unexpected": True}),
        (200, None, {"choices": []}),
        (200, None, {"choices": [{"message": {}}]}),
        (200, None, {"choices": [{"message": {"content": "   "}}]}),
    ],
)
def test_bad_replies_are_translation_errors(
    server: StubServer, status: int, raw: bytes | None, reply: Any
) -> None:
    server.status, server.raw = status, raw
    if reply is not None:
        server.reply = reply
    with pytest.raises(TranslationError):
        OpenAICompatibleTranslator(server.endpoint, "m").translate("hello there friend")


def test_a_server_that_is_not_there_is_a_translation_error() -> None:
    with pytest.raises(TranslationError, match="reach"):
        OpenAICompatibleTranslator("http://127.0.0.1:9/v1", "m", timeout=2).translate(
            "one two three"
        )


def test_usage_is_optional(server: StubServer) -> None:
    server.reply = {"choices": [{"message": {"content": "done"}}]}
    result = OpenAICompatibleTranslator(server.endpoint, "m").translate("a b c")
    assert (result.tokens_in, result.tokens_out) == (0, 0)


# --- Settings -------------------------------------------------------------------------------


def _settings(chattr_root: Path, **values: object) -> Settings:
    path = chattr_root / "config.json"
    data = json.loads(path.read_text())
    data["global"]["ai_translation"].update(values)
    path.write_text(json.dumps(data))
    return Settings.load(chattr_root)


def test_the_defaults_are_local_and_off(chattr_root: Path) -> None:
    settings = Settings.load(chattr_root)
    assert settings.get("global.ai_translation.enabled") is False
    assert settings.get("global.ai_translation.endpoint") == "http://localhost:11434/v1"
    assert make_translator(settings) is None


def test_no_model_means_no_translator(chattr_root: Path) -> None:
    assert make_translator(_settings(chattr_root, enabled=True, model="")) is None


def test_a_local_endpoint_is_built_from_the_settings(chattr_root: Path, server: StubServer) -> None:
    settings = _settings(
        chattr_root,
        enabled=True,
        model="llama3",
        endpoint=server.endpoint,
        target_language="French",
    )
    translator = make_translator(settings, env={})
    assert isinstance(translator, OpenAICompatibleTranslator)
    translator.translate("hello there friend")
    assert "French" in server.requests[0]["json"]["messages"][0]["content"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://openrouter.ai/api/v1",
        "http://192.168.1.20:11434/v1",
        "http://example.com/v1",
        "http://localhost.evil.example/v1",
    ],
)
def test_a_non_local_endpoint_is_refused_unless_allowed(chattr_root: Path, endpoint: str) -> None:
    settings = _settings(chattr_root, enabled=True, model="m", endpoint=endpoint)
    with pytest.raises(TranslationUnavailable, match="allow_remote"):
        make_translator(settings)
    allowed = _settings(chattr_root, allow_remote=True)
    assert make_translator(allowed, env={}) is not None


@pytest.mark.parametrize(
    "endpoint", ["http://localhost:1234/v1", "http://127.0.0.1:8080/v1", "http://[::1]:8080/v1"]
)
def test_loopback_endpoints_are_local(chattr_root: Path, endpoint: str) -> None:
    assert make_translator(
        _settings(chattr_root, enabled=True, model="m", endpoint=endpoint), env={}
    )


def test_the_key_comes_from_the_environment_only(chattr_root: Path, server: StubServer) -> None:
    settings = _settings(
        chattr_root,
        enabled=True,
        model="m",
        endpoint=server.endpoint,
        api_key="from-the-file",  # must be ignored
        api_key_env_var="MY_TRANSLATION_KEY",
    )
    make_translator(settings, env={}).translate("a b c")  # type: ignore[union-attr]
    make_translator(settings, env={"MY_TRANSLATION_KEY": "from-the-environment"}).translate("a b c")  # type: ignore[union-attr]
    first, second = server.requests
    assert "Authorization" not in first["headers"]
    assert second["headers"]["Authorization"] == "Bearer from-the-environment"
    assert "from-the-file" not in json.dumps(server.requests)


def test_a_bad_endpoint_value_is_unavailable_not_a_crash(chattr_root: Path) -> None:
    with pytest.raises(TranslationUnavailable):
        make_translator(_settings(chattr_root, enabled=True, model="m", endpoint="not a url"))
