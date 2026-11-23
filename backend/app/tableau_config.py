"""Tableau connection settings. The token secret NEVER leaves the backend: it is a SecretStr,
and every error/log string goes through `redact()` before it can reach a response or a log.

Source: env vars (TABLEAU_SERVER_URL, TABLEAU_SITE, TABLEAU_TOKEN_NAME, TABLEAU_API_TOKEN,
TABLEAU_API_TOKEN_EXP) or, if RDB_TABLEAU_SECRET_NAME is set, that Secrets Manager secret
(JSON keys: server_url, site, token_name, token_secret, token_expires).
"""

import json
import os
import re
import threading
from datetime import date
from functools import lru_cache

from pydantic import BaseModel, SecretStr

from app import config as _config  # noqa: F401  (loads the repo-root .env)


class TableauConfig(BaseModel):
    server_url: str
    site: str
    token_name: str
    token_secret: SecretStr
    token_expires: date | None = None

    @property
    def host(self) -> str:
        return re.sub(r"^https?://", "", self.server_url).split("/")[0]


def parse_expiry(raw: str | None) -> date | None:
    """YYYY-MM-DD or MM/DD/YYYY (US) -> date. Empty -> None. Anything else -> ValueError."""
    s = (raw or "").strip()
    if not s:
        return None
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        return date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s):
        return date(int(m[3]), int(m[1]), int(m[2]))
    raise ValueError("token expiry must be YYYY-MM-DD or MM/DD/YYYY")


def _from_env() -> TableauConfig | None:
    e = os.environ
    if not (e.get("TABLEAU_SERVER_URL") and e.get("TABLEAU_API_TOKEN") and e.get("TABLEAU_SITE")):
        return None
    return TableauConfig(
        server_url=e["TABLEAU_SERVER_URL"].strip().rstrip("/"),
        site=e["TABLEAU_SITE"].strip(),
        token_name=e.get("TABLEAU_TOKEN_NAME", "").strip(),
        token_secret=SecretStr(e["TABLEAU_API_TOKEN"].strip()),
        token_expires=parse_expiry(e.get("TABLEAU_API_TOKEN_EXP")),
    )


def _from_secret(name: str) -> TableauConfig:
    from app.aws import session  # lazy: keeps this module importable without AWS config

    raw = session().client("secretsmanager").get_secret_value(SecretId=name)["SecretString"]
    d = json.loads(raw)
    return TableauConfig(
        server_url=d["server_url"].strip().rstrip("/"),
        site=d["site"].strip(),
        token_name=d["token_name"].strip(),
        token_secret=SecretStr(d["token_secret"]),
        token_expires=parse_expiry(d.get("token_expires")),
    )


@lru_cache
def load_config() -> TableauConfig | None:
    """None when Tableau isn't configured."""
    name = os.environ.get("RDB_TABLEAU_SECRET_NAME")
    return _from_secret(name) if name else _from_env()


# ---------- redaction ----------
_extra_secrets: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str) -> None:
    """Also redact short-lived values (the session token)."""
    if value:
        with _lock:
            _extra_secrets.add(value)


def redact(text: object, cfg: TableauConfig | None = None) -> str:
    s = str(text)
    try:
        cfg = cfg or load_config()
    except Exception:  # noqa: BLE001 - redaction must never raise
        cfg = None
    secrets = set(_extra_secrets)
    if cfg:
        secrets.add(cfg.token_secret.get_secret_value())
    for v in sorted((x for x in secrets if x), key=len, reverse=True):
        s = s.replace(v, "[redacted]")
    return s
