import pytest
from app.config import settings
from app.db.session import get_db_session
from app.db.models import AuditLog, AuditReason
from app.pipeline.providers.smtp_provider import SMTPEmailProvider, SeedAllowlistViolationError, SEED_ALLOWLIST


def test_seed_allowlist_contains_all_17_test_addresses():
    assert len(SEED_ALLOWLIST) == 17
    assert "avnimittal900@gmail.com" in SEED_ALLOWLIST
    for i in range(1, 10):
        assert f"avnimittal900+prospect{i}@gmail.com" in SEED_ALLOWLIST
    for new_addr in [
        "avnimittal175@gmail.com",
        "bhoomi.thakur168@gmail.com",
        "abhinavusesthis@gmail.com",
        "ayushofficialuse@gmail.com",
        "abhinavarcade70@gmail.com",
        "mittalavni019@gmail.com",
        "mittalavni89@gmail.com",
    ]:
        assert new_addr in SEED_ALLOWLIST


def test_non_allowlisted_address_raises_and_writes_audit_row(monkeypatch):
    """
    Attempt to send to a non-allowlisted address while LIVE_EMAIL_SENDING=false
    MUST raise SeedAllowlistViolationError AND write an audit row (§18.2).
    """
    monkeypatch.setattr(settings, "live_email_sending", False)

    provider = SMTPEmailProvider(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_user="test@example.com",
        smtp_password="password",
    )

    with get_db_session() as db:
        initial_audit_count = (
            db.query(AuditLog)
            .filter(AuditReason.SEED_ALLOWLIST_VIOLATION == AuditLog.reason)
            .count()
        )

        with pytest.raises(SeedAllowlistViolationError, match="SEED_ALLOWLIST violation"):
            provider.send_email(
                to_email="unauthorized_buyer@competitor.com",
                subject="Test Subject",
                body_text="Test Body",
                session=db,
                contact_id=999,
            )

        new_audit_count = (
            db.query(AuditLog)
            .filter(AuditReason.SEED_ALLOWLIST_VIOLATION == AuditLog.reason)
            .count()
        )
        assert new_audit_count == initial_audit_count + 1

        latest_audit = (
            db.query(AuditLog)
            .filter(AuditReason.SEED_ALLOWLIST_VIOLATION == AuditLog.reason)
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert latest_audit.actor == "smtp_provider"
        assert latest_audit.reason == AuditReason.SEED_ALLOWLIST_VIOLATION
        assert latest_audit.object_id == "999"


def test_allowlisted_address_passes_allowlist_check(monkeypatch):
    """Allowlisted address clears the security check and attempts SMTP delivery."""
    monkeypatch.setattr(settings, "live_email_sending", False)

    # Mock smtplib to avoid connecting to network in unit test
    class MockSMTP:
        def __init__(self, *args, **kwargs):
            pass
        def ehlo(self):
            pass
        def starttls(self):
            pass
        def login(self, *args):
            pass
        def send_message(self, msg):
            pass
        def quit(self):
            pass

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", MockSMTP)

    provider = SMTPEmailProvider(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_user="test@example.com",
        smtp_password="password",
    )

    result = provider.send_email(
        to_email="avnimittal900+prospect1@gmail.com",
        subject="Allowlisted Test",
        body_text="Hello",
        custom_headers={"X-Narayani-Send-Key": "sk_test_123"},
    )
    assert result.success is True
    assert "sk_test_123" in result.provider_message_id
