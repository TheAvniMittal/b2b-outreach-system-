import re
from dataclasses import dataclass
from typing import Optional, Set
import dns.resolver

# Standard RFC 5322 compliant regex supporting subaddressing (+)
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

# Common disposable email provider domains
DISPOSABLE_DOMAINS: Set[str] = {
    "mailinator.com",
    "tempmail.com",
    "guerrillamail.com",
    "10minutemail.com",
    "sharklasers.com",
    "yopmail.com",
    "trashmail.com",
    "dispostable.com",
    "temp-mail.org",
    "fakeinbox.com",
    "throwawaymail.com",
    "getairmail.com",
    "mytemp.email",
}

@dataclass
class VerificationResult:
    is_valid: bool
    status: str  # "valid", "invalid"
    stage_reached: int  # 0, 1, 2
    reason: Optional[str] = None


def verify_email_stages_0_to_2(email: str, dns_timeout: float = 5.0) -> VerificationResult:
    """
    Email verification ladder stages 0-2 (VISION.md §12.1):
    Stage 0: Syntax regex
    Stage 1: Domain resolves + MX record exists (dnspython)
    Stage 2: Disposable/temp domain check
    """
    clean_email = email.strip()

    # Stage 0: Syntax
    if not clean_email or not EMAIL_REGEX.match(clean_email):
        return VerificationResult(
            is_valid=False,
            status="invalid",
            stage_reached=0,
            reason="invalid_syntax"
        )

    parts = clean_email.split("@")
    if len(parts) != 2:
        return VerificationResult(
            is_valid=False,
            status="invalid",
            stage_reached=0,
            reason="invalid_syntax"
        )

    domain = parts[1].lower().strip()

    # Stage 1: Domain resolves + MX record exists
    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = dns_timeout
        resolver.timeout = dns_timeout
        mx_records = resolver.resolve(domain, "MX")
        if not mx_records or len(mx_records) == 0:
            return VerificationResult(
                is_valid=False,
                status="invalid",
                stage_reached=1,
                reason="no_mx_record"
            )
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers, dns.exception.Timeout):
        return VerificationResult(
            is_valid=False,
            status="invalid",
            stage_reached=1,
            reason="no_mx_record"
        )
    except Exception as e:
        return VerificationResult(
            is_valid=False,
            status="invalid",
            stage_reached=1,
            reason=f"dns_lookup_error: {str(e)}"
        )

    # Stage 2: Disposable domain check
    if domain in DISPOSABLE_DOMAINS:
        return VerificationResult(
            is_valid=False,
            status="invalid",
            stage_reached=2,
            reason="disposable_domain"
        )

    # Passed stages 0, 1, 2
    return VerificationResult(
        is_valid=True,
        status="valid",
        stage_reached=2,
        reason=None
    )
