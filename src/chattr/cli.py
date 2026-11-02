"""`chattr-cli`: Chattr's step-by-step menu and its non-interactive subcommands.

Both call the same functions in `chattr.commands`, so anything the menu does can be scripted:

    chattr-cli [--root DIR] convert <service> [export] [--db | --json] [--html] ...
    chattr-cli [--root DIR] html [--out DIR] [--attachments-dir DIR]
    chattr-cli [--root DIR] people [--contacts F] [--aliases F] [--owner NAME] [--json]
    chattr-cli [--root DIR] rebuild [--from DIR] [--db FILE]
    chattr-cli [--root DIR] chats [chat.db]
    chattr-cli [--root DIR] doctor [chat.db] [--check info|connection|spam]

With no command, the menu runs.
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional, Sequence

from dotenv import load_dotenv

from chattr import __version__, commands
from chattr.config import ConfigurationError, Settings, load_settings
from chattr.preflight import run_preflight
from chattr.transcription import Transcriber, TranscriptionUnavailable, make_transcriber


def database_path(settings: Settings) -> Path:
    """The central database for this Chattr root: `<root>/Database/chattr.sqlite`."""
    return commands.central_database(settings)


IMESSAGE_ONLY = (
    "attachment_root",
    "batch_size",
    "attachment_workers",
    "workers",
    "spam_mode",
    "chat",
    "limit",
    "verbose",
)
VOICEMAIL_ONLY = ("transcribe", "model", "language")
#: Options that only make sense when iMessage goes into the central archive.
CENTRAL_ONLY_FOR_IMESSAGE = ("html", "aliases", "owner")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chattr-cli",
        description="Convert message exports into readable archives. "
        "With no command, the step-by-step menu runs.",
    )
    parser.add_argument("--version", action="version", version=f"chattr {__version__}")
    parser.add_argument("--root", help="Chattr data folder (skips the first-run prompt)")
    sub = parser.add_subparsers(dest="command", metavar="command")

    convert = sub.add_parser("convert", help="convert one export")
    convert.add_argument(
        "service",
        type=str.lower,
        choices=[
            "whatsapp",
            "messenger",
            "instagram",
            "imessage",
            "mail",
            "googlevoice",
            "voicemail",
        ],
    )
    convert.add_argument(
        "export",
        nargs="?",
        help="the export file (iMessage: chat.db, default: configured; Google Voice: the folder)",
    )
    route = convert.add_mutually_exclusive_group()
    route.add_argument(
        "--db",
        dest="route",
        action="store_const",
        const="central",
        help="import into the central archive (the default)",
    )
    route.add_argument(
        "--json",
        dest="route",
        action="store_const",
        const="service",
        help="write JSON to the service's output folder instead",
    )
    convert.add_argument("--html", action="store_true", help="also write an HTML page")
    convert.add_argument("--owner", help="the archive owner's name (their messages are 'sent')")
    convert.add_argument("--aliases", help="JSON file of {identifier or name: person name}")
    convert.add_argument("--contacts", help="vCard file that names the people behind phone numbers")
    convert.add_argument("--region", help="phone number region for contacts (default: US)")
    convert.add_argument(
        "--attachments-dir",
        help="also copy media into this archive folder, renamed and dated "
        "(iMessage: with --json)",
    )
    imessage = convert.add_argument_group("iMessage options")
    imessage.add_argument("--attachment-root", help="folder holding the original attachments")
    imessage.add_argument("--batch-size", type=int, help="messages per batch")
    imessage.add_argument("--attachment-workers", type=int, help="parallel attachment workers")
    imessage.add_argument("--workers", type=int, help="parallel message workers")
    imessage.add_argument("--spam-mode", choices=commands.SPAM_MODES, help="spam filtering")
    imessage.add_argument("--chat", type=int, help="only this chat ID (see `chats`)")
    imessage.add_argument("--limit", type=int, help="at most this many messages")
    imessage.add_argument("--verbose", action="store_true", help="print every message")

    voicemail = convert.add_argument_group("voicemail options")
    voicemail.add_argument(
        "--transcribe", action="store_true", help="transcribe the audio locally (no network)"
    )
    voicemail.add_argument("--model", help="local speech-to-text model (file or folder)")
    voicemail.add_argument("--language", help="spoken language code, such as en (default: detect)")

    html = sub.add_parser(
        "html", help="render the central archive as pages: one per person (or group) per year"
    )
    html.add_argument("--out", help="output folder (default: Output/Chattr)")
    html.add_argument(
        "--attachments-dir",
        action="append",
        default=[],
        help="a folder to link attachment files from (repeatable)",
    )

    people = sub.add_parser(
        "people", help="link handles to people (contacts, aliases) and list those still unnamed"
    )
    people.add_argument("--contacts", help="vCard file that names the people behind phone numbers")
    people.add_argument("--aliases", help="JSON file of {identifier or name: person name}")
    people.add_argument("--owner", help="the archive owner's name")
    people.add_argument("--region", help="phone number region for contacts (default: US)")
    people.add_argument("--json", action="store_true", help="print the unresolved handles as JSON")

    rebuild = sub.add_parser(
        "rebuild", help="recreate chattr.sqlite from the rendered pages (their data islands)"
    )
    rebuild.add_argument("--from", dest="site", help="the pages' folder (default: Output/Chattr)")
    rebuild.add_argument("--db", help="the database to create (default: Database/chattr.sqlite)")

    chats = sub.add_parser("chats", help="list the chats in an iMessage database")
    chats.add_argument("database", nargs="?", help="chat.db (default: configured)")

    doctor = sub.add_parser("doctor", help="check an iMessage database")
    doctor.add_argument("database", nargs="?", help="chat.db (default: configured)")
    doctor.add_argument(
        "--check",
        action="append",
        choices=commands.DOCTOR_CHECKS,
        help="run only this check (repeatable; default: all)",
    )
    for command in (convert, html, people, rebuild, chats, doctor):
        # `--root` works before or after the command; SUPPRESS keeps an earlier value.
        command.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    return parser


def _check_convert_options(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Reject options that don't apply to the chosen service, before anything runs."""

    def given(name: str) -> bool:
        return getattr(args, name) not in (None, False)

    def flag(name: str) -> str:
        return "--db/--json" if name == "route" else "--" + name.replace("_", "-")

    if args.service == "imessage":
        if args.route == "service":
            wrong = [flag(n) for n in CENTRAL_ONLY_FOR_IMESSAGE if given(n)]
            if wrong:
                parser.error(
                    f"{', '.join(wrong)} need the central archive; --json writes iMessage's "
                    "native JSON only."
                )
        elif args.attachments_dir:
            parser.error(
                "--attachments-dir copies attachments with --json; the central archive "
                "doesn't archive attachment files yet."
            )
        return
    wrong = [flag(n) for n in IMESSAGE_ONLY if given(n)]
    if wrong:
        parser.error(f"{', '.join(wrong)} applies to iMessage only.")
    wrong = [flag(n) for n in VOICEMAIL_ONLY if given(n)]
    if wrong and args.service != "voicemail":
        parser.error(f"{', '.join(wrong)} applies to voicemail only.")
    if not args.export:
        parser.error(f"convert {args.service}: give the export file to convert.")


def _configured_chat_db(settings: Settings) -> str:
    return settings.get_expanded_path("services.imessage.preferences.database.default_db_path")


def _print_convert_result(settings: Settings, result: commands.ConvertResult, route: str) -> None:
    counts = result.counts
    where = commands.central_database(settings) if route == "central" else result.json_path
    print(
        f"{result.service}: {counts.inserted:,} new, {counts.updated:,} updated, "
        f"{counts.unchanged:,} already archived → {where}"
    )
    if result.html_path:
        print(f"HTML: {result.html_path}")
    _print_unresolved_note(result)


def _print_unresolved_note(result: commands.ConvertResult) -> None:
    if result.unresolved:
        print(
            f"{result.unresolved} {result.service} handle(s) have no person yet: "
            "run `chattr-cli people` to see them."
        )


def _print_imessage_result(result: commands.ConvertResult) -> None:
    print(f"iMessage: {result.counts.total:,} messages → {result.json_path}")


def _print_chats(database: str) -> None:
    for chat_id, name in commands.list_chats(database):
        print(f"{chat_id:>5}  {name}")


def _transcriber(settings: Settings, args: argparse.Namespace) -> Optional[Transcriber]:
    """The local transcriber the flags and settings ask for (None: leave voicemail untranscribed)."""
    wanted = bool(args.transcribe or args.model)
    try:
        transcriber = make_transcriber(
            settings,
            enabled=True if wanted else None,
            model_path=args.model,
            language=args.language,
        )
    except TranscriptionUnavailable as error:
        raise commands.CommandError(str(error)) from error
    if wanted and transcriber is None:
        raise commands.CommandError(
            "No speech-to-text model is set. Give --model PATH, or set "
            "services.voicemail.preferences.transcription.model_path."
        )
    return transcriber


def _run_convert(settings: Settings, args: argparse.Namespace) -> int:
    if args.service == "imessage":
        route = args.route or "central"
        result = commands.convert_imessage(
            settings,
            db_path=args.export,
            contacts=args.contacts,
            region=args.region,
            attachment_root=args.attachment_root,
            batch_size=args.batch_size,
            attachment_workers=args.attachment_workers,
            workers=args.workers,
            spam_mode=args.spam_mode,
            chat=args.chat,
            limit=args.limit,
            verbose=args.verbose,
            attachments_dir=args.attachments_dir,
            route=route,
            html=args.html,
            owner=args.owner,
            aliases=args.aliases,
        )
        if route == "central":
            _print_convert_result(settings, result, route)
        else:
            _print_imessage_result(result)
        return 0
    route = args.route or "central"
    transcriber = None
    if args.service == "voicemail":
        transcriber = _transcriber(settings, args)
    result = commands.convert(
        settings,
        args.service,
        args.export,
        route=route,
        html=args.html,
        owner=args.owner,
        aliases=args.aliases,
        attachments_dir=args.attachments_dir,
        transcriber=transcriber,
        contacts=args.contacts,
        region=args.region,
    )
    _print_convert_result(settings, result, route)
    return 0


def _run_html(settings: Settings, args: argparse.Namespace) -> int:
    path = commands.generate_html(settings, out=args.out, attachments_dirs=args.attachments_dir)
    if path is None:
        print("The central archive is empty: convert an export first.")
        return 1
    print(f"HTML: {path}")
    return 0


def _run_people(settings: Settings, args: argparse.Namespace) -> int:
    result = commands.people(
        settings,
        owner=args.owner,
        aliases=args.aliases,
        contacts=args.contacts,
        region=args.region,
    )
    if args.json:
        print(json.dumps([asdict(u) for u in result.unresolved], indent=2, ensure_ascii=False))
        return 0
    if not result.unresolved:
        print("Every handle in the archive has a person.")
        return 0
    print(f"{len(result.unresolved)} handle(s) have no person yet (busiest first):")
    for u in result.unresolved:
        span = f"{(u.first_seen or '')[:10]} to {(u.last_seen or '')[:10]}" if u.messages else "-"
        label = f"{u.identifier} ({u.display_name})" if u.display_name else u.identifier
        print(f"  {u.service:<12} {label:<40} {u.messages:>5} message(s)  {span}")
    print("Name them in an aliases file ({identifier: person}) and run again with --aliases.")
    return 0


def _run_rebuild(settings: Settings, args: argparse.Namespace) -> int:
    result = commands.rebuild(settings, site=args.site, db_path=args.db)
    print(
        f"Rebuilt from {result.pages} page(s): {result.conversations} conversations, "
        f"{result.messages} messages, {result.attachments} attachments, "
        f"{result.reactions} reactions, {result.handles} handles."
    )
    if result.skipped:
        print(f"{len(result.skipped)} page(s) skipped:")
        for reason in result.skipped:
            print(f"  {reason}")
    return 0


def _run_chats(settings: Settings, args: argparse.Namespace) -> int:
    _print_chats(args.database or _configured_chat_db(settings))
    return 0


def _run_doctor(settings: Settings, args: argparse.Namespace) -> int:
    database = args.database or _configured_chat_db(settings)
    results = [commands.doctor(database, check) for check in args.check or commands.DOCTOR_CHECKS]
    return 0 if all(results) else 1


SUBCOMMANDS = {
    "convert": _run_convert,
    "html": _run_html,
    "people": _run_people,
    "rebuild": _run_rebuild,
    "chats": _run_chats,
    "doctor": _run_doctor,
}


# --- The step-by-step menu -------------------------------------------------------------------

MAIN_MENU = ("Convert an export", "Generate HTML from the archive", "iMessage tools", "Exit")
SERVICE_CHOICES = (
    ("whatsapp", "WhatsApp"),
    ("messenger", "Messenger"),
    ("instagram", "Instagram"),
    ("imessage", "iMessage"),
    ("mail", "Mail (MBOX)"),
    ("googlevoice", "Google Voice (Takeout folder)"),
    ("voicemail", "iPhone Voicemail (backup folder)"),
)
IMESSAGE_TOOLS = (
    "List chats",
    "Database information",
    "Test connection",
    "Spam filtering analysis",
    "Back",
)


def _ask(prompt: str, default: Optional[str] = None) -> str:
    """Ask for text; Enter accepts the default (shown in brackets)."""
    answer = input(f"{prompt} [{default}]: " if default else f"{prompt}: ").strip()
    return answer or (default or "")


def _ask_path(prompt: str, default: Optional[str] = None) -> str:
    """Ask for a path, accepting quoted or drag-and-dropped (backslash-escaped) paths."""
    answer = _ask(prompt, default).strip("'\"").replace("\\ ", " ")
    return str(Path(answer).expanduser()) if answer else ""


def _choose(title: str, options: Sequence[str], default: Optional[int] = None) -> int:
    """Ask for one of `options` by number; returns its index. Enter accepts the default."""
    print(f"\n{title}")
    for number, option in enumerate(options, 1):
        print(f"  {number}. {option}")
    hint = f" [{default + 1}]" if default is not None else ""
    while True:
        answer = input(f"Choose 1-{len(options)}{hint}: ").strip()
        if not answer and default is not None:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print(f"Please choose a number from 1 to {len(options)}.")


def _yes(prompt: str, default: bool) -> bool:
    answer = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return answer.startswith("y") if answer else default


def _number(prompt: str, default: Optional[int] = None) -> Optional[int]:
    """Ask for a whole number; Enter accepts the default (or none)."""
    while True:
        answer = _ask(prompt, str(default) if default is not None else None)
        if not answer:
            return None
        if answer.isdigit():
            return int(answer)
        print("Please enter a whole number.")


def _owner() -> Optional[str]:
    return _ask("Archive owner's name, as it appears in the export (Enter to skip)") or None


def _menu_convert(settings: Settings) -> None:
    index = _choose("Which service?", [name for _, name in SERVICE_CHOICES])
    key, name = SERVICE_CHOICES[index]
    if key == "imessage":
        _menu_convert_imessage(settings)
        return
    folder = key in commands.FOLDER_SERVICES
    export = _ask_path(f"Path to the {name} export {'folder' if folder else 'file'}")
    found = Path(export).is_dir() if folder else Path(export).is_file()
    if not export or not found:
        print(f"❌ Export {'folder' if folder else 'file'} not found: {export or '(none given)'}")
        return
    destinations = (
        "The central archive (Database/chattr.sqlite)",
        f"The {name} folder, as JSON (Output/{commands.OUTPUT_FOLDERS[key]}/)",
    )
    route = ("central", "service")[_choose("Where should it go?", destinations, default=0)]
    html = _yes("Also write an HTML page?", default=False)
    transcriber = None
    if key == "voicemail":
        try:
            transcriber = make_transcriber(settings)  # as the settings say; the menu doesn't ask
        except TranscriptionUnavailable as error:
            print(f"❌ {error}")
            return
    result = commands.convert(
        settings, key, export, route=route, html=html, owner=_owner(), transcriber=transcriber
    )
    _print_convert_result(settings, result, route)


def _menu_convert_imessage(settings: Settings) -> None:
    db_path = _ask_path("Path to the iMessage database", _configured_chat_db(settings))
    if not db_path or not Path(db_path).is_file():
        print(f"❌ iMessage database not found: {db_path or '(none given)'}")
        return
    destinations = (
        "The central archive (Database/chattr.sqlite)",
        "iMessage's native JSON (Output/iMessage/)",
    )
    route = ("central", "service")[_choose("Where should it go?", destinations, default=0)]
    options: dict = {"route": route}
    if route == "central":
        options["html"] = _yes("Also write an HTML page for each conversation?", default=False)
        options["owner"] = _owner()
    if not _yes("Use default settings?", default=True):
        modes = commands.SPAM_MODES
        configured = settings.get("services.imessage.preferences.spam_filtering.mode")
        spam = _choose(
            "Spam filtering",
            [mode.title() for mode in modes],
            default=modes.index(configured) if configured in modes else 0,
        )
        options["spam_mode"] = modes[spam]
        options["workers"] = _number(
            "Workers", settings.get("services.imessage.preferences.performance.workers")
        )
        contacts = settings.get_expanded_path("global.contacts.vcf_file_path") or None
        options["contacts"] = _ask_path("VCF contacts file (Enter for none)", contacts) or None
        options["chat"] = _number("Only this chat ID (Enter for all; see iMessage tools)")
        options["limit"] = _number("At most this many messages (Enter for all)")
    result = commands.convert_imessage(settings, db_path=db_path, **options)
    if route == "central":
        _print_convert_result(settings, result, route)
    else:
        _print_imessage_result(result)


def _menu_html(settings: Settings) -> None:
    path = commands.generate_html(settings)
    print(f"HTML: {path}" if path else "The central archive is empty: convert an export first.")


def _menu_imessage_tools(settings: Settings) -> None:
    while True:
        choice = _choose("iMessage tools", IMESSAGE_TOOLS)
        if choice == len(IMESSAGE_TOOLS) - 1:
            return
        db_path = _ask_path("Path to the iMessage database", _configured_chat_db(settings))
        if choice == 0:
            _print_chats(db_path)
        else:
            commands.doctor(db_path, commands.DOCTOR_CHECKS[choice - 1])


def run_menu(settings: Settings) -> int:
    """The step-by-step menu; returns the exit status."""
    print("\n" + "=" * 70)
    print("💬 Chattr: message exports into readable archives")
    print(f"   Data folder: {settings.root}")
    print("=" * 70)
    actions = (_menu_convert, _menu_html, _menu_imessage_tools)
    try:
        while True:
            choice = _choose("What would you like to do?", MAIN_MENU)
            if choice == len(MAIN_MENU) - 1:
                print("Goodbye.")
                return 0
            try:
                actions[choice](settings)
            except commands.CommandError as error:
                print(f"❌ {error}")
    except EOFError:
        print()
        return 0
    except KeyboardInterrupt:
        print()
        return 130


def main(argv: Optional[List[str]] = None) -> int:
    """Run a `chattr-cli` subcommand, or the step-by-step menu when none is given."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "convert":
        _check_convert_options(parser, args)
    load_dotenv("API_Key.env")

    # Ensure the Chattr root exists (never prompts with --root)
    chattr_root = run_preflight(args.root)
    if not chattr_root:
        print("\n❌ Failed to initialize Chattr. Exiting.")
        return 1

    try:
        settings = load_settings(chattr_root)
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        return 1

    if args.command is None:
        return run_menu(settings)
    try:
        return SUBCOMMANDS[args.command](settings, args)
    except commands.CommandError as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
