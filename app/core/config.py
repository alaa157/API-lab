from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "booking-api"
    environment: str = "development"

    database_url: str = (
        "postgresql+asyncpg://booking:booking@localhost:5432/bookingdb"
    )
    sync_database_url: str = (
        "postgresql+psycopg://booking:booking@localhost:5432/bookingdb"
    )

    jwt_secret_key: str = Field(min_length=32, repr=False)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    backend_cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
    ]

    redis_url: str | None = None
    redis_password: str | None = None
    log_level: str = "INFO"
    log_format: str = "auto"


@lru_cache
def get_settings() -> Settings:
    return Settings()
