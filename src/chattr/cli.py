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
from chattr.importing import import_messages
from chattr import __version__

# iMessage operational functions migrated from imessage_main.py


def format_timestamp(dt: datetime) -> str:
    """Format datetime for display"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def print_message_summary(message: Message, attachments: List[Attachment], tapbacks: List[Tapback]):
    """Print a formatted summary of a message"""
    print(f"\n{'='*80}")
    print(f"Message ID: {message.rowid}")
    print(f"GUID: {message.guid}")
    print(f"Date: {format_timestamp(message.timestamp)}")
    print(f"From: {message.sender}")
    print(f"Chat: {message.chat_name}")
    print(f"Service: {message.service or 'Unknown'}")

    if message.text:
        print(f"Text: {message.text}")

    if message.is_from_me:
        print("Direction: Sent")
    else:
        print("Direction: Received")
        if message.date_read:
            read_time = iMessageDatabaseParser._apple_timestamp_to_datetime(message.date_read)
            print(f"Read: {format_timestamp(read_time)}")

    # Show message type info
    if message.item_type != 0:
        print(f"Item Type: {message.item_type}")

    if message.balloon_bundle_id:
        print(f"App: {message.balloon_bundle_id}")

    # Show attachments
    if attachments:
        print(f"\nAttachments ({len(attachments)}):")
        for i, att in enumerate(attachments, 1):
            print(f"  {i}. {att.transfer_name or 'Unknown'}")
            print(f"     Type: {att.mime_type or 'Unknown'}")
            print(f"     Size: {att.total_bytes:,} bytes")
            if att.is_sticker:
                print(f"     Sticker: Yes")
            if att.filename:
                print(f"     Path: {att.filename}")

    # Show tapbacks
    if tapbacks:
        print(f"\nTapbacks ({len(tapbacks)}):")
        for tapback in tapbacks:
            if tapback.action == "Added":
                print(f"  {tapback.type} by {tapback.sender}")
            # Skip removed tapbacks for cleaner output

    print(f"{'='*80}")


def export_to_json(
    messages: List[Message],
    db: iMessageDatabaseParser,
    output_path: str,
    attachments_dir: Optional[str] = None,
    source_attachments_dir: str = "~/Library/Messages/Attachments",
    attachment_workers: int = 4,
):
    """Export messages to JSON format with parallel processing and attachment archival"""

    print(f"Exporting {len(messages):,} messages to JSON...")

    # Enrich messages with attachments and tapbacks using parallel processing
    messages = db.enrich_messages_with_attachments_parallel(messages)
    messages = db.enrich_messages_with_tapbacks_parallel(messages)

    # Process attachments if directory is specified
    processed_attachments = {}
    if attachments_dir:
        processed_attachments = process_message_attachments(
            messages, attachments_dir, source_attachments_dir, attachment_workers
        )

    print("Converting to JSON format...")
    export_data = []

    for message in messages:
        # Use the pre-loaded attachments and tapbacks

        # Convert to dict for JSON serialization
        message_data = {
            "id": message.rowid,
            "guid": message.guid,
            "text": message.text,
            "timestamp": message.timestamp.isoformat(),
            "sender": message.sender,
            "chat": message.chat_name,
            "service": message.service,
            "is_from_me": message.is_from_me,
            "is_read": message.is_read,
            "attachments": [
                {
                    "original_filename": att.transfer_name or att.filename,
                    "filename": (
                        processed_attachments.get(att.filename or "", {})
                        or processed_attachments.get(os.path.basename(att.filename or ""), {})
                    ).get("archival_filename", att.transfer_name or att.filename),
                    "attachment_path": (
                        processed_attachments.get(att.filename or "", {})
                        or processed_attachments.get(os.path.basename(att.filename or ""), {})
                    ).get("archival_path"),
                    "mime_type": att.mime_type,
                    "file_size": att.total_bytes,
                    "is_sticker": att.is_sticker,
                    "file_hash": (
                        processed_attachments.get(att.filename or "", {})
                        or processed_attachments.get(os.path.basename(att.filename or ""), {})
                    ).get("file_hash"),
                    "is_duplicate": (
                        processed_attachments.get(att.filename or "", {})
                        or processed_attachments.get(os.path.basename(att.filename or ""), {})
                    ).get("is_duplicate", False),
                    "conversion_applied": (
                        processed_attachments.get(att.filename or "", {})
                        or processed_attachments.get(os.path.basename(att.filename or ""), {})
                    ).get("conversion_applied", False),
                }
                for att in (message.attachments or [])
            ],
            "tapbacks": [
                {"type": tb.type, "sender": tb.sender, "action": tb.action}
                for tb in (message.tapbacks or [])
                if tb.action == "Added"
            ],
        }

        export_data.append(message_data)

    # Write to file
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)

    print(f"Exported to: {output_path}")


def process_message_attachments(
    messages: List[Message], attachments_dir: str, source_attachments_dir: str, max_workers: int = 4
) -> Dict[str, Dict[str, Any]]:
    """
    Process all attachments from messages and return mapping of original filename to processed info

    Args:
        messages: List of Message objects with attachments
        attachments_dir: Target directory for archival attachments
        source_attachments_dir: Source directory where original attachments are stored
        max_workers: Number of parallel workers for processing

    Returns:
        Dictionary mapping original filenames to processed attachment info
    """
    print(f"\n📎 PROCESSING ATTACHMENTS")

    # Initialize attachment manager
    attachment_manager = AttachmentManager(
        base_attachments_dir=attachments_dir,
        service_name="iMessage",
        move_files=False,  # Copy files for iMessage (preserve originals)
        max_workers=max_workers,
    )

    # Collect all attachments with their message dates
    attachments_with_dates = []
    source_dir = os.path.expanduser(source_attachments_dir)

    for message in messages:
        if message.attachments:
            for attachment in message.attachments:
                # Create AttachmentInfo from message attachment
                attachment_info = create_attachment_info_from_message_attachment(
                    {
                        "filename": attachment.filename,
                        "mime_type": attachment.mime_type,
                        "file_size": attachment.total_bytes,
                    },
                    source_dir,
                )

                if attachment_info:
                    attachments_with_dates.append((attachment_info, message.timestamp))

    if not attachments_with_dates:
        print("No attachments found to process.")
        return {}

    # Process attachments in parallel
    processed_attachments = attachment_manager.process_attachments_parallel(attachments_with_dates)

    # Create mapping for easy lookup during JSON export
    # Use both full path and basename as keys for flexible lookup
    attachment_mapping = {}
    for info in processed_attachments:
        attachment_data = {
            "archival_filename": info.archival_filename,
            "archival_path": info.archival_path,
            "file_hash": info.file_hash,
            "is_duplicate": info.is_duplicate,
            "conversion_applied": info.conversion_applied,
            "processing_status": info.processing_status,
            "error_message": info.error_message,
        }

        # Map by both original path and filename for flexible lookup
        attachment_mapping[info.original_path] = attachment_data
        attachment_mapping[info.original_filename] = attachment_data

        # Also map by basename in case paths don't match exactly
        basename = os.path.basename(info.original_path)
        attachment_mapping[basename] = attachment_data

    return attachment_mapping


def list_chats(db: iMessageDatabaseParser):
    """List all available chats"""
    print("\nAvailable Chats:")
    print("-" * 50)

    for chat_id, chat_name in db.chats.items():
        # Get message count for this chat
        try:
            messages = db.get_messages_with_fallback(limit=1, chat_filter=chat_id)
            if messages:
                last_message = messages[0]
                print(
                    f"ID: {chat_id:3d} | {chat_name} (Last: {format_timestamp(last_message.timestamp)})"
                )
            else:
                print(f"ID: {chat_id:3d} | {chat_name} (No messages)")
        except Exception as e:
            print(f"ID: {chat_id:3d} | {chat_name} (Error: {e})")


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chattr-cli", description="Convert message exports into readable archives."
    )
    parser.add_argument("--version", action="version", version=f"chattr {__version__}")
    parser.add_argument("--root", help="Chattr data folder (skips the first-run prompt)")
    return parser


def main(argv=None):
    """Main function to orchestrate the chat export processing."""
    args = build_parser().parse_args(argv)
    load_dotenv("API_Key.env")

    # Run preflight check - ensures the Chattr root exists (never prompts with --root)
    chattr_root = run_preflight(args.root)

    if not chattr_root:
        print("\n❌ Failed to initialize Chattr. Exiting.")
        return 1

    # Log successful preflight (silent if structure already existed)
    import logging

    logging.basicConfig(level=logging.INFO)
    logging.info(f"Chattr initialized at: {chattr_root}")

    # Load configuration from the Chattr root
    try:
        config = load_settings(chattr_root)
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        return 1
    output_dir = config.get_expanded_path("global.general.output_directory")
    os.makedirs(output_dir, exist_ok=True)

    db_path = database_path(config)
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


if __name__ == "__main__":
    raise SystemExit(main())
