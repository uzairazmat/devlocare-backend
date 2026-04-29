from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables / .env file.
    All values are configurable — never hardcode in the codebase.
    """

    APP_NAME: str = "DevloCare API"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    DATABASE_URL: str = "sqlite+aiosqlite:///./devlocare.db"

    JWT_SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    DEFAULT_LANGUAGE: str = "English"

    # ----- Super-admin bootstrap ------------------------------------------- #
    # The super admin is created exactly once on application startup if no
    # row with `is_super_admin=True` exists. There is intentionally NO API
    # endpoint to mint a super admin. To rotate, change these values and
    # update the row directly in the DB.
    SUPER_ADMIN_USERNAME: str = ""
    SUPER_ADMIN_PASSWORD: str = ""
    SUPER_ADMIN_EMAIL: str = ""

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
