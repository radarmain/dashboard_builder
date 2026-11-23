"""System alerts (admin banner): Tableau token expiry + runtime auth failure."""

from datetime import UTC, date, datetime
from typing import Any

WARN_DAYS = 30
CRIT_DAYS = 7
AUTH_PK, AUTH_SK = "SYSTEM", "ALERT#tableau_auth"


def expiry_alert(expires: date | None, today: date | None = None) -> dict[str, Any] | None:
    if expires is None:
        return None
    today = today or datetime.now(UTC).date()
    days = (expires - today).days
    if days > WARN_DAYS:
        return None
    if days < 0:
        msg = (
            f"Tableau API token expired on {expires.isoformat()}. Create a new token and update it."
        )
    else:
        msg = f"Tableau API token expires on {expires.isoformat()} ({days} days left)."
    return {
        "id": "tableau_token_expiry",
        "severity": "critical" if days <= CRIT_DAYS else "warning",
        "message": msg,
        "expires_on": expires.isoformat(),
        "days_left": days,
    }


def auth_failure_alert(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    return {
        "id": "tableau_auth",
        "severity": "critical",
        "message": "Tableau rejected the API token (expired or revoked).",
        "expires_on": None,
        "days_left": None,
    }


# ---- DynamoDB marker (PK SYSTEM, SK ALERT#tableau_auth) ----
def _table() -> Any:
    from app.aws import ddb_table

    return ddb_table()


def get_auth_failure() -> dict[str, Any] | None:
    return _table().get_item(Key={"PK": AUTH_PK, "SK": AUTH_SK}).get("Item")


def record_auth_failure() -> None:
    _table().put_item(
        Item={"PK": AUTH_PK, "SK": AUTH_SK, "at": datetime.now(UTC).isoformat(timespec="seconds")}
    )


def clear_auth_failure() -> None:
    if get_auth_failure():
        _table().delete_item(Key={"PK": AUTH_PK, "SK": AUTH_SK})


def list_alerts() -> list[dict[str, Any]]:
    from app.tableau_config import load_config

    cfg = load_config()
    out = [expiry_alert(cfg.token_expires if cfg else None), auth_failure_alert(get_auth_failure())]
    return [a for a in out if a]
