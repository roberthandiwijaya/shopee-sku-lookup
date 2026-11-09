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
    order_sync_enabled: bool = True
    order_sync_interval_minutes: int = 5
    order_sync_backfill_days: int = 90
    order_sync_overlap_minutes: int = 30
    order_webhook_enabled: bool = True
    order_webhook_verification_bootstrap_enabled: bool = False
    shopee_webhook_callback_url: str = "https://shopee.techly.id/api/webhooks/shopee"
    shopee_push_partner_key: str = ""
    order_refresh_worker_batch_size: int = 20
    order_refresh_lease_seconds: int = 600
    order_refresh_max_attempts: int = 8
    order_refresh_retry_base_seconds: int = 15
    order_incomplete_scan_batch_size: int = 200
    order_reconciliation_enabled: bool = True
    order_reconciliation_interval_minutes: int = 1440

    catalog_export_dir: str = "/exports"
    catalog_export_max_bytes: int = 1_000_000_000
    catalog_export_job_seconds: int = 900
    catalog_export_retention_hours: int = 24
    catalog_export_queue_limit: int = 10
    catalog_export_owner_active_limit: int = 1
    catalog_export_cooldown_seconds: int = 30
    catalog_export_owner_hourly_limit: int = 5
    catalog_export_owner_family_hourly_limit: int = 500
    catalog_export_network_hourly_limit: int = 20
    catalog_export_network_family_hourly_limit: int = 2000
    catalog_export_preflight_owner_per_minute: int = 10
    catalog_export_preflight_network_per_minute: int = 60
    # Explicit deployment settings: never infer trust from arbitrary forwarded headers.
    catalog_export_network_secret: str = ""
    catalog_export_trusted_proxy_ips: list[str] = []
    catalog_export_allowed_origins: list[str] = ["https://shopee.techly.id"]
    session_cookie_secure: bool = True
    catalog_export_admin_usernames: list[str] = ["admin"]

    model_config = {"env_file": str(ENV_FILE), "env_file_encoding": "utf-8"}


settings = Settings()
