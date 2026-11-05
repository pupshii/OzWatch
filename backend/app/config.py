"""All configuration comes from environment variables (or a local .env file).

Secrets such as API keys are never hard-coded and .env is git-ignored;
.env.example documents every variable.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    # Empty string means "use the in-memory store" (handy for tests / quick demos).
    database_url: str = ""

    # Transport for NSW Open Data Hub key. Empty -> cameras run on sample data.
    tfnsw_api_key: str = ""

    # OpenSky Network API client (OAuth2 client credentials). Empty -> anonymous, slower polling.
    opensky_client_id: str = ""
    opensky_client_secret: str = ""

    # aisstream.io API key (ships). Empty -> ships run on sample data.
    aisstream_api_key: str = ""

    # Override poll intervals (seconds) without code changes, e.g. POLL_CAMERAS=900
    poll_cameras: int | None = None
    poll_flights: int | None = None
    poll_transit: int | None = None

    history_hours: int = 24
    log_level: str = "INFO"
    # A descriptive User-Agent is polite and lets data owners contact you.
    user_agent: str = "OzWatch/0.1 (portfolio project; +https://github.com/pupshii/ozwatch)"


@lru_cache
def get_settings() -> Settings:
    return Settings()
