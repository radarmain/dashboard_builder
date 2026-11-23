"""Settings from environment (repo-root .env is loaded for local runs). Never log values."""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


class Settings(BaseModel):
    region: str
    table_name: str
    data_bucket: str
    user_pool_id: str
    app_client_id: str
    gbq_billing_project: str | None
    default_max_bytes_billed: int
    query_timeout_seconds: int
    query_row_cap: int
    default_cache_ttl_seconds: int
    bedrock_model_id: str
    chat_max_bytes_billed: int
    chat_max_output_tokens: int
    chat_timeout_seconds: int
    chat_max_iterations: int
    import_max_bytes_billed: int
    import_run_budget_bytes: int
    import_max_iterations: int
    import_timeout_seconds: int
    import_confirm_bytes: int
    staff_email_domains: list[str]

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"


@lru_cache
def get_settings() -> Settings:
    env = os.environ
    return Settings(
        region=env.get("AWS_REGION", "us-east-2"),
        table_name=env.get("RDB_TABLE_NAME", "rdb_main"),
        data_bucket=env["RDB_DATA_BUCKET"],
        user_pool_id=env["COGNITO_USER_POOL_ID"],
        app_client_id=env["COGNITO_APP_CLIENT_ID"],
        gbq_billing_project=env.get("RDB_GBQ_BILLING_PROJECT") or None,
        default_max_bytes_billed=int(env.get("BQ_DEFAULT_MAX_BYTES_BILLED", "10000000000")),
        query_timeout_seconds=int(env.get("RDB_QUERY_TIMEOUT_SECONDS", "60")),
        query_row_cap=int(env.get("RDB_QUERY_ROW_CAP", "10000")),
        default_cache_ttl_seconds=int(env.get("RDB_CACHE_TTL_SECONDS", "3600")),
        bedrock_model_id=env.get("RDB_BEDROCK_MODEL_ID")
        or env.get("BEDROCK_MODEL_ID")
        or "us.anthropic.claude-sonnet-5-5",
        chat_max_bytes_billed=int(env.get("RDB_CHAT_MAX_BYTES_BILLED", "2000000000")),
        chat_max_output_tokens=int(env.get("RDB_CHAT_MAX_OUTPUT_TOKENS", "4096")),
        chat_timeout_seconds=int(env.get("RDB_CHAT_TIMEOUT_SECONDS", "120")),
        chat_max_iterations=int(env.get("RDB_CHAT_MAX_ITERATIONS", "8")),
        import_max_bytes_billed=int(env.get("RDB_IMPORT_MAX_BYTES_BILLED", "8000000000")),
        import_run_budget_bytes=int(env.get("RDB_IMPORT_RUN_BUDGET_BYTES", "40000000000")),
        import_max_iterations=int(env.get("RDB_IMPORT_MAX_ITERATIONS", "8")),
        import_timeout_seconds=int(env.get("RDB_IMPORT_TIMEOUT_SECONDS", "120")),
        import_confirm_bytes=int(env.get("RDB_IMPORT_CONFIRM_BYTES", "2000000000")),
        staff_email_domains=[
            d.strip().lower().lstrip("@")
            for d in env.get("RDB_STAFF_EMAIL_DOMAINS", "radar-analytics.com").split(",")
            if d.strip()
        ],
    )
