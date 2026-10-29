import hashlib
import re
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field, field_validator, model_validator


class A2Output(BaseModel):
    """
    Contract v2 for A2 PersonalizationAgent (VISION.md §7.2, Phase 1b).
    Keeps opener, body, and CTA as separate, cleanly validated components.
    """
    subject: str = Field(..., max_length=90, description="Subject line under 90 characters")
    opener: str = Field(..., description="1 sentence opening acknowledging prospect context")
    body: str = Field(..., description="1-2 paragraphs of body prose grounded in claims")
    cta: str = Field(..., description="1 sentence closing call to action")
    cta_id: str = Field(..., description="Chosen CTA identifier matching segment allowed_ctas")
    angle: str = Field(..., description="Chosen angle matching segment allowed_angles")
    claims_used: List[int] = Field(default_factory=list, description="IDs of approved_claims cited in draft")
    product_terms_used: List[str] = Field(default_factory=list, description="Product terms cited in draft")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0.0 to 1.0")
    skip_reason: Optional[str] = Field(default=None, description="Veto reason if skipping prospect (§7.2c)")
    body_text: Optional[str] = Field(default=None, description="Legacy full body text compatibility")
    metadata: Optional[Any] = Field(default=None, description="Legacy metadata compatibility")

    @model_validator(mode="before")
    @classmethod
    def handle_legacy_args(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "body_text" in data and "metadata" in data and "subject" not in data:
                meta = data["metadata"]
                data["subject"] = getattr(meta, "subject", "") or ""
                data["opener"] = data["body_text"]
                data["body"] = ""
                data["cta"] = getattr(meta, "cta", "") or ""
                data["cta_id"] = getattr(meta, "cta", "") or ""
                data["angle"] = getattr(meta, "angle_used", "") or "default"
                data["claims_used"] = getattr(meta, "claims_used", []) or []
                data["product_terms_used"] = getattr(meta, "product_terms_used", []) or []
                data["confidence"] = getattr(meta, "confidence", 0.5)
                data["skip_reason"] = getattr(meta, "skip_reason", None)
        return data

    @field_validator("subject")
    @classmethod
    def validate_subject_length(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Subject cannot be empty.")
        if len(cleaned) > 90:
            raise ValueError(f"Subject exceeds 90 characters: {len(cleaned)} chars.")
        return cleaned

    @model_validator(mode="after")
    def validate_contract_rules(self) -> "A2Output":
        # 1. If veto (skip_reason populated), standard sending rules do not block
        if self.skip_reason and self.skip_reason.strip():
            return self

        # 2. confidence >= 0.7 requires claims_used non-empty (§7.2b)
        if self.confidence >= 0.7 and not self.claims_used:
            raise ValueError(
                f"Contract violation: confidence {self.confidence} >= 0.7 requires claims_used to be non-empty."
            )

        return self

    def is_hold_forced(self) -> bool:
        """cta == unknown or angle == unknown forces HOLD (§7.2b)."""
        return self.cta.lower() == "unknown" or self.angle.lower() == "unknown"

    def is_veto(self) -> bool:
        """skip_reason is populated (§7.2c)."""
        return bool(self.skip_reason and self.skip_reason.strip())

    def assemble_prose_for_validation(self) -> str:
        """
        Assembles prose (opener + body + cta) for rulebook, word count, and sameness validation.
        Ensures Line 1 is strictly 'Hi <First Name>,' on its own line followed by a blank line,
        and prose paragraphs are structured cleanly to satisfy rulebook sentence limits (2-3 sentences/para).
        The mandatory footer is strictly excluded.
        """
        raw_opener = (self.opener or "").strip()
        raw_body = (self.body or "").strip()
        raw_cta = (self.cta or "").strip()

        # 1. Extract greeting if present in opener
        greeting = ""
        opener_body = raw_opener

        m_greeting = re.match(
            r"^(?:Hi|Dear)\s+([A-Za-z]+(?:-[A-Za-z]+)?),\s*(.*)$",
            raw_opener,
            re.IGNORECASE | re.DOTALL,
        )
        if m_greeting:
            first_name = m_greeting.group(1).capitalize()
            greeting = f"Hi {first_name},"
            opener_body = m_greeting.group(2).strip()
        elif raw_opener.lower().startswith("hi ") and "," in raw_opener:
            parts = raw_opener.split(",", 1)
            greeting = parts[0].strip().title() + ","
            opener_body = parts[1].strip()

        if opener_body and opener_body[0].islower():
            opener_body = opener_body[0].upper() + opener_body[1:]

        # Split body into paragraphs
        body_paras = [p.strip() for p in raw_body.split("\n\n") if p.strip()]

        def get_sentences(text: str) -> List[str]:
            return [s.strip() for s in re.split(r"(?<=[.?!])\s+", text) if len(s.strip()) > 3]

        opener_sentences = get_sentences(opener_body) if opener_body else []
        cta_sentences = get_sentences(raw_cta) if raw_cta else []

        all_sentences: List[str] = []
        if opener_body:
            all_sentences.extend(get_sentences(opener_body))
        for p in body_paras:
            all_sentences.extend(get_sentences(p))
        if raw_cta:
            all_sentences.extend(get_sentences(raw_cta))

        built_paragraphs: List[str] = []
        n = len(all_sentences)

        if n == 0:
            built_paragraphs = []
        elif n <= 3:
            built_paragraphs = [" ".join(all_sentences)]
        elif n == 4:
            built_paragraphs = [" ".join(all_sentences[:2]), " ".join(all_sentences[2:])]
        elif n == 5:
            built_paragraphs = [" ".join(all_sentences[:2]), " ".join(all_sentences[2:])]
        elif n == 6:
            built_paragraphs = [" ".join(all_sentences[:3]), " ".join(all_sentences[3:])]
        elif n == 7:
            built_paragraphs = [
                " ".join(all_sentences[:2]),
                " ".join(all_sentences[2:4]),
                " ".join(all_sentences[4:])
            ]
        elif n == 8:
            built_paragraphs = [
                " ".join(all_sentences[:2]),
                " ".join(all_sentences[2:5]),
                " ".join(all_sentences[5:])
            ]
        else:
            # 9 or more sentences: distribute into 3 paragraphs of 3 sentences (capped at 9 to prevent validator overflow)
            s = all_sentences[:9]
            built_paragraphs = [
                " ".join(s[:3]),
                " ".join(s[3:6]),
                " ".join(s[6:9])
            ]

        all_parts = []
        if greeting:
            all_parts.append(greeting)
        all_parts.extend(p for p in built_paragraphs if p)

        return "\n\n".join(p for p in all_parts if p)

    def assemble_full_email(self, footer: str) -> str:
        """Assembles the final deliverable email with the rulebook footer attached."""
        prose = self.assemble_prose_for_validation()
        clean_footer = footer.strip()
        if clean_footer:
            return f"{prose}\n\n--\n{clean_footer}"
        return prose

    def compute_opening_hash(self) -> str:
        """Computes 8-char sha256 hash of opening sentence for sameness tracking."""
        source_text = self.opener or getattr(self, "body_text", "") or ""
        lines = [line.strip() for line in source_text.split("\n") if line.strip()]
        if not lines:
            return ""
        # Skip line 1 greeting if matches standard greeting
        start_idx = 0
        if lines and re.match(r"^(?:Hi|Dear)\s+[A-Za-z]+,\s*$", lines[0], re.IGNORECASE):
            start_idx = 1
        remaining_text = " ".join(lines[start_idx:]) if start_idx < len(lines) else lines[0]
        sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", remaining_text) if s.strip()]
        first_sentence = sentences[0] if sentences else remaining_text
        normalized = " ".join(re.findall(r"\b[\w'-]+\b", first_sentence.lower()))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]

    def verify_segment_conformance(
        self,
        allowed_angles: List[str],
        allowed_ctas: List[Any],
        segment_claim_ids: List[int],
    ) -> List[str]:
        """
        Enforces that cta_id is in the segment's allowed CTAs and claims_used belong to this segment.
        """
        errors = []
        if self.is_veto():
            return errors

        # Verify claims_used belong to segment
        valid_claims_set = set(segment_claim_ids)
        for cid in self.claims_used:
            if cid not in valid_claims_set:
                errors.append(f"Claim ID [{cid}] does not belong to this contact's segment catalogue.")

        # Verify cta_id in allowed_ctas
        # allowed_ctas can be list of strings or list of dicts with 'id'/'text'
        valid_cta_ids = set()
        for item in allowed_ctas:
            if isinstance(item, dict):
                valid_cta_ids.add(str(item.get("id", "")).lower())
                valid_cta_ids.add(str(item.get("text", "")).lower())
            else:
                valid_cta_ids.add(str(item).lower())

        if self.cta_id.lower() not in valid_cta_ids and valid_cta_ids:
            errors.append(f"CTA ID '{self.cta_id}' is not in the segment's allowed CTAs list.")

        return errors


# Backward compatibility wrapper for existing tests/callers
class MessageMetadata(BaseModel):
    subject: str = Field(..., max_length=90)
    angle_used: str = Field(...)
    cta: str = Field(...)
    claims_used: List[int] = Field(default_factory=list)
    product_terms_used: List[str] = Field(default_factory=list)
    opening_line_hash: str = Field(default="")
    confidence: float = Field(..., ge=0.0, le=1.0)
    skip_reason: Optional[str] = Field(default=None)

    @field_validator("subject")
    @classmethod
    def validate_subject_length(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) > 90:
            raise ValueError(f"Subject exceeds 90 characters: {len(cleaned)} chars.")
        return cleaned

    @model_validator(mode="after")
    def validate_metadata_rules(self) -> "MessageMetadata":
        if self.skip_reason and self.skip_reason.strip():
            return self
        if self.confidence >= 0.7 and not self.claims_used:
            raise ValueError(
                f"Contract violation: confidence {self.confidence} >= 0.7 requires claims_used to be non-empty."
            )
        return self

    def is_hold_forced(self) -> bool:
        return self.cta.lower() == "unknown" or self.angle_used.lower() == "unknown"

    def is_veto(self) -> bool:
        return bool(self.skip_reason and self.skip_reason.strip())
