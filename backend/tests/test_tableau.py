"""Tableau basics: expiry parsing, alert thresholds, token redaction, read-only guard."""

from datetime import date

import pytest
from app import alerts, tableau_client, tableau_config
from app.tableau_config import TableauConfig, parse_expiry, redact
from pydantic import SecretStr

CFG = TableauConfig(
    server_url="https://x.online.tableau.com",
    site="s",
    token_name="n",
    token_secret=SecretStr("SUPERSECRET123"),
    token_expires=date(2027, 11, 10),
)


def test_parse_expiry_formats() -> None:
    assert parse_expiry("2027-11-10") == date(2027, 11, 10)
    assert parse_expiry("11/10/2027") == date(2027, 11, 10)  # US month/day/year
    assert parse_expiry("1/2/2028") == date(2028, 1, 2)
    assert parse_expiry("") is None
    with pytest.raises(ValueError):
        parse_expiry("10 Nov 2027")


@pytest.mark.parametrize(
    ("days", "sev"),
    [
        (31, None),
        (30, "warning"),
        (8, "warning"),
        (7, "critical"),
        (0, "critical"),
        (-3, "critical"),
    ],
)
def test_expiry_thresholds(days: int, sev: str | None) -> None:
    today = date(2027, 1, 1)
    exp = date.fromordinal(today.toordinal() + days)
    a = alerts.expiry_alert(exp, today)
    assert (a["severity"] if a else None) == sev
    if a:
        assert a["days_left"] == days and a["expires_on"] == exp.isoformat()
    assert alerts.expiry_alert(None, today) is None


def test_auth_failure_alert() -> None:
    assert alerts.auth_failure_alert(None) is None
    a = alerts.auth_failure_alert({"PK": "SYSTEM"})
    assert a and a["severity"] == "critical" and "rejected" in a["message"]


def test_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tableau_config, "load_config", lambda: CFG)
    tableau_config.register_secret("SESSIONTOKEN999")
    out = redact("bad SUPERSECRET123 and SESSIONTOKEN999 here")
    assert "SUPERSECRET123" not in out and "SESSIONTOKEN999" not in out
    assert "SUPERSECRET123" not in repr(CFG) and "SUPERSECRET123" not in CFG.model_dump_json()


def test_client_refuses_writes() -> None:
    s = tableau_client.Session(CFG)
    with pytest.raises(tableau_client.TableauError):
        s._request("POST", "https://x.online.tableau.com/api/3.22/sites/1/workbooks")
    with pytest.raises(tableau_client.TableauError):
        s._request("DELETE", "https://x.online.tableau.com/api/3.22/sites/1/workbooks/1")
    with pytest.raises(tableau_client.TableauError):
        s._request(
            "POST",
            "https://x.online.tableau.com/api/metadata/graphql",
            json={"query": "mutation {x}"},
        )
