from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    direct_database_url: str | None = None
    app_env: str = "development"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    jwt_secret_key: str = "dev-insecure-secret-change-in-production-32chars"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Optional Supabase Storage settings (uses local dev URLs if left blank)
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    supabase_public_bucket: str = "lokvirasat-public"
    supabase_private_bucket: str = "lokvirasat-private"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()