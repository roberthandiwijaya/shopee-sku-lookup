from pathlib import Path

from pydantic_settings import BaseSettings

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/shopee_products"

    shopee_partner_id: int = 0
    shopee_partner_key: str = ""
    shopee_shop_id: int = 0
    shopee_base_url: str = "https://openplatform.sandbox.test-stable.shopee.sg"
    shopee_redirect_url: str = "http://localhost:8000/api/auth/callback"

    api_key: str = ""

    session_secret_key: str = "change-me-in-production"

    sync_interval_minutes: int = 60

    model_config = {"env_file": str(ENV_FILE), "env_file_encoding": "utf-8"}


settings = Settings()
