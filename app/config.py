from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Optional, List, Union, Any

class Settings(BaseSettings):
    TPO_USERNAME: str = ""
    TPO_PASSWORD: str = ""

    SMTP_HOST: Optional[str] = ""
    SMTP_PORT: Optional[int] = None
    SMTP_USERNAME: Optional[str] = ""
    SMTP_PASSWORD: Optional[str] = ""
    EMAIL_FROM: Optional[str] = ""
    EMAIL_TO: Optional[str] = ""

    @field_validator("SMTP_PORT", mode="before")
    @classmethod
    def parse_smtp_port(cls, v: Any) -> Optional[int]:
        if v is None or v == "":
            return None
        return int(v)

    CHECK_TIMES: str = "10:00,17:00,00:00"
    TIMEZONE: str = "Asia/Kolkata"
    TARGET_PROGRAMS: Optional[str] = ""

    ENABLE_TELEGRAM: bool = False
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    LOG_LEVEL: str = "INFO"

    # Public V1 Web & Security Settings
    SECRET_KEY: str = "tpo-watcher-secret-key-change-in-production"
    BASE_URL: str = "http://127.0.0.1:8000"
    PUBLIC_HOST: str = "127.0.0.1"
    PUBLIC_PORT: int = 8000
    COOKIE_SECURE: bool = True
    ENABLE_DOCS: bool = False
    HSTS_MAX_AGE: int = 86400

    # Rate Limiting Configuration (Per minute or per window)
    RATE_LIMIT_SIGNUP_PER_IP: int = 30
    RATE_LIMIT_SIGNUP_PER_EMAIL: int = 5
    RATE_LIMIT_MAGIC_LINK_PER_IP: int = 30
    RATE_LIMIT_MAGIC_LINK_PER_EMAIL: int = 5
    RATE_LIMIT_VERIFY_PER_IP: int = 30
    RATE_LIMIT_EXCHANGE_PER_IP: int = 30
    RATE_LIMIT_UNSUBSCRIBE_PER_IP: int = 30
    RATE_LIMIT_PREF_UPDATE_PER_IP: int = 30

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def get_target_programs_list(self) -> List[str]:
        if not self.TARGET_PROGRAMS:
            return []
        return [p.strip() for p in self.TARGET_PROGRAMS.split(",") if p.strip()]

    def get_check_times_list(self) -> List[str]:
        if not self.CHECK_TIMES:
            return ["10:00", "17:00", "00:00"]
        return [t.strip() for t in self.CHECK_TIMES.split(",") if t.strip()]

settings = Settings()
