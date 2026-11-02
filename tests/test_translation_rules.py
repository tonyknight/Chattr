"""When a message needs translating: language detection and the author-already-translated rule."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pytest

from chattr.translation import Candidate, detect_language, needs_translation

T0 = datetime(2024, 1, 1, 12, 0, 0)


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("I will be there at six, please do not forget the bags.", "en"),
        ("Hello Mum, we are at the airport and the flight is on time.", "en"),
        ("Cheguei! Vamos jantar amanhã com a família, não te esqueças.", "pt"),
        ("Mãe, você pode me buscar às seis? Estou na escola e não tenho carro.", "pt"),
        ("Hola, ¿cómo estás? Nos vemos mañana en la casa de los abuelos.", "es"),
        ("Bonjour, nous sommes arrivés à la maison et nous allons dîner ce soir.", "fr"),
        ("Guten Morgen, wir sind gestern angekommen und es geht uns gut.", "de"),
        ("Ciao, siamo arrivati a casa e stasera andiamo a cena con la famiglia.", "it"),
    ],
)
def test_clear_sentences_are_recognised(text: str, language: str) -> None:
    assert detect_language(text) == language


@pytest.mark.parametrize(
    "text", ["", "   ", "ok", "👍", "12345", "Ana", "haha", "https://example.org/x"]
)
def test_too_little_to_tell_is_none(text: str) -> None:
    assert detect_language(text) is None


def test_mixed_or_unknown_text_is_none_not_a_guess() -> None:
    assert detect_language("Zxqv wprt lkjh mnbv qwerty asdfg zxcvb") is None


def _msg(minutes: float, author: str, text: Optional[str], kind: str = "text") -> Candidate:
    return Candidate(T0 + timedelta(minutes=minutes), author, text, kind)


PT = "Cheguei! Vamos jantar amanhã com a família, não te esqueças."
EN = "I arrived! Let us have dinner tomorrow with the family, do not forget."


def test_a_portuguese_message_alone_needs_translation() -> None:
    assert needs_translation([_msg(0, "A", PT)], 0)


def test_english_and_unknown_text_do_not() -> None:
    assert not needs_translation([_msg(0, "A", EN)], 0)
    assert not needs_translation([_msg(0, "A", "ok")], 0)


@pytest.mark.parametrize("kind", ["media", "system", "call", "voicemail", "email"])
def test_only_text_messages_are_translated(kind: str) -> None:
    assert not needs_translation([_msg(0, "A", PT, kind)], 0)


def test_empty_bodies_do_not() -> None:
    assert not needs_translation([_msg(0, "A", None)], 0)
    assert not needs_translation([_msg(0, "A", "")], 0)


def test_the_author_having_already_translated_it_afterwards_is_skipped() -> None:
    chat = [_msg(0, "A", PT), _msg(1, "A", EN)]
    assert not needs_translation(chat, 0)


def test_and_beforehand_too() -> None:
    chat = [_msg(0, "A", EN), _msg(1, "A", PT)]
    assert not needs_translation(chat, 1)


@pytest.mark.parametrize(("seconds", "skipped"), [(299, True), (300, True), (301, False)])
def test_the_window_is_five_minutes(seconds: int, skipped: bool) -> None:
    chat = [_msg(0, "A", PT), Candidate(T0 + timedelta(seconds=seconds), "A", EN, "text")]
    assert needs_translation(chat, 0) is (not skipped)


def _english(length: int) -> str:
    """English text of exactly `length` characters."""
    phrase = "We will be there at six and we will bring all of the bags with us. "
    return (phrase * (length // len(phrase) + 1))[:length].rstrip() + "." * 0 if length else ""


def test_a_much_shorter_or_longer_english_message_is_not_a_translation() -> None:
    far = "Dinner tomorrow."
    assert needs_translation([_msg(0, "A", PT), _msg(1, "A", far)], 0)
    assert needs_translation([_msg(0, "A", PT), _msg(1, "A", _english(len(PT) * 3))], 0)


def test_the_length_tolerance_is_twenty_percent_of_the_other_message() -> None:
    length = len(PT)
    inside_short = _english(int(length / 1.2) + 1)  # (L - M) / M just under 0.2
    inside_long = _english(int(length / 0.8) - 1)  # (M - L) / M just under 0.2
    outside_short = _english(int(length / 1.2) - 1)
    outside_long = _english(int(length / 0.8) + 2)
    for text in (inside_short, inside_long, outside_short, outside_long):
        assert detect_language(text) == "en" and len(text) == len(text.rstrip())
    assert not needs_translation([_msg(0, "A", PT), _msg(1, "A", inside_short)], 0)
    assert not needs_translation([_msg(0, "A", PT), _msg(1, "A", inside_long)], 0)
    assert needs_translation([_msg(0, "A", PT), _msg(1, "A", outside_short)], 0)
    assert needs_translation([_msg(0, "A", PT), _msg(1, "A", outside_long)], 0)


def test_another_authors_message_does_not_count() -> None:
    assert needs_translation([_msg(0, "A", PT), _msg(1, "B", EN)], 0)


def test_the_nearest_message_by_the_same_author_decides() -> None:
    # B's message sits between: the rule looks at the next message *by A*, and stops at A's first
    chat = [
        _msg(0, "A", PT),
        _msg(0.5, "B", "Sure."),
        _msg(1, "A", "Another thought entirely, long enough to count."),
    ]
    assert needs_translation(chat, 0)


def test_a_non_english_neighbour_is_not_a_translation() -> None:
    other_pt = "Cheguei! Vamos jantar amanhã com a família, não te esqueças dela."
    assert needs_translation([_msg(0, "A", PT), _msg(1, "A", other_pt)], 0)
