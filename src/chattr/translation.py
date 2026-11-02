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

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

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
