from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    PROJECT_NAME: str = "Flight Navigator MOW-IST-HKT MVP"
    VERSION: str = "0.2.0"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False

    # Travelpayouts / Aviasales Data API
    AVIASALES_TOKEN: Optional[str] = Field(None, description="Travelpayouts Data API token")
    AVIASALES_MARKER: str = Field("765617", description="Travelpayouts partner marker")
    TRAVELPAYOUTS_API_BASE_URL: str = Field(
        "https://api.travelpayouts.com",
        description="Base URL for Travelpayouts Data API"
    )

    # Defaults for MOW-IST-HKT search
    DEFAULT_ORIGIN: str = "MOW"
    DEFAULT_HUB: str = "IST"
    DEFAULT_DESTINATION: str = "HKT"
    DEFAULT_CURRENCY: str = "RUB"


settings = Settings()
