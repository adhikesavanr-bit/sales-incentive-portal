"""Application configuration. Every secret comes from the environment."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Google Cloud / BigQuery -------------------------------------------
    gcp_project_id: str = Field(..., alias="GCP_PROJECT_ID")
    bq_dataset: str = Field("sales_incentive", alias="BQ_DATASET")
    bq_location: str = Field("asia-south1", alias="BQ_LOCATION")
    # Path to a service-account JSON, OR leave unset to use Workload Identity /
    # Application Default Credentials. Never ship the file itself.
    google_application_credentials: str | None = Field(
        None, alias="GOOGLE_APPLICATION_CREDENTIALS"
    )

    # --- Auth ---------------------------------------------------------------
    google_oauth_client_id: str = Field(..., alias="GOOGLE_OAUTH_CLIENT_ID")
    google_oauth_client_secret: str = Field(..., alias="GOOGLE_OAUTH_CLIENT_SECRET")
    jwt_secret: str = Field(..., alias="JWT_SECRET")
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = Field(480, alias="JWT_TTL_MINUTES")
    # Only these email domains may sign in.
    allowed_email_domains: str = Field(
        "marrowmed.com,dailyrounds.org", alias="ALLOWED_EMAIL_DOMAINS"
    )

    # --- Source sales tables ------------------------------------------------
    # Where each month's sales are read from, when they are not uploaded.
    # A row in `source_table_config` overrides all of this per period.
    source_project: str | None = Field(None, alias="SOURCE_PROJECT")
    source_dataset: str | None = Field(None, alias="SOURCE_DATASET")
    # Placeholders: {MMM} Aug, {MM} 08, {YYYY} 2026, {YY} 26, {period} 2026-08
    source_table_template: str | None = Field(None, alias="SOURCE_TABLE_TEMPLATE")
    source_date_column: str = Field("payment_date_ist", alias="SOURCE_DATE_COLUMN")

    # --- Local development sign-in -----------------------------------------
    # Lets you open the UI without configuring Google OAuth. Refused unless the
    # request comes from the loopback interface, and the endpoint is not even
    # registered unless this is true, so it cannot be reached on a deployed
    # instance by flipping one variable.
    allow_dev_login: bool = Field(False, alias="ALLOW_DEV_LOGIN")
    dev_login_email: str | None = Field(None, alias="DEV_LOGIN_EMAIL")

    # Coupon regions that are not part of the field incentive scheme. Their
    # sales are still qualified and stored — they belong to the coupon audit —
    # but they do not produce a field incentive row, because those teams are
    # paid under a different scheme. Comma-separated, matched case-insensitively.
    non_field_coupon_regions: str = Field("Inside Sales", alias="NON_FIELD_COUPON_REGIONS")

    # --- Storage ------------------------------------------------------------
    gcs_upload_bucket: str | None = Field(None, alias="GCS_UPLOAD_BUCKET")

    # --- Business configuration --------------------------------------------
    # Sourced from Policy!B3. Overridable per financial year without a deploy.
    default_arpu: float = Field(33000.0, alias="DEFAULT_ARPU")
    # Incentive Report- Output column AD.
    monthly_payout_cap: float = Field(200000.0, alias="MONTHLY_PAYOUT_CAP")
    # See DATA_MAPPING.md §1.1 — open item #3.
    refund_handling: Literal["exclude", "negative_adjustment"] = Field(
        "exclude", alias="REFUND_HANDLING"
    )
    # See DATA_MAPPING.md §7.4.
    treat_zero_sales_as_exit: bool = Field(True, alias="TREAT_ZERO_SALES_AS_EXIT")

    # --- App ----------------------------------------------------------------
    cors_origins: str = Field("http://localhost:3000", alias="CORS_ORIGINS")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    @property
    def domains(self) -> list[str]:
        return [d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()]

    @property
    def non_field_regions(self) -> set[str]:
        return {
            r.strip().lower()
            for r in (self.non_field_coupon_regions or "").split(",")
            if r.strip()
        }

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def table(self, name: str) -> str:
        return f"`{self.gcp_project_id}.{self.bq_dataset}.{name}`"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
