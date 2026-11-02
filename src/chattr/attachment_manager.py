#!/usr/bin/env python3
"""
AttachmentManager - Universal Attachment Processing System

A comprehensive attachment processing system designed for archival quality storage
across multiple messaging platforms. Handles file copying, format conversion,
metadata preservation, and standardized naming conventions.

🎯 KEY FEATURES:
- Universal attachment processing for multiple messaging services
- Archival quality format conversion (HEIC/PNG → JPG at 85% quality)
- EXIF metadata preservation and enhancement
- Standardized date-based folder structure and naming
- Duplicate detection with hash-based deduplication
- Parallel processing for large datasets
- Swift-compatible architecture for future porting

📁 ARCHIVAL STRUCTURE:
/Attachments/{service}/{year}/{month_number} - {month_name}/{filename}
Example: /Attachments/iMessage/2024/3 - March/(2024-03-15) 14-30-25 [123].jpg

🔧 PROCESSING PIPELINE:
1. Source file validation and hash calculation
2. EXIF metadata extraction and date determination
3. Format conversion (if needed) with quality optimization
4. Metadata embedding using ExifTool
5. Archival naming and folder organization
6. Duplicate detection and symlink creation
7. Database record updates with new paths

🎛️ SUPPORTED FORMATS:
- Images: HEIC, PNG, WebP → JPG (85% quality), JPEG (preserved), GIF (preserved)
- Videos: MP4, MOV (metadata enhancement only)
- Documents: PDF, DOC, XLS, TXT (date-prefixed naming)
- Audio: CAF → AAC (lossless), M4A, MP3 (date-prefixed naming)
- Other: VCF, ZIP, etc. (date-prefixed naming)

⚡ PERFORMANCE:
- Multi-threaded processing with configurable workers
- Progress tracking with detailed statistics
- Efficient duplicate detection using SHA-256 hashing
- Batch processing for optimal memory usage
"""

import os
import re
import shutil
import hashlib
import subprocess
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm

try:
    from PIL import Image, ExifTags

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("Warning: PIL/Pillow not available. Image conversion will be disabled.")

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()  # Register HEIC/HEIF support for Pillow
    HEIF_AVAILABLE = True
except ImportError:
    HEIF_AVAILABLE = False
    print("Warning: pillow-heif not available. HEIC conversion will be limited.")

try:
    import ffmpeg

    FFMPEG_AVAILABLE = True
except ImportError:
    FFMPEG_AVAILABLE = False
    print("Warning: ffmpeg-python not available. Audio conversion will be disabled.")


@dataclass
class AttachmentInfo:
    """
    Data structure representing an attachment with all necessary metadata

    This class is designed to be easily portable to Swift as a struct
    """

    # Original attachment data
    original_path: str
    original_filename: str
    mime_type: Optional[str]
    file_size: int
    created_date: Optional[datetime]

    # Processing results
    archival_path: Optional[str] = None
    archival_filename: Optional[str] = None
    file_hash: Optional[str] = None
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None
    conversion_applied: bool = False
    metadata_enhanced: bool = False

    # Who it came from and whose folder it belongs in
    sender: Optional[str] = None
    person: Optional[str] = None
    is_group: bool = False

    # Filled in while archiving
    target_date: Optional[datetime] = None  # capture date when the file has one, else message time
    has_capture_date: bool = False
    reused: bool = False  # the archive already holds this exact content under this name

    # Processing status
    processing_status: str = "pending"  # pending, processing, completed, failed
    error_message: Optional[str] = None


@dataclass
class ProcessingStats:
    """Statistics tracking for attachment processing operations"""

    total_attachments: int = 0
    processed_successfully: int = 0
    failed_processing: int = 0
    skipped_processing: int = 0  # Files skipped (e.g., plugin payload attachments)
    duplicates_found: int = 0
    conversions_applied: int = 0
    metadata_enhanced: int = 0
    total_size_bytes: int = 0
    processing_time_seconds: float = 0.0


class AttachmentProcessor:
    """
    Core attachment processing engine

    Handles individual file operations including conversion, metadata enhancement,
    and archival naming. Designed with MVC pattern for Swift compatibility.
    """

    def __init__(self, service_name: str = "iMessage"):
        self.service_name = service_name
        self.exiftool = shutil.which("exiftool")  # capture dates are read and written with it
        self.supported_image_formats = {
            ".heic",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".tiff",
            ".tif",
            ".bmp",
            ".webp",
        }
        self.supported_video_formats = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".3gp", ".webm"}
        self.supported_audio_formats = {".m4a", ".caf", ".mp3", ".wav", ".aac", ".flac"}
        self.image_conversion_formats = {".heic", ".png", ".webp"}  # Formats to convert to JPG
        self.audio_conversion_formats = {".caf"}  # Formats to convert to AAC
        self.conversion_formats = self.image_conversion_formats | self.audio_conversion_formats

    def calculate_file_hash(self, file_path: str) -> str:
        """Calculate SHA-256 hash of file for duplicate detection"""
        hash_sha256 = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_sha256.update(chunk)
            return hash_sha256.hexdigest()
        except Exception as e:
            raise Exception(f"Failed to calculate hash for {file_path}: {e}")

    def extract_exif_date(self, file_path: str) -> Optional[datetime]:
        """Extract DateTimeOriginal from EXIF data using ExifTool with robust parsing"""
        if not self.exiftool:
            return None
        try:
            # Use ExifTool without format specification to get raw data
            result = subprocess.run(
                ["exiftool", "-DateTimeOriginal", "-s3", file_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0 and result.stdout.strip():
                date_output = result.stdout.strip()
                if date_output and date_output != "-":
                    # Try to parse various date formats
                    return self._parse_exif_date_string(date_output)
        except Exception as e:
            print(f"Warning: Failed to extract EXIF date from {file_path}: {e}")

        return None

    def _parse_exif_date_string(self, date_str: str) -> Optional[datetime]:
        """Parse EXIF date string with multiple format support"""
        # Clean up the string - take only the first line if multiple lines exist
        date_str = date_str.split("\n")[0].strip()

        # Common EXIF date formats to try
        date_formats = [
            "%Y:%m:%d %H:%M:%S",  # Standard EXIF format: 2018:02:23 22:28:12
            "%Y-%m-%d %H:%M:%S",  # ISO format: 2018-02-23 22:28:12
            "%d.%m.%Y %H:%M:%S",  # European format: 04.03.2011 10:03:50
            "%m/%d/%Y %H:%M:%S",  # US format: 03/04/2011 10:03:50
            "%Y:%m:%d %H:%M:%S%z",  # With timezone
            "%Y-%m-%d %H:%M:%S%z",  # ISO with timezone
        ]

        for fmt in date_formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        # If no format matches, try to extract just the date part
        import re

        # Try to find YYYY:MM:DD or YYYY-MM-DD pattern
        date_match = re.search(r"(\d{4})[:-](\d{2})[:-](\d{2})", date_str)
        if date_match:
            year, month, day = date_match.groups()

            # Try to find time part
            time_match = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", date_str)
            if time_match:
                hour, minute, second = time_match.groups()
                try:
                    return datetime(
                        int(year), int(month), int(day), int(hour), int(minute), int(second)
                    )
                except ValueError:
                    pass

            # Just date without time
            try:
                return datetime(int(year), int(month), int(day))
            except ValueError:
                pass

        print(f"Warning: Could not parse EXIF date format: '{date_str}'")
        return None

    def embed_exif_date(self, file_path: str, date_time: datetime) -> bool:
        """Embed DateTimeOriginal into file using ExifTool"""
        if not self.exiftool:
            return False
        try:
            date_str = date_time.strftime("%Y:%m:%d %H:%M:%S")
            result = subprocess.run(
                ["exiftool", "-overwrite_original", f"-DateTimeOriginal={date_str}", file_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            return result.returncode == 0
        except Exception as e:
            print(f"Warning: Failed to embed EXIF date in {file_path}: {e}")
            return False

    def _validate_image_file(self, file_path: str) -> bool:
        """Validate that an image file can be opened and read"""
        if not PIL_AVAILABLE:
            return False

        # Check if it's a HEIC file and we don't have HEIF support
        file_ext = Path(file_path).suffix.lower()
        if file_ext in [".heic", ".heif"] and not HEIF_AVAILABLE:
            print(f"Warning: HEIC file {file_path} requires pillow-heif library")
            return False

        try:
            with Image.open(file_path) as img:
                # Try to load the image data to verify it's not corrupted
                img.load()
                # Check if image has reasonable dimensions
                if img.size[0] > 0 and img.size[1] > 0:
                    return True
        except Exception as e:
            # Provide more specific error information for HEIC files
            if file_ext in [".heic", ".heif"]:
                print(f"Warning: HEIC file validation failed for {file_path}: {e}")
                if not HEIF_AVAILABLE:
                    print("  → Install pillow-heif: pip install pillow-heif")
            pass

        return False

    def convert_image_to_jpg(self, input_path: str, output_path: str, quality: int = 85) -> bool:
        """Convert HEIC/PNG/WebP to JPG with specified quality and validation"""
        if not PIL_AVAILABLE:
            return False

        # Check for HEIC files specifically
        file_ext = Path(input_path).suffix.lower()
        if file_ext in [".heic", ".heif"] and not HEIF_AVAILABLE:
            print(f"Warning: Cannot convert HEIC file {input_path} - pillow-heif not available")
            print("  → Install with: pip install pillow-heif")
            return False

        # Pre-validate the image file
        if not self._validate_image_file(input_path):
            print(
                f"Warning: Image file validation failed for {input_path} (corrupted or unreadable)"
            )
            return False

        try:
            with Image.open(input_path) as opened:
                img: Image.Image = opened
                # For HEIC files, ensure we handle them properly
                if file_ext in [".heic", ".heif"]:
                    print(f"Converting HEIC file: {Path(input_path).name}")

                # Convert to RGB if necessary (for HEIC/PNG/WebP with transparency)
                if img.mode in ("RGBA", "LA", "P"):
                    # Create white background for transparency
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    background.paste(
                        img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None
                    )
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                # Save as JPG with specified quality
                img.save(output_path, "JPEG", quality=quality, optimize=True)
                return True
        except Exception as e:
            # Provide specific guidance for HEIC conversion failures
            if file_ext in [".heic", ".heif"]:
                print(f"Warning: HEIC conversion failed for {input_path}: {e}")
                if not HEIF_AVAILABLE:
                    print("  → This likely requires: pip install pillow-heif")
            else:
                print(f"Warning: Failed to convert {input_path} to JPG: {e}")
            return False

    def convert_caf_to_aac(self, input_path: str, output_path: str) -> bool:
        """Convert CAF audio to AAC format (lossless when possible)"""
        if not FFMPEG_AVAILABLE:
            return False

        try:
            # Use FFmpeg to convert CAF to AAC
            # -c:a aac uses the built-in AAC encoder
            # -b:a 128k sets a reasonable bitrate for good quality
            # -movflags +faststart optimizes for streaming
            (
                ffmpeg.input(input_path)
                .output(output_path, acodec="aac", audio_bitrate="128k", movflags="+faststart")
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
            return True
        except Exception as e:
            print(f"Warning: Failed to convert {input_path} to AAC: {e}")
            return False


MANIFEST_NAME = ".attachments.json"
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
#: File types that can carry a capture date in standard metadata (GIF and BMP cannot).
_DATED_FORMATS = {".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff", ".webp"}


def safe_name(text: Optional[str], fallback: str = "Unknown", limit: int = 80) -> str:
    """A person or sender name made safe for a file or folder name."""
    cleaned = re.sub(r"\s+", " ", _UNSAFE.sub("-", text or "")).strip(" .-")
    return cleaned[:limit].strip(" .-") or fallback


def _sort_key(when: datetime) -> float:
    aware = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return aware.timestamp()


class AttachmentManager:
    """The one attachment pipeline: every service's files, archived the same way.

    Items are `(AttachmentInfo, message time)`. Each file is copied into
    `<archive>/<Person>/Attachments/` (groups: `Groups/<Name>/Attachments/`) as

        (YYYY-MM-DD) HH-MM-SS <sender> <n>.<ext>

    dated by the file's own capture date when it has one, else by the message time as stated.
    The work is done in three steps so the result is the same on every run:

    1. hash each file and read its capture date (in parallel);
    2. give each file its name, in time then source order, numbering `<n>` so nothing collides;
    3. copy (in parallel), writing the message date into standard metadata on the copy when the
       file has none and exiftool is installed.

    Originals are never moved or changed. A manifest in the archive (`.attachments.json`: relative
    path → SHA-256 of the source) lets a re-run reuse the names it already placed, so nothing is
    duplicated; a file the manifest doesn't know is never overwritten. Conversion (HEIC/PNG/WebP
    to JPG, CAF to AAC) is off unless asked for.
    """

    def __init__(
        self,
        base_attachments_dir: str,
        service_name: str = "iMessage",
        move_files: bool = False,
        max_workers: int = 4,
        convert: bool = False,
    ):
        """
        Args:
            base_attachments_dir: Root of the archive
            service_name: The service the files came from (reporting only)
            move_files: Move instead of copy (originals are then gone; off by default)
            max_workers: Parallel workers for hashing and copying
            convert: Convert HEIC/PNG/WebP to JPG and CAF to AAC while archiving
        """
        self.base_attachments_dir = Path(base_attachments_dir)
        self.service_name = service_name
        self.move_files = move_files
        self.max_workers = max_workers
        self.convert = convert
        self.processor = AttachmentProcessor(service_name)
        self.stats = ProcessingStats()
        self._claimed: set[str] = set()
        self._manifest = self._load_manifest()
        self.base_attachments_dir.mkdir(parents=True, exist_ok=True)

        print(f"AttachmentManager initialized for {service_name}")
        print(f"Base directory: {base_attachments_dir}")
        print(f"Mode: {'MOVE' if move_files else 'COPY'} files")
        print(f"Workers: {max_workers}")

    # --- Manifest ---------------------------------------------------------------------------

    def _load_manifest(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads((self.base_attachments_dir / MANIFEST_NAME).read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_manifest(self) -> None:
        path = self.base_attachments_dir / MANIFEST_NAME
        temporary = path.with_name(path.name + ".part")
        temporary.write_text(json.dumps(self._manifest, indent=2, sort_keys=True), "utf-8")
        os.replace(temporary, path)

    # --- Step 1: look at the file -------------------------------------------------------------

    def _prepare(self, attachment: AttachmentInfo, message_date: datetime) -> AttachmentInfo:
        attachment.processing_status = "processing"
        try:
            if attachment.original_filename.lower().endswith(".pluginpayloadattachment"):
                attachment.processing_status = "skipped"
                attachment.error_message = (
                    "Apple proprietary blob format - not suitable for archival"
                )
                self.stats.skipped_processing += 1
                return attachment
            if not os.path.isfile(attachment.original_path):
                raise FileNotFoundError(f"Source file not found: {attachment.original_path}")
            attachment.file_hash = self.processor.calculate_file_hash(attachment.original_path)
            capture = self.processor.extract_exif_date(attachment.original_path)
            attachment.has_capture_date = capture is not None
            attachment.target_date = capture or message_date
        except Exception as error:
            attachment.processing_status = "failed"
            attachment.error_message = str(error)
            self.stats.failed_processing += 1
            print(f"Error processing {attachment.original_filename}: {error}")
        return attachment

    # --- Step 2: give it a name ---------------------------------------------------------------

    def _extension(self, attachment: AttachmentInfo) -> str:
        extension = Path(attachment.original_filename).suffix.lower()
        if self.convert and extension in self.processor.image_conversion_formats:
            return ".jpg"
        if self.convert and extension in self.processor.audio_conversion_formats:
            return ".aac"
        return extension

    def _folder(self, attachment: AttachmentInfo) -> str:
        person = safe_name(attachment.person)
        owner = f"Groups/{person}" if attachment.is_group else person
        return f"{owner}/Attachments"

    def _plan(self, attachment: AttachmentInfo) -> None:
        """Choose `<folder>/(date) time sender n.ext`: the first number not taken by other content."""
        when = attachment.target_date or datetime.now()
        stamp = when.strftime("(%Y-%m-%d) %H-%M-%S")
        if when.microsecond:
            stamp += f" [{when.microsecond // 1000:03d}]"
        stem = f"{stamp} {safe_name(attachment.sender)}"
        folder = self._folder(attachment)
        extension = self._extension(attachment)
        number = 1
        while True:
            relative = f"{folder}/{stem} {number}{extension}"
            number += 1
            if relative in self._claimed:
                continue
            target = self.base_attachments_dir / relative
            if not target.exists():
                break
            known = self._manifest.get(relative, {}).get("sha256")
            if known and known == attachment.file_hash and not self.convert:
                attachment.reused = True  # this exact content is already here under this name
                break
        self._claimed.add(relative)
        attachment.archival_path = relative
        attachment.archival_filename = relative.rsplit("/", 1)[1]

    # --- Step 3: write it ---------------------------------------------------------------------

    def _write(self, attachment: AttachmentInfo, message_date: datetime) -> AttachmentInfo:
        if attachment.processing_status in ("failed", "skipped") or not attachment.archival_path:
            return attachment
        try:
            target = self.base_attachments_dir / attachment.archival_path
            if attachment.reused:
                attachment.processing_status = "completed"
                self.stats.processed_successfully += 1
                return attachment
            target.parent.mkdir(parents=True, exist_ok=True)
            source = attachment.original_path
            converted = False
            extension = Path(attachment.original_filename).suffix.lower()
            if self.convert and extension in self.processor.image_conversion_formats:
                converted = self.processor.convert_image_to_jpg(source, str(target))
            elif self.convert and extension in self.processor.audio_conversion_formats:
                converted = self.processor.convert_caf_to_aac(source, str(target))
            if converted:
                attachment.conversion_applied = True
                self.stats.conversions_applied += 1
            else:
                if self.convert and target.suffix != extension:
                    # Conversion failed: keep the file as it is, under its own extension.
                    target = target.with_suffix(extension)
                    attachment.archival_path = str(target.relative_to(self.base_attachments_dir))
                    attachment.archival_filename = target.name
                if self.move_files:
                    shutil.move(source, str(target))
                else:
                    shutil.copy2(source, target)
            if not attachment.has_capture_date and target.suffix.lower() in _DATED_FORMATS:
                if self.processor.embed_exif_date(str(target), message_date):
                    attachment.metadata_enhanced = True
                    self.stats.metadata_enhanced += 1
            attachment.processing_status = "completed"
            self.stats.processed_successfully += 1
        except Exception as error:
            attachment.processing_status = "failed"
            attachment.error_message = str(error)
            self.stats.failed_processing += 1
            print(f"Error processing {attachment.original_filename}: {error}")
        return attachment

    # --- The pipeline -------------------------------------------------------------------------

    def process_attachments_parallel(
        self, attachments_with_dates: List[Tuple[AttachmentInfo, datetime]]
    ) -> List[AttachmentInfo]:
        """Archive `(AttachmentInfo, message_time)` pairs; returns them, processed, in time order."""
        if not attachments_with_dates:
            return []
        items = sorted(
            attachments_with_dates, key=lambda it: (_sort_key(it[1]), it[0].original_path)
        )
        print(f"Processing {len(items):,} attachments with {self.max_workers} workers...")
        self.stats.total_attachments = len(items)
        self.stats.total_size_bytes = sum(att.file_size for att, _ in items)
        started = datetime.now()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            with tqdm(total=len(items) * 2, desc="Processing attachments") as pbar:
                prepared = []
                for done in executor.map(lambda it: self._prepare(*it), items):
                    prepared.append(done)
                    pbar.update(1)
                seen: set[str] = set()
                for attachment in prepared:  # sequential and sorted: the names are deterministic
                    if attachment.processing_status == "processing":
                        if attachment.file_hash in seen:
                            attachment.is_duplicate = True  # same content seen earlier in this run
                            self.stats.duplicates_found += 1
                        seen.add(attachment.file_hash or "")
                        self._plan(attachment)
                dates = [when for _, when in items]
                results = []
                for done in executor.map(lambda pair: self._write(*pair), zip(prepared, dates)):
                    results.append(done)
                    pbar.update(1)

        for attachment in results:
            if attachment.processing_status == "completed" and attachment.archival_path:
                self._manifest[attachment.archival_path] = {"sha256": attachment.file_hash or ""}
        self._save_manifest()
        self.stats.processing_time_seconds = (datetime.now() - started).total_seconds()
        self.print_processing_statistics()
        return results

    def print_processing_statistics(self):
        """Print comprehensive processing statistics"""
        print(f"\n📊 ATTACHMENT PROCESSING STATISTICS:")
        print(f"Total attachments: {self.stats.total_attachments:,}")
        print(f"Successfully processed: {self.stats.processed_successfully:,}")
        print(f"Failed processing: {self.stats.failed_processing:,}")
        print(f"Skipped processing: {self.stats.skipped_processing:,} (plugin payload attachments)")
        print(f"Same content seen again: {self.stats.duplicates_found:,}")
        print(f"Format conversions: {self.stats.conversions_applied:,}")
        print(f"Metadata enhanced: {self.stats.metadata_enhanced:,}")
        print(f"Total size processed: {self.stats.total_size_bytes / (1024*1024):.1f} MB")
        print(f"Processing time: {self.stats.processing_time_seconds:.1f} seconds")


#: How iMessage's chat.db records the location of an attachment file.
IMESSAGE_ATTACHMENTS_PREFIX = "~/Library/Messages/Attachments/"


def create_attachment_info_from_message_attachment(
    attachment_dict: Dict[str, Any], source_attachments_dir: str
) -> Optional[AttachmentInfo]:
    """
    Create AttachmentInfo from message attachment dictionary

    This function bridges the gap between the message system and attachment manager.
    Designed to be easily adaptable for different messaging platforms.

    Args:
        attachment_dict: Attachment data from message export
        source_attachments_dir: Base directory where original attachments are stored

    Returns:
        AttachmentInfo object or None if attachment cannot be processed
    """
    try:
        filename = attachment_dict.get("filename", "")
        if not filename:
            return None

        # iMessage records attachments as "~/Library/Messages/Attachments/…": re-root those under
        # the given source folder, so a chat.db copied from another Mac finds its attachments.
        if filename.startswith(IMESSAGE_ATTACHMENTS_PREFIX):
            relative = filename[len(IMESSAGE_ATTACHMENTS_PREFIX) :]
            original_path = os.path.join(os.path.expanduser(source_attachments_dir), relative)
        elif filename.startswith("~/"):
            original_path = os.path.expanduser(filename)
        else:
            original_path = os.path.join(source_attachments_dir, filename)

        # Verify file exists
        if not os.path.exists(original_path):
            print(f"Warning: Attachment file not found: {original_path}")
            return None

        # Extract file info
        file_size = attachment_dict.get("file_size", 0)
        if file_size == 0:
            file_size = os.path.getsize(original_path)

        # Create AttachmentInfo
        return AttachmentInfo(
            original_path=original_path,
            original_filename=os.path.basename(filename),
            mime_type=attachment_dict.get("mime_type"),
            file_size=file_size,
            created_date=None,  # Will be determined from EXIF or message date
        )

    except Exception as e:
        print(f"Error creating AttachmentInfo: {e}")
        return None


# Example usage and testing functions
if __name__ == "__main__":
    # This section can be used for testing the AttachmentManager
    print("AttachmentManager module loaded successfully")
    print("Key classes: AttachmentManager, AttachmentProcessor, AttachmentInfo")
    print("Use create_attachment_info_from_message_attachment() to bridge with message system")
