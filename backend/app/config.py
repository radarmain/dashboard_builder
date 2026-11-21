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
    )
