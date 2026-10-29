import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Union, Optional
from sqlalchemy.orm import Session

from app.db.models import Contact, Segment, Suppression, OutboundQueue, AuditReason, record_audit_log
from app.pipeline.verify_email import verify_email_stages_0_to_2
from app.pipeline.queue import enqueue_message

@dataclass
class ImportRowError:
    row_num: int
    data: Dict[str, Any]
    error: str


@dataclass
class ImportReport:
    total_rows: int = 0
    imported_contacts: List[Contact] = field(default_factory=list)
    rejected_rows: List[ImportRowError] = field(default_factory=list)
    enqueued_count: int = 0

    @property
    def imported_count(self) -> int:
        return len(self.imported_contacts)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected_rows)


def hash_email(email: str) -> str:
    """One-way SHA-256 hash of normalized email (VISION.md §14.8b)."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def import_prospects_csv(
    session: Session,
    csv_source: Union[str, Path, io.StringIO, io.TextIOBase],
    suppress_check: bool = True,
    verify_emails: bool = True,
) -> ImportReport:
    """
    Imports prospects from a CSV source.
    Enforces that 'segment' is a strictly REQUIRED column — a row missing it
    is rejected at import, never guessed (VISION.md §18.3, standing in for deferred A6).
    """
    report = ImportReport()

    # Read CSV content
    if isinstance(csv_source, Path):
        content = csv_source.read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(content))
    elif isinstance(csv_source, str):
        # Check if it's a file path or raw CSV string
        path_candidate = Path(csv_source)
        if path_candidate.is_file():
            content = path_candidate.read_text(encoding="utf-8")
            reader = csv.DictReader(io.StringIO(content))
        else:
            reader = csv.DictReader(io.StringIO(csv_source))
    else:
        reader = csv.DictReader(csv_source)

    # Cache valid segments from DB
    valid_segments = {s.segment for s in session.query(Segment.segment).all()}

    row_num = 1  # 1-indexed (row 1 is first data row)
    for row in reader:
        row_num += 1
        report.total_rows += 1

        # Strip whitespace from keys and values
        clean_row = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}

        # 1. Segment validation: strictly REQUIRED
        segment_val = clean_row.get("segment", "").strip()
        if not segment_val:
            report.rejected_rows.append(
                ImportRowError(
                    row_num=row_num,
                    data=clean_row,
                    error="Missing required column 'segment'. Segment is never guessed (§18.3)."
                )
            )
            continue

        if segment_val not in valid_segments:
            report.rejected_rows.append(
                ImportRowError(
                    row_num=row_num,
                    data=clean_row,
                    error=f"Invalid segment '{segment_val}'. Must match a known configured segment."
                )
            )
            continue

        # 2. Email presence
        email_val = clean_row.get("email", "").strip().lower()
        if not email_val:
            report.rejected_rows.append(
                ImportRowError(
                    row_num=row_num,
                    data=clean_row,
                    error="Missing required column 'email'."
                )
            )
            continue

        company_name = clean_row.get("company_name", "").strip()
        contact_name = clean_row.get("contact_name", "").strip()
        if not company_name or not contact_name:
            report.rejected_rows.append(
                ImportRowError(
                    row_num=row_num,
                    data=clean_row,
                    error="Missing required fields: company_name and contact_name are mandatory."
                )
            )
            continue

        # 3. Check suppression list via SHA-256 hash
        e_hash = hash_email(email_val)
        is_suppressed = False
        if suppress_check:
            suppression_entry = session.query(Suppression).filter_by(email_hash=e_hash).first()
            if suppression_entry:
                is_suppressed = True

        # 4. Email verification ladder (Stages 0-2)
        v_status = "unknown"
        v_reason = None
        v_stage = None
        v_time = None

        if verify_emails:
            v_res = verify_email_stages_0_to_2(email_val)
            v_status = v_res.status
            v_reason = v_res.reason
            v_stage = v_res.stage_reached
            v_time = datetime.now(timezone.utc)

        initial_status = "suppressed" if is_suppressed else ("pending" if v_status == "valid" else "held")

        # Check if contact already exists
        contact = session.query(Contact).filter_by(email=email_val).first()
        if contact:
            contact.company_name = company_name
            contact.segment = segment_val
            contact.company_domain = clean_row.get("company_domain") or None
            contact.contact_name = contact_name
            contact.designation = clean_row.get("designation") or None
            contact.country = clean_row.get("country") or None
            contact.product_interest = clean_row.get("product_interest") or None
            contact.notes = clean_row.get("notes") or None
            contact.verification_status = v_status
            contact.verification_reason = v_reason
            contact.verification_stage_reached = v_stage
            contact.verified_at = v_time
            if is_suppressed:
                contact.status = "suppressed"
        else:
            contact = Contact(
                company_name=company_name,
                segment=segment_val,
                company_domain=clean_row.get("company_domain") or None,
                contact_name=contact_name,
                designation=clean_row.get("designation") or None,
                country=clean_row.get("country") or None,
                email=email_val,
                product_interest=clean_row.get("product_interest") or None,
                notes=clean_row.get("notes") or None,
                verification_status=v_status,
                verification_reason=v_reason,
                verification_stage_reached=v_stage,
                verified_at=v_time,
                status=initial_status,
                retention_class="prospect_pii",
            )
            session.add(contact)

        session.flush()

        # Audit log entry for import & verification
        record_audit_log(
            session,
            actor="system",
            action="import_prospect",
            object_type="contact",
            object_id=str(contact.id),
            reason=AuditReason.IMPORT_VERIFIED,
            new_state=v_status,
        )

        # Enqueue valid, unsuppressed prospect into outbound queue (§Phase 4)
        if contact.status == "pending" and not is_suppressed:
            existing_queue = session.query(OutboundQueue).filter(
                OutboundQueue.contact_id == contact.id,
                OutboundQueue.status.in_(["eligible", "rendering", "pending", "held", "sent"])
            ).first()
            if not existing_queue:
                enqueue_message(session, contact_id=contact.id, campaign_step="intro")
                report.enqueued_count += 1

        report.imported_contacts.append(contact)

    session.commit()
    return report
