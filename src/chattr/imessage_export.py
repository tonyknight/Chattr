"""Native iMessage JSON export: messages with attachments and tapbacks.

Moved out of the CLI so the menu and the `chattr-cli` subcommands can share it.
"""

import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

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
            "spam_reason": message.spam_reason,
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
