from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional

class Settings(BaseSettings):
    # App
    APP_NAME: str    = "OmniSensus Backend API"
    APP_VERSION: str = "3.0.1"
    ENV: str         = "development"
    DEBUG: bool      = False

    # Database — Neon PostgreSQL
    DATABASE_URL: str                        # required — no default

    # JWT
    JWT_SECRET: str                          # required — no default
    JWT_ALGORITHM: str               = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int   = 7

    # ML API
    ML_API_URL: str = "http://localhost:8000"
    ML_API_KEY: str                          # required — no default

    # CORS
    FRONTEND_URL: str = "http://localhost:5500"

    # Backend API key (for ML → Backend calls)
    API_KEY: str                             # required — no default

    # ── DOCS LOGIN CREDENTIALS ────────────────────────────────────
    # Set these in Render environment variables.
    # Anyone visiting /docs will see a browser username+password popup.
    # Remove hard-coded docs credentials; set via environment when needed
    DOCS_USERNAME: Optional[str] = None
    DOCS_PASSWORD: Optional[str] = None

    class Config:
        env_file       = ".env"
        case_sensitive = True

@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()