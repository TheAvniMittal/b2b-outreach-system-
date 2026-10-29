import os
import re
import uuid
import hashlib
import logging
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Segment, Catalogue, OutboundQueue, Contact, AuditReason, record_audit_log

logger = logging.getLogger("catalogue_service")

# Regex to detect mentions of attachments in draft text
ATTACHMENT_MENTION_REGEX = re.compile(
    r"\b(find\s+attached|attached\s+(?:is|are|the|our|catalogue|catalog|pdf)|please\s+find\s+attached|check\s+the\s+attachment|as\s+attached|see\s+attached)\b",
    re.IGNORECASE,
)


def validate_pdf_content(file_bytes: bytes) -> Tuple[bool, Optional[str]]:
    """
    Validates genuine PDF content using magic bytes.
    Rejects files that are not genuine PDFs regardless of file extension.
    """
    if not file_bytes:
        return False, "Uploaded file is empty."
    
    # PDF magic bytes %PDF- must appear in the first 1024 bytes
    header_chunk = file_bytes[:1024]
    if b"%PDF-" not in header_chunk:
        return False, "Uploaded file is not a valid PDF document (missing %PDF- header)."
    
    return True, None


def ensure_catalogues_dir() -> str:
    """Ensures local catalogues directory exists and returns absolute path."""
    cat_dir = os.path.abspath(settings.catalogues_dir)
    os.makedirs(cat_dir, exist_ok=True)
    return cat_dir


def save_uploaded_catalogue(
    file_bytes: bytes,
    original_filename: str,
    segment_id: str,
    operator_name: str,
    session: Session,
) -> Dict[str, Any]:
    """
    Saves an operator-uploaded catalogue PDF for a segment:
    1. Validates magic bytes.
    2. Calculates SHA-256 checksum and size.
    3. Saves securely on local disk outside public directory with random UUID filename.
    4. Links or creates Catalogue record for the segment.
    5. Writes an audit log row.
    """
    is_pdf, error_msg = validate_pdf_content(file_bytes)
    if not is_pdf:
        raise ValueError(error_msg)

    # Size cap check
    size_bytes = len(file_bytes)
    max_bytes = settings.max_attachment_size_mb * 1024 * 1024
    is_oversized = size_bytes > max_bytes

    checksum = hashlib.sha256(file_bytes).hexdigest()

    # Find segment
    segment = session.query(Segment).filter(Segment.segment == segment_id).first()
    if not segment:
        raise ValueError(f"Segment '{segment_id}' does not exist.")

    # Save to disk
    cat_dir = ensure_catalogues_dir()
    safe_storage_name = f"{uuid.uuid4().hex}.pdf"
    storage_path = os.path.join(cat_dir, safe_storage_name)
    with open(storage_path, "wb") as f:
        f.write(file_bytes)

    # Link / update catalogue in DB
    catalogue = None
    if segment.catalogue_id:
        catalogue = session.query(Catalogue).filter(Catalogue.id == segment.catalogue_id).first()

    if not catalogue:
        catalogue = Catalogue(
            name=f"{segment.name} Catalogue",
            catalogue_file=safe_storage_name,
            checksum=checksum,
            file_size_bytes=size_bytes,
            summary=f"Catalogue PDF for {segment.name} segment.",
            registered_term_list=[],
            is_summary_confirmed=True,
            supersession_policy="pin",
        )
        session.add(catalogue)
        session.flush()
        segment.catalogue_id = catalogue.id
    else:
        # Update existing catalogue
        catalogue.catalogue_file = safe_storage_name
        catalogue.checksum = checksum
        catalogue.file_size_bytes = size_bytes

    # Record audit log
    record_audit_log(
        session=session,
        actor=operator_name or "operator",
        action="catalogue_uploaded",
        object_type="catalogue",
        object_id=str(catalogue.id),
        reason=AuditReason.CATALOGUE_UPLOADED,
        prev_state="pending",
        new_state="active",
    )
    session.commit()

    return {
        "catalogue_id": catalogue.id,
        "segment_id": segment.segment,
        "filename": original_filename,
        "storage_file": safe_storage_name,
        "checksum": checksum,
        "file_size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "is_oversized": is_oversized,
        "attachment_enabled": segment.attach_catalogue,
    }


def resolve_send_attachment(
    queue_item: OutboundQueue,
    session: Session,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Pre-send check and attachment resolver:
    1. Checks if contact's segment has catalogue attachment enabled.
    2. Validates segment match and verifies catalogue file integrity on disk.
    3. If file exceeds MAX_ATTACHMENT_SIZE_MB, returns (None, fallback_download_url).
    4. If file is valid and within cap, returns (attachment_dict, None).
    """
    contact = session.query(Contact).filter(Contact.id == queue_item.contact_id).first()
    if not contact or not contact.segment:
        return None, None

    segment = session.query(Segment).filter(Segment.segment == contact.segment).first()
    if not segment or not segment.attach_catalogue:
        return None, None

    if not segment.catalogue_id:
        return None, None

    catalogue = session.query(Catalogue).filter(Catalogue.id == segment.catalogue_id).first()
    if not catalogue or not catalogue.catalogue_file:
        return None, None

    cat_dir = ensure_catalogues_dir()
    file_path = os.path.join(cat_dir, catalogue.catalogue_file)
    if not os.path.exists(file_path):
        logger.warning(f"Catalogue file '{catalogue.catalogue_file}' missing on disk for segment '{segment.segment}'.")
        return None, None

    # Read and verify checksum
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    disk_checksum = hashlib.sha256(file_bytes).hexdigest()
    if catalogue.checksum and disk_checksum != catalogue.checksum:
        logger.error(f"Checksum mismatch for catalogue {catalogue.id} on disk ({disk_checksum} vs {catalogue.checksum}).")
        return None, None

    size_bytes = len(file_bytes)
    max_bytes = settings.max_attachment_size_mb * 1024 * 1024

    # If oversized, generate fallback download link
    if size_bytes > max_bytes:
        download_url = f"{settings.base_catalogue_url}{catalogue.id}"
        logger.info(f"Catalogue {catalogue.id} exceeds {settings.max_attachment_size_mb}MB ({size_bytes} bytes). Using link fallback.")
        return None, download_url

    clean_segment_name = re.sub(r"[^\w\s-]", "", segment.name).strip().replace(" ", "_")
    attachment_filename = f"{clean_segment_name}_Catalogue.pdf"

    return {
        "filename": attachment_filename,
        "content": file_bytes,
        "checksum": disk_checksum,
        "size_bytes": size_bytes,
        "catalogue_id": catalogue.id,
    }, None


def validate_attachment_text_alignment(body_text: str, has_attachment: bool) -> Tuple[bool, Optional[str]]:
    """
    Verifies that the email mentions 'attached' only if a file really is attached.
    If text promises an attachment but no file is attached, returns (False, plain_reason).
    """
    if has_attachment:
        return True, None

    match = ATTACHMENT_MENTION_REGEX.search(body_text)
    if match:
        phrase = match.group(0)
        return False, (
            f"The email draft promises an attached file ('{phrase}'), but catalogue "
            f"attachments are turned off or unavailable for this customer segment."
        )

    return True, None
