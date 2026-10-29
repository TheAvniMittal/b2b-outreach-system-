from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator


class A3Kind(str, Enum):
    HUMAN = "human"
    AUTO_REPLY = "auto_reply"
    BOUNCE_NOTICE = "bounce_notice"
    UNKNOWN = "unknown"


class A3Intent(str, Enum):
    INTERESTED = "interested"
    WANTS_INFO = "wants_info"
    WANTS_CATALOGUE = "wants_catalogue"
    WANTS_PRICING = "wants_pricing"
    WANTS_SAMPLES = "wants_samples"
    WANTS_MEETING = "wants_meeting"
    NEGOTIATION = "negotiation"
    NOT_INTERESTED = "not_interested"
    UNSUBSCRIBE_REQUEST = "unsubscribe_request"
    WRONG_PERSON = "wrong_person"
    REFERRAL = "referral"
    OTHER = "other"
    UNKNOWN = "unknown"


class A3SuppressionSignal(str, Enum):
    NONE = "none"
    UNSUBSCRIBE_REQUEST = "unsubscribe_request"
    LEFT_COMPANY = "left_company"
    INVALID_ADDRESS = "invalid_address"
    DO_NOT_CONTACT = "do_not_contact"
    UNKNOWN = "unknown"


class A3ReplyContract(BaseModel):
    """
    Flat Pydantic contract for A3 ReplyClassifierAgent (VISION.md §7.5).
    Every enum field carries an 'unknown' member.
    """
    kind: A3Kind = Field(
        description="Type of response: human, auto_reply, bounce_notice, or unknown."
    )
    intent: A3Intent = Field(
        description="Intent of the reply: interested, wants_info, wants_catalogue, not_interested, unsubscribe_request, etc."
    )
    suppression_signal: A3SuppressionSignal = Field(
        default=A3SuppressionSignal.NONE,
        description="Suppression signal detected: none, unsubscribe_request, left_company, invalid_address, do_not_contact, unknown."
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Classification confidence score between 0.0 and 1.0."
    )
    summary: Optional[str] = Field(
        default=None,
        max_length=250,
        description="Short summary of the reply (max 250 characters)."
    )
    evidence_quote: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Direct quote from the reply supporting the classification (max 200 characters)."
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, v: Any) -> float:
        try:
            val = float(v)
            if val < 0.0:
                return 0.0
            if val > 1.0:
                return 1.0
            return val
        except (ValueError, TypeError):
            return 0.0

    @classmethod
    def fallback(cls) -> "A3ReplyContract":
        """
        Returns a safe all-unknown instance that fails closed (§7.5, §19.5).
        """
        return cls(
            kind=A3Kind.UNKNOWN,
            intent=A3Intent.UNKNOWN,
            suppression_signal=A3SuppressionSignal.UNKNOWN,
            confidence=0.0,
            summary="Fallback: all-unknown instance",
            evidence_quote=None,
        )
