"""Gemini settings. The API key is a SecretStr and is never logged or returned.

Source: env vars (GEMINI_API_KEY, GEMINI_DEFAULT_MODEL, GEMINI_DECISION_MODEL) or, if
RDB_GEMINI_SECRET_NAME is set, that Secrets Manager secret instead (JSON keys: api_key,
default_model, decision_model). Switches: RDB_GEMINI_ENABLED, RDB_DECISIONS_ENABLED,
RDB_DECISIONS_TIMEOUT_SECONDS, RDB_GEMINI_ALLOW_ROW_SAMPLES.

Kept in its own module (like tableau_config.py) so it does not touch app/config.py.
"""

import json
import logging
import os
from functools import lru_cache

from pydantic import BaseModel, SecretStr

from app import config as _config  # noqa: F401  (loads the repo-root .env)

log = logging.getLogger("rdb.llm.config")

DEFAULT_TEXT_MODEL = "gemini-3.8-flash"
DEFAULT_DECISION_MODEL = "gemini-decision-1"


class GeminiSettings(BaseModel):
    enabled: bool = True
    decisions_enabled: bool = True
    decisions_timeout_seconds: float = 8.0
    allow_row_samples: bool = False
    text_timeout_seconds: float = 60.0
    api_key: SecretStr | None = None
    default_model: str = DEFAULT_TEXT_MODEL
    decision_model: str = DEFAULT_DECISION_MODEL

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key.get_secret_value())


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def _credentials() -> tuple[str | None, str, str]:
    """(api_key, default_model, decision_model) from the secret if named, else from env."""
    env = os.environ
    name = env.get("RDB_GEMINI_SECRET_NAME")
    if not name:
        return (
            env.get("GEMINI_API_KEY") or None,
            env.get("GEMINI_DEFAULT_MODEL") or DEFAULT_TEXT_MODEL,
            env.get("GEMINI_DECISION_MODEL") or DEFAULT_DECISION_MODEL,
        )
    try:
        from app.aws import session  # lazy: keeps this module importable without AWS config

        raw = session().client("secretsmanager").get_secret_value(SecretId=name)["SecretString"]
        d = json.loads(raw)
        return (
            d.get("api_key") or None,
            d.get("default_model") or DEFAULT_TEXT_MODEL,
            d.get("decision_model") or DEFAULT_DECISION_MODEL,
        )
    except Exception as e:  # noqa: BLE001 - Gemini is optional; never break startup
        log.warning("gemini secret %s could not be loaded (%s)", name, type(e).__name__)
        return None, DEFAULT_TEXT_MODEL, DEFAULT_DECISION_MODEL


@lru_cache
def get_gemini_settings() -> GeminiSettings:
    key, default_model, decision_model = _credentials()
    return GeminiSettings(
        enabled=_flag("RDB_GEMINI_ENABLED", True),
        decisions_enabled=_flag("RDB_DECISIONS_ENABLED", True),
        decisions_timeout_seconds=_num("RDB_DECISIONS_TIMEOUT_SECONDS", 8.0),
        allow_row_samples=_flag("RDB_GEMINI_ALLOW_ROW_SAMPLES", False),
        text_timeout_seconds=_num("RDB_GEMINI_TEXT_TIMEOUT_SECONDS", 60.0),
        api_key=SecretStr(key) if key else None,
        default_model=default_model,
        decision_model=decision_model,
    )
