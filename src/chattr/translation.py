"""Optional, local-first translation of message text.

Conversion never touches the network. Translation is a separate, explicit step
(`chattr-cli translate`) that sends the text of messages that need it to an OpenAI-compatible chat
endpoint and keeps the result in `messages.translation`. The endpoint is local by default, a
non-local address is refused unless the settings allow it, and the API key (if any) comes from an
environment variable, never from a file.

Which messages need translating:

- only text messages with text, in a language other than English;
- not what the author already translated: when the same author, within five minutes before or
  after, wrote an English message of about the same length (within 20%), the message is itself the
  translation's source and is left alone (the rule from the project's first version).

Language is detected by a small built-in stopword detector (English, Portuguese, Spanish, French,
German, Italian); it returns None rather than guess when the evidence is thin.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

from chattr.config import Settings

#: Distinctive common words per language. A message is judged by how many of its words appear.
_STOPWORDS: dict[str, set[str]] = {
    "en": set(
        "the and you are is will be we have to of in it that this for with do not at on was were "
        "please there here from my your our they what let us arrived dinner tomorrow family "
        "bags forget airport flight time hello mum".split()
    ),
    "pt": set(
        "não nao você voce vocês com para uma os as está estou são nós nos vamos amanhã amanha "
        "mãe obrigado obrigada muito tenho tem que de do da em no na ao um eu ele ela cheguei "
        "jantar família familia esqueças pode às seis e escola carro olá".split()
    ),
    "es": set(
        "no que de la el los las un una es está estás cómo hola gracias mañana nos vemos en con "
        "para por muy tengo casa abuelos buenos días estoy pero yo tú usted".split()
    ),
    "fr": set(
        "le la les des un une est sont nous vous je tu il elle et ne pas pour dans avec ce ça "
        "bonjour merci soir maison arrivés allons dîner".split()
    ),
    "de": set(
        "der die das und ist sind wir ich du nicht mit für auf gestern guten morgen angekommen uns "
        "geht gut es ein eine zu im den dem von".split()
    ),
    "it": set(
        "il lo la gli le un una è sono siamo ciao grazie stasera cena famiglia arrivati casa "
        "andiamo per con non che di mi ti ci e".split()
    ),
}
_HINT_CHARACTERS = {"pt": "ãõç", "es": "ñ¿¡", "fr": "çœàèêâîôû", "de": "äöüß", "it": "ìòù"}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_MIN_HITS = 2
_WINDOW_SECONDS = 300
_LENGTH_TOLERANCE = 0.2


def detect_language(text: Optional[str]) -> Optional[str]:
    """The language code when the text clearly is one of the known languages, else None."""
    words = [w.lower() for w in _WORD.findall(text or "")]
    if len(words) < 2:
        return None
    lowered = (text or "").lower()
    scores = {
        code: sum(1 for w in words if w in stop)
        + min(2, sum(lowered.count(ch) for ch in _HINT_CHARACTERS.get(code, "")))
        for code, stop in _STOPWORDS.items()
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (best, hits), runner_up = ranked[0], ranked[1][1]
    if hits < _MIN_HITS or hits <= runner_up or hits / len(words) < 0.2:
        return None
    return best


@dataclass(frozen=True)
class Candidate:
    """What the rules need to know about a message."""

    timestamp: datetime
    author: Optional[str]
    text: Optional[str]
    kind: str = "text"  # the message's content type


def _is_translation_pair(current: Candidate, other: Candidate) -> bool:
    """`other` is an English message of about the same length as `current`, close in time."""
    if abs((current.timestamp - other.timestamp).total_seconds()) > _WINDOW_SECONDS:
        return False
    if other.kind != "text" or not other.text:
        return False
    if detect_language(other.text) != "en":
        return False
    return abs(len(current.text or "") - len(other.text)) / len(other.text) < _LENGTH_TOLERANCE


def needs_translation(messages: Sequence[Candidate], index: int) -> bool:
    """Whether `messages[index]` should be sent for translation."""
    current = messages[index]
    if current.kind != "text" or not current.text or not current.text.strip():
        return False
    language = detect_language(current.text)
    if language is None or language == "en":
        return False
    # The nearest message by the same author on each side decides (others are skipped over).
    for step in (-1, 1):
        position = index + step
        while 0 <= position < len(messages):
            neighbour = messages[position]
            if neighbour.author == current.author:
                if _is_translation_pair(current, neighbour):
                    return False
                break
            position += step
    return True


# --- The endpoint ----------------------------------------------------------------------------


class TranslationError(Exception):
    """Translating one message failed (endpoint unreachable, bad reply)."""


class TranslationUnavailable(TranslationError):
    """Translation can't run at all: a setting is missing or not allowed."""


@dataclass(frozen=True)
class Translation:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0


def is_local_endpoint(endpoint: str) -> bool:
    """Whether the endpoint's host is this machine (localhost or a loopback address)."""
    host = (urlparse(endpoint).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class OpenAICompatibleTranslator:
    """Translate through `POST <endpoint>/chat/completions` (OpenAI-compatible servers)."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        target_language: str = "English",
        api_key: Optional[str] = None,
        timeout: float = 60,
    ) -> None:
        self.url = endpoint.rstrip("/") + "/chat/completions"
        self.model = model
        self.target_language = target_language
        self.api_key = api_key
        self.timeout = timeout
        # Local servers are never reached through a proxy configured for the internet.
        handlers = [] if not is_local_endpoint(endpoint) else [urllib.request.ProxyHandler({})]
        self._opener = urllib.request.build_opener(*handlers)

    def translate(self, text: str) -> Translation:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"You are an expert translator. Translate the user's message into "
                        f"{self.target_language}. Keep its meaning, tone and style, add nothing, "
                        "and return only the translation."
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise TranslationError(f"The translation server answered {error.code}.") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TranslationError(f"Could not reach the translation server: {error}") from error
        except ValueError as error:
            raise TranslationError("The translation server did not send JSON.") from error
        try:
            translated = str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as error:
            raise TranslationError("The translation server's reply had no translation.") from error
        if not translated:
            raise TranslationError("The translation server returned an empty translation.")
        usage = data.get("usage") or {}
        return Translation(
            translated,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )


def make_translator(
    settings: Settings,
    endpoint: Optional[str] = None,
    model: Optional[str] = None,
    enabled: Optional[bool] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[OpenAICompatibleTranslator]:
    """The translator the `global.ai_translation` settings ask for, or None when it is off.

    Arguments override the settings. The endpoint must be on this machine unless `allow_remote` is
    set. The API key is read only from the environment variable the settings name; a key written
    in the settings file is ignored.
    """
    prefix = "global.ai_translation"
    on = settings.get(f"{prefix}.enabled", False) if enabled is None else enabled
    chosen_model = model or settings.get(f"{prefix}.model", "")
    if not on or not chosen_model:
        return None
    url = endpoint or str(settings.get(f"{prefix}.endpoint", "") or "")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise TranslationUnavailable(f"The translation endpoint '{url}' is not a web address.")
    if not is_local_endpoint(url) and not settings.get(f"{prefix}.allow_remote", False):
        raise TranslationUnavailable(
            f"The translation endpoint {url} is not on this machine. Chattr translates locally "
            "unless you set global.ai_translation.allow_remote to true."
        )
    if settings.get(f"{prefix}.api_key"):
        print("Note: ignoring api_key in the settings; keys come from the environment only.")
    variable = str(settings.get(f"{prefix}.api_key_env_var", "") or "")
    key = (env if env is not None else os.environ).get(variable) if variable else None
    return OpenAICompatibleTranslator(
        url,
        str(chosen_model),
        target_language=str(settings.get(f"{prefix}.target_language", "English") or "English"),
        api_key=key or None,
        timeout=float(settings.get(f"{prefix}.timeout_seconds", 60) or 60),
    )
