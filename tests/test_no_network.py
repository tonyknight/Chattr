"""N2: conversion never uses the network (translation is a separate, explicit step)."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fixtures import FamilyExports

from chattr import commands
from chattr.config import Settings
from chattr.database_manager import DatabaseManager
from chattr.pages import render_pages
from chattr.render import Renderer


def test_every_service_converts_and_renders_with_the_network_cut(
    chattr_root: Path,
    fixture_family: FamilyExports,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("conversion touched the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    settings = Settings.load(chattr_root)
    paths = fixture_family.paths
    commands.convert_imessage(
        settings,
        db_path=paths["imessage"],
        attachment_root=paths["imessage"].parent / "Attachments",
        spam_mode="disabled",
        workers=1,
    )
    commands.convert(
        settings, "whatsapp", paths["whatsapp"] / "WhatsApp Chat - Ana Costa" / "_chat.txt"
    )
    commands.convert(settings, "messenger", paths["messenger"])
    commands.convert(settings, "instagram", paths["instagram"])
    commands.convert(settings, "mail", paths["mbox"])
    commands.convert(settings, "googlevoice", paths["google_voice"])
    commands.convert(settings, "voicemail", paths["iphone_voicemail"])
    with DatabaseManager(commands.central_database(settings)) as db:
        assert render_pages(Renderer(db), tmp_path / "site")
