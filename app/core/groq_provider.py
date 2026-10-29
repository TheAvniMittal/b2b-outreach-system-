import logging
import time
import os
from datetime import datetime, timezone, time as dt_time
from typing import Optional, Dict, Any, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session
from langchain_groq import ChatGroq

from app.config import settings
from app.db.models import AIUsageLog

logger = logging.getLogger("groq_provider")


class GroqProviderError(Exception):
    """Raised when Groq API encounters a network, auth, rate-limit, or server error."""
    def __init__(self, message: str, original_error: Optional[Exception] = None, retry_after: Optional[float] = None):
        super().__init__(message)
        self.original_error = original_error
        self.retry_after = retry_after

MODEL_LIMITS: Dict[str, Dict[str, Any]] = {
    "qwen/qwen3.8-27b": {
        "rpm": 3,
        "rpd": 14400,
        "tpm": 15000,
        "tpd": 500000,
        "min_interval": 18.0,
    },
    "openai/gpt-oss-20b": {
        "rpm": 30,
        "rpd": 14400,
        "tpm": 6000,
        "tpd": 500000,
        "min_interval": 2.0,
    },
    "llama-3.3-70b-versatile": {
        "rpm": 30,
        "rpd": 1000,
        "tpm": 12000,
        "tpd": 100000,
        "min_interval": 1.5,
    },
    "llama-3.1-8b-instant": {
        "rpm": 30,
        "rpd": 14400,
        "tpm": 6000,
        "tpd": 500000,
        "min_interval": 1.5,
    },
}

DEFAULT_MODEL_LIMITS: Dict[str, Any] = {
    "rpm": 10,
    "rpd": 1000,
    "tpm": 6000,
    "tpd": 100000,
    "min_interval": 3.0,
}


class GroqRateLimiter:
    """
    Token-aware rate limiter and daily budget tracker for Groq.
    - Daily token totals derived directly from ai_usage_log database table.
    - Per-minute token and request pacing to prevent HTTP 429 bursts.
    - 80% daily budget warnings.
    - Plain-language sending stop when daily tokens are nearly exhausted.
    """

    def __init__(self):
        self._last_call_time: float = 0.0
        self._minute_window_start: float = time.time()
        self._minute_requests: int = 0
        self._minute_tokens: int = 0

    def get_today_reset_boundary(self) -> datetime:
        """Returns the UTC datetime when today's limits reset (default 00:00 UTC)."""
        now = datetime.now(timezone.utc)
        return datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone.utc)

    def get_daily_usage(self, session: Session, model_name: str) -> Dict[str, int]:
        """Queries ai_usage_log for tokens and requests used since 00:00 UTC today."""
        start_of_day = self.get_today_reset_boundary()
        row = (
            session.query(
                func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
                func.count(AIUsageLog.id),
            )
            .filter(
                AIUsageLog.model_name == model_name,
                AIUsageLog.created_at >= start_of_day,
                AIUsageLog.status == "success",
            )
            .first()
        )
        total_tokens = int(row[0]) if row else 0
        total_requests = int(row[1]) if row else 0
        return {"total_tokens": total_tokens, "total_requests": total_requests}

    def check_can_send(self, session: Session, model_name: str) -> Tuple[bool, str]:
        """
        Safety check called before claiming/sending emails:
        Returns (True, "OK") or (False, "Plain explanation message").
        Halts when daily tokens reach 95% of cap.
        """
        limits = MODEL_LIMITS.get(model_name, DEFAULT_MODEL_LIMITS)
        usage = self.get_daily_usage(session, model_name)
        tpd_cap = limits["tpd"]
        used_tokens = usage["total_tokens"]

        if used_tokens >= int(tpd_cap * 0.95):
            msg = (
                f"Paused: Groq daily token allowance nearly exhausted ({used_tokens:,} of {tpd_cap:,} tokens used today). "
                f"Outreach will resume after reset at 00:00 UTC."
            )
            return False, msg

        if used_tokens >= int(tpd_cap * 0.80):
            logger.warning(
                f"Groq warning: {model_name} has consumed 80%+ of daily limit ({used_tokens:,}/{tpd_cap:,} tokens)."
            )

        return True, "OK"

    def acquire(self, session: Session, model_name: str, estimated_tokens: int = 1500) -> None:
        """Enforces per-minute pacing and minimum interval between requests."""
        limits = MODEL_LIMITS.get(model_name, DEFAULT_MODEL_LIMITS)
        now = time.time()

        # Reset minute window
        if now - self._minute_window_start >= 60.0:
            self._minute_window_start = now
            self._minute_requests = 0
            self._minute_tokens = 0

        # Check minute rate limits
        if (self._minute_requests >= limits["rpm"]) or (self._minute_tokens + estimated_tokens > limits["tpm"]):
            sleep_needed = max(1.0, 60.0 - (now - self._minute_window_start))
            logger.info(f"Pacing Groq request: sleeping {sleep_needed:.2f}s for minute rate window.")
            time.sleep(sleep_needed)
            self._minute_window_start = time.time()
            self._minute_requests = 0
            self._minute_tokens = 0

        # Minimum spacing between calls
        min_spacing = float(limits.get("min_interval", 1.5))
        elapsed_since_last = time.time() - self._last_call_time
        if elapsed_since_last < min_spacing:
            time.sleep(min_spacing - elapsed_since_last)

        self._minute_requests += 1
        self._minute_tokens += estimated_tokens
        self._last_call_time = time.time()


limiter = GroqRateLimiter()


def log_ai_usage(
    session: Session,
    agent_name: str,
    model_name: str,
    call_type: str,
    tokens_prompt: int,
    tokens_completion: int,
    status: str = "success",
) -> AIUsageLog:
    """Records an immutable content-free row in ai_usage_log table."""
    total = tokens_prompt + tokens_completion
    entry = AIUsageLog(
        agent_name=agent_name,
        model_name=model_name,
        call_type=call_type,
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
        total_tokens=total,
        status=status,
        retention_class="operational_audit",
    )
    session.add(entry)
    session.commit()
    return entry


import random
import re


def extract_retry_after(error: Exception) -> Optional[float]:
    """Attempts to extract Retry-After duration in seconds from an exception or its headers."""
    # 1. From response headers if present
    response = getattr(error, "response", None)
    if response is not None:
        headers = getattr(response, "headers", {})
        if "retry-after" in headers:
            try:
                return float(headers["retry-after"])
            except (ValueError, TypeError):
                pass
        if "retry-after-ms" in headers:
            try:
                return float(headers["retry-after-ms"]) / 1000.0
            except (ValueError, TypeError):
                pass

    # 2. From error message string
    err_msg = str(error)
    match = re.search(r"retry(?:\s+after)?\s+([0-9.]+)\s*(?:s|seconds)?", err_msg, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except (ValueError, TypeError):
            pass

    return None


class GroqCircuitBreaker:
    """
    Shared circuit breaker for Groq AI calls:
    - 401/403 or rejected key: marks key rejected, red banner, stops without retries.
    - Daily limit reached: stops new work until 00:00 UTC reset.
    - Transient (429 per-minute, timeouts, connection errors, 5xx):
      pauses all callers briefly together with exponential backoff and jitter, honoring retry-after.
    """

    def __init__(self):
        self._circuit_open_until: float = 0.0
        self._open_reason: str = ""
        self._key_rejected: bool = False
        self._daily_limit_hit: bool = False
        self._consecutive_failures: int = 0

    def reset(self):
        self._circuit_open_until = 0.0
        self._open_reason = ""
        self._key_rejected = False
        self._daily_limit_hit = False
        self._consecutive_failures = 0

    def is_key_rejected(self) -> bool:
        return self._key_rejected

    def is_daily_limit_hit(self) -> bool:
        return self._daily_limit_hit

    def is_available(self) -> Tuple[bool, str]:
        if self._key_rejected:
            return False, "Groq rejected the key. Update it in Connections."
        if self._daily_limit_hit:
            return False, "Groq daily token allowance reached. Outreach will resume after reset at 00:00 UTC."
        now = time.time()
        if now < self._circuit_open_until:
            wait_sec = max(1, int(self._circuit_open_until - now))
            return False, f"AI service temporarily paused: {self._open_reason} (resuming in {wait_sec}s)"
        return True, "OK"

    def record_success(self):
        self._consecutive_failures = 0

    def record_failure(self, error: Exception, retry_after: Optional[float] = None) -> Tuple[str, str]:
        """
        Classifies error, updates circuit breaker, and returns (error_type, plain_message).
        error_type is one of: 'auth_rejected', 'daily_limit', 'transient', 'unknown'
        """
        err_str = str(error).lower()

        # 1. 401 / 403 / invalid api key
        if (
            "401" in err_str
            or "403" in err_str
            or "invalid_api_key" in err_str
            or "unauthorized" in err_str
            or "authentication" in err_str
        ):
            self._key_rejected = True
            msg = "Groq rejected the key. Update it in Connections."
            logger.critical(f"Groq API key rejected: {error}")
            return "auth_rejected", msg

        # 2. Daily limit reached
        if "daily" in err_str and ("limit" in err_str or "quota" in err_str or "exceeded" in err_str):
            self._daily_limit_hit = True
            msg = "Groq daily token allowance reached. Outreach will resume after reset at 00:00 UTC."
            logger.warning(f"Groq daily limit reached: {error}")
            return "daily_limit", msg

        # 3. Transient errors (429 rate limit, 500/502/503/504, timeout, connection)
        self._consecutive_failures += 1

        if retry_after is not None and retry_after > 0:
            pause_time = retry_after
        else:
            base = min(60.0, 2.0 ** min(self._consecutive_failures, 5))
            jitter = random.uniform(0.5, 1.5)
            pause_time = base + jitter

        self._circuit_open_until = time.time() + pause_time
        self._open_reason = f"Provider busy or timed out (pausing {pause_time:.1f}s)"
        logger.warning(f"Groq transient error (circuit paused for {pause_time:.1f}s): {error}")
        return "transient", f"AI service busy or timed out; pausing {int(pause_time)}s"

    def reset_key_state(self):
        self._key_rejected = False

    def reset_daily_state(self):
        self._daily_limit_hit = False

    def reset_circuit(self):
        self._circuit_open_until = 0.0
        self._open_reason = ""
        self._consecutive_failures = 0


circuit_breaker = GroqCircuitBreaker()


def get_groq_chat(
    agent_name: str = "A2_writer",
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
) -> ChatGroq:
    """
    Factory creating a configured ChatGroq instance.
    Parameters default to .env settings per agent role.
    """
    api_key = settings.groq_api_key or os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY is not set in environment or .env file.")

    if agent_name == "A2_writer":
        selected_model = model_name or settings.a2_model
        selected_temp = temperature if temperature is not None else settings.a2_temperature
    elif agent_name == "A3_reader":
        selected_model = model_name or settings.a3_model
        selected_temp = temperature if temperature is not None else settings.a3_temperature
    elif agent_name == "tone_studio":
        selected_model = model_name or settings.studio_model
        selected_temp = temperature if temperature is not None else settings.studio_temperature
    else:
        selected_model = model_name or "qwen/qwen3.8-27b"
        selected_temp = temperature if temperature is not None else 0.5

    return ChatGroq(
        model=selected_model,
        groq_api_key=api_key,
        temperature=selected_temp,
        max_retries=0,
        request_timeout=settings.groq_timeout_seconds,
    )

