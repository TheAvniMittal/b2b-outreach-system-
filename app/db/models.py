import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float, Date, DateTime,
    ForeignKey, func, Enum as SAEnum, JSON
)
from sqlalchemy.orm import declarative_base, relationship, Session, Mapped, mapped_column

Base = declarative_base()

class AuditReason(str, Enum):
    UNSUBSCRIBE_REQUEST = "unsubscribe_request"
    HARD_BOUNCE = "hard_bounce"
    HUMAN_REPLY = "human_reply"
    ADMIN_PAUSE = "admin_pause"
    VALIDATOR_HOLD = "validator_hold"
    VETO_LIMIT = "veto_limit"
    LEASE_EXPIRED = "lease_expired"
    WORKER_CRASH = "worker_crash"
    DEAD_LETTER = "dead_letter"
    IMPORT_VERIFIED = "import_verified"
    INITIAL_ENQUEUE = "initial_enqueue"
    RECONCILE_FOUND = "reconcile_found"
    RECONCILE_NOT_FOUND = "reconcile_not_found"
    RECONCILE_AMBIGUOUS_HOLD = "reconcile_ambiguous_hold"
    CAP_ENFORCED = "cap_enforced"
    SEED_ALLOWLIST_VIOLATION = "seed_allowlist_violation"
    SUPPRESSION_ENFORCED = "suppression_enforced"
    UNSUPPRESSION = "unsuppression"
    TONE_APPROVED = "tone_approved"
    TONE_ROLLBACK = "tone_rollback"
    POLICY_BLOCK = "policy_block"
    SPAM_COMPLAINT = "spam_complaint"
    SENDING_STARTED = "sending_started"
    SENDING_STOPPED = "sending_stopped"
    AUTO_STOP_TRIGGERED = "auto_stop_triggered"
    AUTO_STOP_CLEARED = "auto_stop_cleared"
    SEGMENT_PAUSED = "segment_paused"
    SEGMENT_RESUMED = "segment_resumed"
    OUT_OF_OFFICE_SNOOZE = "out_of_office_snooze"
    INBOUND_REPLY_CLASSIFIED = "inbound_reply_classified"
    OPERATOR_EMAIL_SENT = "operator_email_sent"
    CATALOGUE_UPLOADED = "catalogue_uploaded"
    CATALOGUE_ATTACHMENT_TOGGLED = "catalogue_attachment_toggled"
    CLAIMS_UPLOADED = "claims_uploaded"
    RULEBOOK_UPDATED = "rulebook_updated"


class Catalogue(Base):
    __tablename__ = "catalogues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    catalogue_file: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registered_term_list: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_summary_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supersession_policy: Mapped[str] = mapped_column(String(30), nullable=False, default="pin")
    retention_class: Mapped[str] = mapped_column(String(50), nullable=False, default="operational_config")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    segments = relationship("Segment", back_populates="catalogue")


class Segment(Base):
    __tablename__ = "segments"

    segment: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    catalogue_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("catalogues.id", ondelete="SET NULL"), nullable=True
    )
    attach_catalogue: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_angles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    allowed_ctas: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    approved_claim_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    forbidden_topics: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    target_words_min: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    target_words_max: Mapped[int] = mapped_column(Integer, nullable=False, default=150)
    register_spec: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cadence_override: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    opportunity_weights: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    retention_class: Mapped[str] = mapped_column(String(50), nullable=False, default="operational_config")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    catalogue = relationship("Catalogue", back_populates="segments")
    claims = relationship("ApprovedClaim", back_populates="segment_rel")
    contacts = relationship("Contact", back_populates="segment_rel")


class ApprovedClaim(Base):
    __tablename__ = "approved_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    segment: Mapped[str] = mapped_column(String(50), ForeignKey("segments.segment"), nullable=False)
    product_category: Mapped[str] = mapped_column(String(100), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(50), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    valid_from: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    last_reviewed_at: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    retention_class: Mapped[str] = mapped_column(String(50), nullable=False, default="operational_config")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    segment_rel = relationship("Segment", back_populates="claims")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    segment: Mapped[str] = mapped_column(String(50), ForeignKey("segments.segment"), nullable=False)
    company_domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    designation: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    product_interest: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verified_signal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown")
    verification_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    verification_stage_reached: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    verified_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    opportunity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    due_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_class: Mapped[str] = mapped_column(String(50), nullable=False, default="prospect_pii")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    segment_rel = relationship("Segment", back_populates="contacts")
    queue_items = relationship("OutboundQueue", back_populates="contact", cascade="all, delete-orphan")
    send_keys = relationship("SendKey", back_populates="contact", cascade="all, delete-orphan")


class OutboundQueue(Base):
    __tablename__ = "outbound_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_step: Mapped[str] = mapped_column(String(50), nullable=False, default="intro")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="eligible", index=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    locked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expiry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    veto_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processing_started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    failed_validator: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    due_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    hold_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rendered_subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rendered_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attachment_checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    send_key_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    retention_class: Mapped[str] = mapped_column(String(50), nullable=False, default="sent_messages")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    contact = relationship("Contact", back_populates="queue_items")
    send_keys = relationship("SendKey", back_populates="queue_item", cascade="all, delete-orphan")


class SendKey(Base):
    __tablename__ = "send_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    queue_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("outbound_queue.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="sending")
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    retention_class: Mapped[str] = mapped_column(String(50), nullable=False, default="provider_message_ids")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    queue_item = relationship("OutboundQueue", back_populates="send_keys")
    contact = relationship("Contact", back_populates="send_keys")


class Suppression(Base):
    __tablename__ = "suppression"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    contact_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    retention_class: Mapped[str] = mapped_column(String(50), nullable=False, default="permanent")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    object_id: Mapped[str] = mapped_column(String(100), nullable=False)
    prev_state: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    new_state: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    reason: Mapped[AuditReason] = mapped_column(
        SAEnum(AuditReason, name="audit_reason_enum", native_enum=True, values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )
    rulebook_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    retention_class: Mapped[str] = mapped_column(String(50), nullable=False, default="audit_log")


def record_audit_log(
    session: Session,
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: str,
    reason: AuditReason,
    prev_state: Optional[str] = None,
    new_state: Optional[str] = None,
    rulebook_version: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> AuditLog:
    if not isinstance(reason, AuditReason):
        raise TypeError(
            f"Audit log reason must be an instance of AuditReason Enum, got {type(reason).__name__}: {reason}"
        )
    log_entry = AuditLog(
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        prev_state=prev_state,
        new_state=new_state,
        reason=reason,
        rulebook_version=rulebook_version,
        prompt_version=prompt_version,
        retention_class="audit_log",
    )
    session.add(log_entry)
    return log_entry


class AIUsageLog(Base):
    __tablename__ = "ai_usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    call_type: Mapped[str] = mapped_column(String(50), nullable=False)
    tokens_prompt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_completion: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    retention_class: Mapped[str] = mapped_column(String(50), nullable=False, default="operational_audit")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ToneStudioSession(Base):
    __tablename__ = "tone_studio_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    segment: Mapped[str] = mapped_column(String(50), ForeignKey("segments.segment"), nullable=False, index=True)
    messages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ToneReference(Base):
    __tablename__ = "tone_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    segment: Mapped[str] = mapped_column(String(50), ForeignKey("segments.segment"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    draft_body: Mapped[str] = mapped_column(Text, nullable=False)
    tone_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="approved")  # approved, rejected, archived
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="operator")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    validator_results: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    retention_class: Mapped[str] = mapped_column(String(50), nullable=False, default="operational_config")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class SystemControl(Base):
    __tablename__ = "system_controls"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RulebookConfig(Base):
    __tablename__ = "rulebook_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Rulebook v1")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    banned_openers: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: [
            "i hope this email finds you well",
            "i hope you are doing well",
            "i trust this email finds you in good health",
        ],
    )
    banned_phrases: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: ["reaching out", "leverage", "synergy", "circle back"],
    )
    max_paragraphs: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    max_sentences_per_paragraph: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    allow_exclamation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_all_caps: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    retention_class: Mapped[str] = mapped_column(String(50), nullable=False, default="operational_config")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())



