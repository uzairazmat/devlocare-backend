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
    # 8 hours — production middle-ground. Long enough that an active user
    # isn't kicked mid-session; short enough that a stolen token isn't
    # useful for a full day. Frontend layers an idle timeout on top.
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8

    DEFAULT_LANGUAGE: str = "English"

    # ----- Stateful chat / prediction confidence --------------------------- #
    # Top-1 calibrated probability below this threshold triggers a follow-up
    # question instead of the final assessment. The threshold is the ONLY
    # gate — the chat keeps asking clarifying questions until confidence
    # reaches it. Range [0, 1].
    PREDICTION_CONFIDENCE_THRESHOLD: float = 0.55

    # ----- Explainability feature importance ------------------------------- #
    # LIME returns weights for every word it samples, including stopwords
    # ("I", "my", "on"). Anything below this normalised threshold is dropped
    # before the response is sent so the bar chart only shows clinically
    # meaningful tokens. Range [0, 1]; 0 disables filtering.
    FEATURE_IMPORTANCE_THRESHOLD: float = 0.40

    # ----- Rate limiting (slowapi) ----------------------------------------- #
    # Set to false to disable rate limiting entirely (useful in tests). The
    # per-endpoint limits below are read at app startup; tweak per env.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_LOGIN: str = "5/minute"        # POST /auth/login + /auth/token
    RATE_LIMIT_REGISTER: str = "10/hour"      # POST /auth/register
    RATE_LIMIT_PREDICT: str = "30/minute"     # POST /predict/text

    # ----- CORS ------------------------------------------------------------ #
    # Comma-separated list of allowed browser origins. Use "*" only in
    # development; production should list explicit origins.
    # Examples:
    #   CORS_ORIGINS=http://localhost:5173,http://192.168.100.29:5173
    #   CORS_ORIGINS=*
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        raw = (self.CORS_ORIGINS or "").strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

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
