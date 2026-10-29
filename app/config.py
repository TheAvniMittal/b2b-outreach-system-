import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / ".env"
if ENV_FILE.exists():
    load_dotenv(dotenv_path=ENV_FILE)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = os.environ.get("DATABASE_URL", "")
    target_daily_sends: int = 1000
    ramp_target: int = 150

    # Groq AI settings (Phase 1)
    groq_api_key: str = os.environ.get("GROQ_API_KEY", "")
    a2_model: str = os.environ.get("A2_MODEL", "qwen/qwen3.8-27b")
    a2_temperature: float = float(os.environ.get("A2_TEMPERATURE", "0.3"))
    a3_model: str = os.environ.get("A3_MODEL", "qwen/qwen3.8-27b")
    a3_temperature: float = float(os.environ.get("A3_TEMPERATURE", "0.0"))
    studio_model: str = os.environ.get("STUDIO_MODEL", "qwen/qwen3.8-27b")
    studio_temperature: float = float(os.environ.get("STUDIO_TEMPERATURE", "0.8"))
    groq_daily_reset_utc: str = os.environ.get("GROQ_DAILY_RESET_UTC", "00:00")

    # Stage 5 sending config
    smtp_gmail: str = os.environ.get("SMTP_gmail", "")
    smtp_password: str = os.environ.get("SMTP_PASSWORD", "")
    smtp_host: str = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.environ.get("SMTP_PORT", "587"))
    live_email_sending: bool = os.environ.get("LIVE_EMAIL_SENDING", "false").lower() == "true"
    seed_sending_enabled: bool = os.environ.get("SEED_SENDING_ENABLED", "true").lower() == "true"
    test_mode: bool = os.environ.get("TEST_MODE", "true").lower() == "true"

    # Phase 6 Sending Controller & Auto-Stop thresholds
    auto_stop_bounce_rate_threshold: float = float(os.environ.get("AUTO_STOP_BOUNCE_RATE_THRESHOLD", "0.05"))
    auto_stop_min_sends: int = int(os.environ.get("AUTO_STOP_MIN_SENDS", "10"))

    # Phase 8 Lease, Sweeper & Provider recovery settings
    queue_lease_seconds: int = int(os.environ.get("QUEUE_LEASE_SECONDS", "300"))
    sweeper_interval_seconds: int = int(os.environ.get("SWEEPER_INTERVAL_SECONDS", "60"))
    max_processing_seconds: int = int(os.environ.get("MAX_PROCESSING_SECONDS", "900"))
    groq_timeout_seconds: float = float(os.environ.get("GROQ_TIMEOUT_SECONDS", "45.0"))
    provider_max_retries: int = int(os.environ.get("PROVIDER_MAX_RETRIES", "5"))
    provider_unavailable_timeout_hours: int = int(os.environ.get("PROVIDER_UNAVAILABLE_TIMEOUT_HOURS", "24"))

    # Phase 9 Catalogue Attachment & Configuration settings
    max_attachment_size_mb: int = int(os.environ.get("MAX_ATTACHMENT_SIZE_MB", "5"))
    catalogues_dir: str = os.environ.get("CATALOGUES_DIR", "catalogues")
    base_catalogue_url: str = os.environ.get("BASE_CATALOGUE_URL", "http://127.0.0.1:8000/api/catalogues/download/")

settings = Settings()

