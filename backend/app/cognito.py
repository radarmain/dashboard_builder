"""Thin Cognito admin wrapper for the rdb-users pool ONLY (pool id comes from settings).

Deliberately has no delete function: users are disabled, never deleted (the API role has no
cognito-idp:AdminDeleteUser permission either). Tests replace this module's functions.
"""

import os
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError

from app.aws import idp
from app.config import get_settings

GROUPS = ("admin", "analyst", "client")


@dataclass
class CUser:
    sub: str
    email: str
    name: str | None
    enabled: bool
    cognito_status: str  # CONFIRMED, FORCE_CHANGE_PASSWORD, ...
    created: str  # ISO date
    groups: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        return "invited" if self.cognito_status == "FORCE_CHANGE_PASSWORD" else "active"


def _pool() -> str:
    return get_settings().user_pool_id


def _attrs(attrs: list[dict[str, str]]) -> dict[str, str]:
    return {a["Name"]: a["Value"] for a in attrs}


def _from_list(u: dict[str, Any], groups: list[str] | None = None) -> CUser:
    a = _attrs(u.get("Attributes", []))
    return CUser(
        sub=a.get("sub", u["Username"]),
        email=a.get("email", ""),
        name=a.get("name"),
        enabled=u.get("Enabled", True),
        cognito_status=u.get("UserStatus", ""),
        created=u["UserCreateDate"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        groups=groups or [],
    )


def _find(attr: str, value: str) -> CUser | None:
    safe = value.replace("\\", "\\\\").replace('"', '\\"')
    r = idp().list_users(UserPoolId=_pool(), Filter=f'{attr} = "{safe}"', Limit=2)
    return _from_list(r["Users"][0]) if r["Users"] else None


def find_by_email(email: str) -> CUser | None:
    u = _find("email", email)
    return _with_groups(u) if u else None


def get_by_sub(sub: str, with_groups: bool = True) -> CUser | None:
    u = _find("sub", sub)
    return _with_groups(u) if u and with_groups else u


def groups_of(sub: str) -> list[str]:
    r = idp().admin_list_groups_for_user(UserPoolId=_pool(), Username=sub)
    return sorted(g["GroupName"] for g in r["Groups"])


def _with_groups(u: CUser) -> CUser:
    u.groups = groups_of(u.sub)
    return u


def create(email: str, name: str | None, group: str) -> CUser:
    """New user in exactly one group. Cognito emails the temporary password (unless suppressed)."""
    attrs = [{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}]
    if name:
        attrs.append({"Name": "name", "Value": name})
    kw: dict[str, Any] = {}
    if os.environ.get("RDB_SUPPRESS_INVITE_EMAILS", "").lower() == "true":
        kw["MessageAction"] = "SUPPRESS"  # tests only; never set in the deployed Lambda
    else:
        kw["DesiredDeliveryMediums"] = ["EMAIL"]
    r = idp().admin_create_user(UserPoolId=_pool(), Username=email, UserAttributes=attrs, **kw)
    u = _from_list(r["User"])
    idp().admin_add_user_to_group(UserPoolId=_pool(), Username=u.sub, GroupName=group)
    u.groups = [group]
    return u


def add_to_group(sub: str, group: str) -> None:
    idp().admin_add_user_to_group(UserPoolId=_pool(), Username=sub, GroupName=group)


def remove_from_group(sub: str, group: str) -> None:
    idp().admin_remove_user_from_group(UserPoolId=_pool(), Username=sub, GroupName=group)


def set_enabled(sub: str, enabled: bool) -> None:
    if enabled:
        idp().admin_enable_user(UserPoolId=_pool(), Username=sub)
    else:
        idp().admin_disable_user(UserPoolId=_pool(), Username=sub)
        idp().admin_user_global_sign_out(UserPoolId=_pool(), Username=sub)


def resend_invite(email: str) -> None:
    idp().admin_create_user(
        UserPoolId=_pool(), Username=email, MessageAction="RESEND", DesiredDeliveryMediums=["EMAIL"]
    )


def list_group(group: str) -> list[CUser]:
    """All users in a group (prototype scale: a handful of pages at most)."""
    out: list[CUser] = []
    kw: dict[str, Any] = {"UserPoolId": _pool(), "GroupName": group, "Limit": 60}
    while True:
        r = idp().list_users_in_group(**kw)
        out += [_from_list(u, [group]) for u in r["Users"]]
        if not r.get("NextToken"):
            return out
        kw["NextToken"] = r["NextToken"]


def is_conflict(e: Exception) -> bool:
    return isinstance(e, ClientError) and e.response["Error"]["Code"] == "UsernameExistsException"
