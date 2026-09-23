from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "Flight Navigator MOW-IST-HKT MVP"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False
    
    # Defaults for MOW-IST-HKT search
    DEFAULT_ORIGIN: str = "MOW"
    DEFAULT_HUB: str = "IST"
    DEFAULT_DESTINATION: str = "HKT"
    
    # Currency
    DEFAULT_CURRENCY: str = "RUB"
    
    # Transfer parameters
    DEFAULT_MIN_STOPOVER_HOURS: float = 2.0
    DEFAULT_MAX_STOPOVER_HOURS: float = 48.0


settings = Settings()
