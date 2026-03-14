# pydantic-settings class

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    MSE_LOGIN_URL: str = "https://messervices.etudiant.gouv.fr/oauth2/login"

    MSE_EMAIL: str = Field(default=...)
    MSE_PASSWORD: str = Field(default=...)

    TELEGRAM_BOT_TOKEN: str = Field(default=...)
    MY_TELEGRAM_ID: str = Field(default=...)
    SEARCH_URL: str = Field(default=...)
    
    # Optional date filtering (YYYY-MM-DD)
    DATE_FROM: str | None = None
    DATE_TO: str | None = None
