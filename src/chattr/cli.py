import argparse
import os
from pathlib import Path
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Import the new classes
from chattr.database_manager import DatabaseManager
from chattr.html_generator import HTMLGenerator
from chattr.parsers.whatsapp_parser import WhatsAppParser
from chattr.parsers.messenger_parser import MessengerParser
from chattr.parsers.imessage_database_parser import (
    iMessageDatabaseParser,
    Message,
    Attachment,
    Tapback,
)
from chattr.attachment_manager import (
    AttachmentManager,
    create_attachment_info_from_message_attachment,
)
from chattr.config import ConfigurationError, Settings, get_config, load_settings
from chattr.preflight import run_preflight
from chattr.imessage_export import (  # noqa: F401  (re-exported for older callers)
    export_to_json,
    format_timestamp,
    list_chats,
    print_message_summary,
    process_message_attachments,
)
from chattr.importing import import_messages
from chattr import __version__, commands

# iMessage operational functions migrated from imessage_main.py


def main_menu():
    """Displays the main menu and handles user choices for parsing."""
    print("\n" + "=" * 70)
    print("💬 Chattr - Your Message Archive Swiss Army Knife")
    print("=" * 70)
    print("\nSelect an option:")
    print("  1. Import WhatsApp Chats")
    print("  2. Import Facebook Messenger Chats")
    print("  3. Process iMessage Export")
    print("  4. Generate HTML Reports")
    print("  5. Exit")
    print("=" * 70)

    choice = input("\nEnter your choice: ").strip()
    return choice


def process_and_import(parser, service_name, db_manager):
    """Handles parsing a source and inserting it into the database."""
    source_path = input(f"Enter the path to your {service_name} data: ").strip()
    print(f"Parsing {service_name} data...")
    messages = parser.parse(source_path)
    if messages:
        print(f"Found {len(messages)} messages. Importing into the database...")
        try:
            settings = get_config()
        except ConfigurationError:
            settings = None
        with db_manager:
            counts = import_messages(db_manager, settings, service_name, source_path, messages)
        print(
            f"Import complete: {counts.inserted} new, {counts.updated} updated, "
            f"{counts.unchanged} already archived."
        )
    else:
        print("No messages found or an error occurred during parsing.")


def imessage_submenu():
    """Handles iMessage processing with advanced options"""
    try:
        config = get_config()

        print("\n--- iMessage Processing ---")
        print("Direct database access for maximum performance and data quality")
        print("\nSelect an option:")
        print("  1. Quick Process (use default settings)")
        print("  2. Advanced Options")
        print("  3. List Available Chats")
        print("  4. Diagnostics & Tools")
        print("  5. Back to Main Menu")

        choice = input("Enter your choice: ").strip()

        if choice == "1":
            imessage_quick_process(config)
        elif choice == "2":
            imessage_advanced_options(config)
        elif choice == "3":
            imessage_list_chats(config)
        elif choice == "4":
            imessage_diagnostics(config)
        elif choice == "5":
            return
        else:
            print("Invalid choice. Please try again.")

    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        print("Please check your config.json file.")
    except Exception as e:
        print(f"Error: {e}")


def imessage_quick_process(config):
    """Quick iMessage processing with default settings"""
    print("\n--- Quick iMessage Processing ---")

    # Get configuration defaults
    db_path = config.get_expanded_path("services.imessage.preferences.database.default_db_path")
    spam_mode = config.get("services.imessage.preferences.spam_filtering.mode", "aggressive")
    max_workers = config.get("services.imessage.preferences.performance.workers", 4)

    print(f"Database: {db_path}")
    print(f"Spam filtering: {spam_mode}")
    print(f"Workers: {max_workers}")

    # Ask for output file
    output_file = input(
        "Enter output JSON file path (e.g., ./output/imessage_export.json): "
    ).strip()
    if not output_file:
        print("Output file is required.")
        return

    # Create output directory if needed
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

    try:
        # Initialize database
        enable_spam_filtering = spam_mode != "disabled"
        aggressive_spam_filtering = spam_mode == "aggressive"

        print("Connecting to iMessage database...")
        db = iMessageDatabaseParser(
            db_path=db_path,
            enable_spam_filtering=enable_spam_filtering,
            aggressive_spam_filtering=aggressive_spam_filtering,
        )

        # Get all messages
        print("Fetching all messages...")
        messages = db.get_all_messages_parallel(max_workers=max_workers)

        if not messages:
            print("No messages found.")
            return

        print(f"Found {len(messages):,} messages")

        # Show spam filtering statistics
        spam_stats = db.get_spam_filtering_stats()
        if spam_stats and spam_stats["total_suppressed"] > 0:
            print(f"Spam filtering removed {spam_stats['total_suppressed']:,} low-value messages:")
            for category, count in spam_stats.items():
                if category != "total_suppressed" and count > 0:
                    print(f"  - {category.replace('_', ' ').title()}: {count:,}")

        # Export to JSON
        export_to_json(messages, db, output_file)

        print(f"\n✅ Quick processing complete!")
        print(f"Exported {len(messages):,} messages to {output_file}")

    except Exception as e:
        print(f"Error during processing: {e}")
    finally:
        try:
            db.close()
        except:
            pass


def imessage_advanced_options(config):
    """Advanced iMessage processing with custom options"""
    print("\n--- Advanced iMessage Options ---")

    # Database path
    default_db_path = config.get_expanded_path(
        "services.imessage.preferences.database.default_db_path"
    )
    db_path = input(f"Database path (default: {default_db_path}): ").strip()
    if not db_path:
        db_path = default_db_path

    # VCF contacts file
    vcf_path = input("VCF contacts file path (optional): ").strip() or None

    # Spam filtering mode
    print("\nSpam filtering modes:")
    print("  1. Aggressive (99.887% text rate) - recommended")
    print("  2. Conservative (99.572% text rate)")
    print("  3. Disabled (99.424% text rate)")
    spam_choice = input("Choose spam filtering mode (1-3, default: 1): ").strip()

    if spam_choice == "2":
        enable_spam_filtering = True
        aggressive_spam_filtering = False
    elif spam_choice == "3":
        enable_spam_filtering = False
        aggressive_spam_filtering = False
    else:
        enable_spam_filtering = True
        aggressive_spam_filtering = True

    # Performance settings
    max_workers = int(
        input(
            f"Number of workers (default: {config.get('services.imessage.preferences.performance.workers', 4)}): "
        ).strip()
        or config.get("services.imessage.preferences.performance.workers", 4)
    )

    # Chat filter
    chat_filter_text = input("Filter by specific chat ID (optional): ").strip()
    chat_filter = int(chat_filter_text) if chat_filter_text else None

    # Message limit
    limit_text = input("Maximum messages to export (optional, default: all): ").strip()
    limit = int(limit_text) if limit_text else None

    # Output file
    output_file = input("Output JSON file path: ").strip()
    if not output_file:
        print("Output file is required.")
        return

    # Attachment processing
    process_attachments = input("Process attachments? (y/n, default: n): ").strip().lower() == "y"
    attachments_dir = None
    source_attachments_dir = config.get_expanded_path(
        "services.imessage.preferences.database.default_attachments_path"
    )

    if process_attachments:
        attachments_dir = (
            input(f"Attachments output directory (default: ./Attachments): ").strip()
            or "./Attachments"
        )
        source_attachments_dir = (
            input(f"Source attachments directory (default: {source_attachments_dir}): ").strip()
            or source_attachments_dir
        )

    # Create output directory
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

    try:
        # Initialize database
        print("Connecting to iMessage database...")
        db = iMessageDatabaseParser(
            db_path=db_path,
            vcf_path=vcf_path,
            enable_spam_filtering=enable_spam_filtering,
            aggressive_spam_filtering=aggressive_spam_filtering,
        )

        # Get messages
        if limit:
            print(f"Fetching messages (limit: {limit})...")
            messages = db.get_messages_with_fallback(limit=limit, chat_filter=chat_filter)
        else:
            print("Fetching all messages...")
            messages = db.get_all_messages_parallel(
                chat_filter=chat_filter, max_workers=max_workers
            )

        if not messages:
            print("No messages found.")
            return

        print(f"Found {len(messages):,} messages")

        # Show spam filtering statistics
        spam_stats = db.get_spam_filtering_stats()
        if spam_stats and spam_stats["total_suppressed"] > 0:
            print(f"Spam filtering removed {spam_stats['total_suppressed']:,} low-value messages:")
            for category, count in spam_stats.items():
                if category != "total_suppressed" and count > 0:
                    print(f"  - {category.replace('_', ' ').title()}: {count:,}")

        # Export to JSON
        export_to_json(
            messages,
            db,
            output_file,
            attachments_dir=attachments_dir,
            source_attachments_dir=source_attachments_dir,
        )

        print(f"\n✅ Advanced processing complete!")
        print(f"Exported {len(messages):,} messages to {output_file}")

    except Exception as e:
        print(f"Error during processing: {e}")
    finally:
        try:
            db.close()
        except:
            pass


def imessage_list_chats(config):
    """List available iMessage chats"""
    print("\n--- Available iMessage Chats ---")

    db_path = config.get_expanded_path("services.imessage.preferences.database.default_db_path")

    try:
        db = iMessageDatabaseParser(db_path=db_path)
        list_chats(db)
    except Exception as e:
        print(f"Error listing chats: {e}")
    finally:
        try:
            db.close()
        except:
            pass


def imessage_diagnostics(config):
    """iMessage diagnostics and tools"""
    print("\n--- iMessage Diagnostics & Tools ---")
    print("  1. Database Information")
    print("  2. Test Database Connection")
    print("  3. Spam Filtering Analysis")
    print("  4. Back to iMessage Menu")

    choice = input("Enter your choice: ").strip()

    if choice == "1":
        imessage_database_info(config)
    elif choice == "2":
        imessage_test_connection(config)
    elif choice == "3":
        imessage_spam_analysis(config)
    elif choice == "4":
        return
    else:
        print("Invalid choice.")


def imessage_database_info(config):
    """Show iMessage database information"""
    db_path = config.get_expanded_path("services.imessage.preferences.database.default_db_path")

    try:
        print(f"\nDatabase path: {db_path}")
        print(f"Database exists: {os.path.exists(db_path)}")

        if os.path.exists(db_path):
            stat = os.stat(db_path)
            print(f"Database size: {stat.st_size:,} bytes")
            print(f"Last modified: {datetime.fromtimestamp(stat.st_mtime)}")

        db = iMessageDatabaseParser(db_path=db_path)
        print(f"Handles loaded: {len(db.handles)}")
        print(f"Chats loaded: {len(db.chats)}")

        # Quick message count
        messages = db.get_messages_with_fallback(limit=1)
        if messages:
            print("Database connection: ✅ Success")
        else:
            print("Database connection: ⚠️  No messages found")

    except Exception as e:
        print(f"Database connection: ❌ Error - {e}")
    finally:
        try:
            db.close()
        except:
            pass


def imessage_test_connection(config):
    """Test iMessage database connection"""
    db_path = config.get_expanded_path("services.imessage.preferences.database.default_db_path")

    try:
        print(f"Testing connection to: {db_path}")
        db = iMessageDatabaseParser(db_path=db_path)

        # Test basic queries
        print("✅ Database connection successful")
        print(f"✅ Loaded {len(db.handles)} handles")
        print(f"✅ Loaded {len(db.chats)} chats")

        # Test message query
        messages = db.get_messages_with_fallback(limit=5)
        print(f"✅ Retrieved {len(messages)} test messages")

        print("\n🎉 All tests passed!")

    except Exception as e:
        print(f"❌ Connection test failed: {e}")
    finally:
        try:
            db.close()
        except:
            pass


def imessage_spam_analysis(config):
    """Analyze spam filtering effectiveness"""
    db_path = config.get_expanded_path("services.imessage.preferences.database.default_db_path")

    try:
        print("Analyzing spam filtering effectiveness...")

        # Test with different filtering modes
        modes = [
            ("Disabled", False, False),
            ("Conservative", True, False),
            ("Aggressive", True, True),
        ]

        for mode_name, enable_filtering, aggressive in modes:
            print(f"\n--- {mode_name} Mode ---")

            db = iMessageDatabaseParser(
                db_path=db_path,
                enable_spam_filtering=enable_filtering,
                aggressive_spam_filtering=aggressive,
            )

            # Get a sample of messages
            messages = db.get_messages_with_fallback(limit=1000)

            spam_stats = db.get_spam_filtering_stats()
            if spam_stats:
                total_suppressed = spam_stats["total_suppressed"]
                print(f"Messages suppressed: {total_suppressed:,}")
                for category, count in spam_stats.items():
                    if category != "total_suppressed" and count > 0:
                        print(f"  - {category.replace('_', ' ').title()}: {count:,}")
            else:
                print("No spam filtering applied")

            db.close()

    except Exception as e:
        print(f"Error during spam analysis: {e}")


def generate_html_submenu(db_manager):
    """Handles the generation of HTML reports from the database."""
    html_generator = HTMLGenerator()
    # Placeholder for a more advanced submenu
    print("\nGenerating 'All Chat' report...")
    with db_manager:
        all_messages = db_manager.get_all_messages()

    if all_messages:
        owner_name = input("Enter the archive owner's name (for 'sent' messages): ").strip()
        output_path = os.path.join("output", "All_Chats_Archive.html")
        html_generator.generate_html(all_messages, output_path, main_author=owner_name)
    else:
        print("No messages found in the database to generate a report.")


def database_path(settings: Settings) -> Path:
    """The central database for this Chattr root: `<root>/Database/chattr.sqlite`."""
    return Path(settings.root) / "Database" / "chattr.sqlite"


IMESSAGE_ONLY = (
    "contacts",
    "region",
    "attachment_root",
    "batch_size",
    "attachment_workers",
    "workers",
    "spam_mode",
    "chat",
    "limit",
    "verbose",
)
NOT_FOR_IMESSAGE = ("route", "html", "aliases", "owner")


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
    convert.add_argument("service", type=str.lower, choices=["whatsapp", "messenger", "imessage"])
    convert.add_argument(
        "export", nargs="?", help="the export file (for iMessage, chat.db; default: configured)"
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
    imessage = convert.add_argument_group("iMessage options")
    imessage.add_argument("--contacts", help="VCF contacts file used to name senders")
    imessage.add_argument("--region", help="phone number region for contacts (default: US)")
    imessage.add_argument("--attachment-root", help="folder holding the original attachments")
    imessage.add_argument("--batch-size", type=int, help="messages per batch")
    imessage.add_argument("--attachment-workers", type=int, help="parallel attachment workers")
    imessage.add_argument("--workers", type=int, help="parallel message workers")
    imessage.add_argument("--spam-mode", choices=commands.SPAM_MODES, help="spam filtering")
    imessage.add_argument("--chat", type=int, help="only this chat ID (see `chats`)")
    imessage.add_argument("--limit", type=int, help="at most this many messages")
    imessage.add_argument("--verbose", action="store_true", help="print every message")

    html = sub.add_parser("html", help="render the central archive as one HTML page")
    html.add_argument("--out", help="output file (default: Output/Chattr/All_Chats_Archive.html)")
    html.add_argument("--owner", help="the archive owner's name (their messages are 'sent')")

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
    for command in (convert, html, chats, doctor):
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
        wrong = [flag(n) for n in NOT_FOR_IMESSAGE if given(n)]
        if wrong:
            parser.error(
                f"{', '.join(wrong)} can't be used with iMessage yet: iMessage converts to its "
                "native JSON in Output/iMessage/."
            )
        return
    wrong = [flag(n) for n in IMESSAGE_ONLY if given(n)]
    if wrong:
        parser.error(f"{', '.join(wrong)} applies to iMessage only.")
    if not args.export:
        parser.error(f"convert {args.service}: give the export file to convert.")


def _configured_chat_db(settings: Settings) -> str:
    return settings.get_expanded_path("services.imessage.preferences.database.default_db_path")


def _run_convert(settings: Settings, args: argparse.Namespace) -> int:
    if args.service == "imessage":
        result = commands.convert_imessage(
            settings,
            db_path=args.export,
            contacts=args.contacts,
            region=args.region or "US",
            attachment_root=args.attachment_root,
            batch_size=args.batch_size,
            attachment_workers=args.attachment_workers,
            workers=args.workers,
            spam_mode=args.spam_mode,
            chat=args.chat,
            limit=args.limit,
            verbose=args.verbose,
        )
        print(f"iMessage: {result.counts.total:,} messages → {result.json_path}")
        return 0
    route = args.route or "central"
    result = commands.convert(
        settings,
        args.service,
        args.export,
        route=route,
        html=args.html,
        owner=args.owner,
        aliases=args.aliases,
    )
    counts = result.counts
    where = commands.central_database(settings) if route == "central" else result.json_path
    print(
        f"{result.service}: {counts.inserted:,} new, {counts.updated:,} updated, "
        f"{counts.unchanged:,} already archived → {where}"
    )
    if result.html_path:
        print(f"HTML: {result.html_path}")
    return 0


def _run_html(settings: Settings, args: argparse.Namespace) -> int:
    path = commands.generate_html(settings, out=args.out, owner=args.owner)
    if path is None:
        print("The central archive is empty: convert an export first.")
        return 1
    print(f"HTML: {path}")
    return 0


def _run_chats(settings: Settings, args: argparse.Namespace) -> int:
    for chat_id, name in commands.list_chats(args.database or _configured_chat_db(settings)):
        print(f"{chat_id:>5}  {name}")
    return 0


def _run_doctor(settings: Settings, args: argparse.Namespace) -> int:
    database = args.database or _configured_chat_db(settings)
    results = [commands.doctor(database, check) for check in args.check or commands.DOCTOR_CHECKS]
    return 0 if all(results) else 1


SUBCOMMANDS = {
    "convert": _run_convert,
    "html": _run_html,
    "chats": _run_chats,
    "doctor": _run_doctor,
}


def run_menu(settings: Settings) -> int:
    """The step-by-step menu; returns the exit status."""
    output_dir = settings.get_expanded_path("global.general.output_directory")
    os.makedirs(output_dir, exist_ok=True)

    db_path = database_path(settings)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_manager = DatabaseManager(db_path=str(db_path))

    # Ensure tables are created once at the start
    with db_manager:
        db_manager.create_tables()

    while True:
        choice = main_menu()

        if choice == "1":
            process_and_import(WhatsAppParser(), "WhatsApp", db_manager)
        elif choice == "2":
            process_and_import(MessengerParser(), "Facebook Messenger", db_manager)
        elif choice == "3":
            imessage_submenu()
        elif choice == "4":
            generate_html_submenu(db_manager)
        elif choice == "5":
            print("Exiting.")
            return 0
        else:
            print("Invalid choice. Please try again.")

        print("\n--------------------")


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
