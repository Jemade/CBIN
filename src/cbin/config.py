import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./cbin.db"
    environment: str = "test"
    pepper: str = ""
    max_attempts: int = 6
    lease_seconds: int = 180

    @classmethod
    def from_env(cls):
        settings = cls(
            database_url=os.getenv("CBIN_DATABASE_URL", "sqlite:///./cbin.db"),
            environment=os.getenv("CBIN_ENVIRONMENT", "test"),
            pepper=os.getenv("CBIN_CREDENTIAL_PEPPER", ""),
        )
        if settings.environment not in {"test", "live"}:
            raise ValueError("CBIN_ENVIRONMENT must be test or live")
        if len(settings.pepper) < 32:
            raise ValueError("Set CBIN_CREDENTIAL_PEPPER to at least 32 random characters")
        return settings
