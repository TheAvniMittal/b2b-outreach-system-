import pytest
from app.db.session import get_db_session
from app.db.models import AuditLog, AuditReason, record_audit_log

def test_audit_log_accepts_audit_reason_enum():
    with get_db_session() as session:
        entry = record_audit_log(
            session,
            actor="system",
            action="state_transition",
            object_type="queue_item",
            object_id="101",
            reason=AuditReason.LEASE_EXPIRED,
            prev_state="rendering",
            new_state="eligible"
        )
        session.flush()
        assert entry.id is not None
        assert entry.reason == AuditReason.LEASE_EXPIRED
        assert entry.retention_class == "audit_log"

        # Query back from database
        persisted = session.query(AuditLog).filter_by(id=entry.id).first()
        assert persisted is not None
        assert persisted.reason == AuditReason.LEASE_EXPIRED
        assert persisted.actor == "system"


def test_audit_log_rejects_raw_string():
    """
    VISION.md §14.8f:
    'The audit-writing helper accepts an enum and refuses a string.
    A CI assertion fails the build if any call site passes free text.'
    """
    with get_db_session() as session:
        with pytest.raises(TypeError) as exc_info:
            record_audit_log(
                session,
                actor="system",
                action="state_transition",
                object_type="queue_item",
                object_id="102",
                reason="lease_expired",  # Raw string must be rejected!
                prev_state="rendering",
                new_state="eligible"
            )
        assert "Audit log reason must be an instance of AuditReason Enum" in str(exc_info.value)
