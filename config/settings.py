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
    GEMINI_IMAGE_MODEL: str = "gemini-3.1-flash-image"

    # News & Research Collection (100% Authorized AI Lab Feeds + Hugging Face)
    COLLECTOR_MODE: Literal["authorized", "auto", "playwright", "syndication", "mock"] = "authorized"
    PLAYWRIGHT_HEADLESS: bool = True
    COOKIES_FILE: str = "cookies.json"
    USER_DATA_DIR: str = "userdata"

    TWEETS_PER_ACCOUNT: int = 2
    MAX_TWEET_AGE_HOURS: int = 24
    VIDEO_DURATION_SECONDS: int = 3

    # Avatar & Voice Configuration
    AVATAR_IMAGE_PATH: str = "assets/avatar.png"
    TTS_VOICE: str = "en-US-AndrewNeural"
    INTRO_AVATAR_DURATION: float = 3.0
    ENABLE_GEMINI_VEO: bool = False
    VEO_MODEL: str = "veo-3.1-fast-generate-preview"

    # HeyGen AI Avatar Clone Settings
    HEYGEN_API_KEY: Optional[str] = Field(default=None)
    HEYGEN_AVATAR_ID: str = "0c8510c2f7ad4b96a5193bb7ce4c18a2"
    HEYGEN_VOICE_ID: Optional[str] = Field(default=None)

    # Slack (Optional Webhook)
    SLACK_WEBHOOK_URL: Optional[str] = Field(default=None)

    # LinkedIn (Posts API, token needs w_member_social + openid/profile scopes)
    LINKEDIN_ACCESS_TOKEN: Optional[str] = Field(default=None)
    LINKEDIN_AUTHOR_URN: Optional[str] = Field(default=None)  # auto-detected if empty
    LINKEDIN_API_VERSION: str = "202509"

    # YouTube Shorts (uses the Google OAuth connection below)
    YOUTUBE_PRIVACY: str = "public"  # public | unlisted | private

    # Google Workspace OAuth (Drive / Calendar / Gmail tools)
    GOOGLE_CLIENT_ID: Optional[str] = Field(default=None)
    GOOGLE_CLIENT_SECRET: Optional[str] = Field(default=None)
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/google/callback"

    # Instagram Direct Auto-Posting (instagrapi)
    INSTAGRAM_USERNAME: Optional[str] = Field(default=None)
    INSTAGRAM_PASSWORD: Optional[str] = Field(default=None)
    INSTAGRAM_SESSIONID: Optional[str] = Field(default=None)

    # Scheduler Settings (Containerized Periodic Pipeline)
    SCHEDULE_INTERVAL_HOURS: int = Field(default=4)
    SCHEDULE_RUN_ON_STARTUP: bool = Field(default=True)

    # Server Settings (FastAPI + LangGraph Orchestrator)
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Operating Hours & Persona (Active between 9:00 AM - 11:59 PM)
    ACTIVE_HOURS_ENABLED: bool = False
    ACTIVE_HOURS_START: int = 9    # 9 AM
    ACTIVE_HOURS_END: int = 24     # 11:59 PM (hours 9 through 23 inclusive)
    TIMEZONE: str = "Asia/Kolkata" # User's local timezone
    USER_NAME: str = "Ashok"

    # Prioritization Parameters
    HIGH_PRIORITY_THRESHOLD: float = 75.0
    MEDIUM_PRIORITY_THRESHOLD: float = 50.0
    TOP_NEWS_LIMIT: int = 4

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
