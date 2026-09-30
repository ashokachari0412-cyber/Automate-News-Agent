"""Application settings and configuration management."""

import os
from pathlib import Path
from typing import List, Literal, Optional
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class TwitterAccountConfig(BaseModel):
    """Schema for a trusted Twitter account."""
    handle: str
    name: str
    category: Literal["official_lab", "pioneer_researcher", "news_curator"] = "pioneer_researcher"
    weight: float = 1.0
    topics: List[str] = Field(default_factory=list)
    description: str = ""


class Settings(BaseSettings):
    """Global system configuration for Google-Powered AI Content Agent."""
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Google Gemini Settings (Google AI Pro)
    GEMINI_API_KEY: Optional[str] = Field(default=None)
    GEMINI_MODEL: str = "gemini-3.8-flash"

    # Twitter / X Collection (Playwright with cookies)
    COLLECTOR_MODE: Literal["auto", "playwright", "mock"] = "auto"
    PLAYWRIGHT_HEADLESS: bool = True
    COOKIES_FILE: str = "cookies.json"
    USER_DATA_DIR: str = "userdata"

    TWEETS_PER_ACCOUNT: int = 2
    MAX_TWEET_AGE_HOURS: int = 24
    VIDEO_DURATION_SECONDS: int = 5

    # Telegram (Human-in-the-Loop Review)
    TELEGRAM_BOT_TOKEN: Optional[str] = Field(default=None)
    TELEGRAM_CHAT_ID: Optional[str] = Field(default=None)

    # Slack (Optional Webhook)
    SLACK_WEBHOOK_URL: Optional[str] = Field(default=None)

    # Server Settings (FastAPI + LangGraph Orchestrator)
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Operating Hours & Persona (Active between 10:00 AM - 10:00 PM)
    ACTIVE_HOURS_ENABLED: bool = True
    ACTIVE_HOURS_START: int = 10   # 10 AM
    ACTIVE_HOURS_END: int = 22     # 10 PM
    USER_NAME: str = "Ashok"

    # Prioritization Parameters
    HIGH_PRIORITY_THRESHOLD: float = 75.0
    MEDIUM_PRIORITY_THRESHOLD: float = 50.0
    TOP_NEWS_LIMIT: int = 5

    def get_trusted_accounts(self) -> List[TwitterAccountConfig]:
        """Load configured trusted twitter accounts from YAML."""
        yaml_path = BASE_DIR / "config" / "trusted_accounts.yaml"
        if not yaml_path.exists():
            return []
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                raw_accounts = data.get("accounts", [])
                return [TwitterAccountConfig(**acc) for acc in raw_accounts]
        except Exception as e:
            print(f"Warning: Failed to parse trusted_accounts.yaml: {e}")
            return []


# Cached instance
settings = Settings()
