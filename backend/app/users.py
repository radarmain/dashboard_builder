"""User-management rules (docs/DATA_MODEL.md, AGENTS.md). Enforced here, not just in the UI.

- Exactly one Cognito group per user: admin | analyst | client. Client/staff conversions refused.
- Users are never deleted: only disabled. (cognito.py has no delete function.)
- Staff roles only for emails on RDB_STAFF_EMAIL_DOMAINS.
- Each mutating action writes one audit line (actor sub, action, target sub, client slug).
"""

import json
import logging
import re
import sys
from typing import Any

from app import cognito, store
from app.cognito import CUser
from app.config import get_settings

STAFF = ("admin", "analyst")
ROLE_RANK = {"admin": 0, "analyst": 1, "client": 2}
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")

_audit = logging.getLogger("rdb.audit")
if not _audit.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _audit.addHandler(_h)
    _audit.setLevel(logging.INFO)
    _audit.propagate = False


class UserError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def audit(actor: str, action: str, target: str, client: str | None = None) -> None:
    """One structured line; no emails here."""
    _audit.info(
        json.dumps(
            {"audit": action, "actor": actor, "target": target, "client": client}, sort_keys=True
        )
    )


# ---------- validation ----------
def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise UserError(400, "Enter a valid email address.")
    return email


def check_staff_domain(email: str) -> None:
    allowed = get_settings().staff_email_domains
    if email.rsplit("@", 1)[1] not in allowed:
        raise UserError(
            400,
            "Admin and analyst accounts are limited to these email domains: "
            + ", ".join(allowed)
            + ".",
        )


def _is_client_only(groups: list[str]) -> bool:
    return groups == ["client"]


def _is_staff(groups: list[str]) -> bool:
    return bool(groups) and all(g in STAFF for g in groups)


def role_of(groups: list[str]) -> str | None:
    """Highest role (only differs from the single group for legacy multi-group users)."""
    known = [g for g in groups if g in ROLE_RANK]
    return min(known, key=lambda g: ROLE_RANK[g]) if known else None


def _need_user(sub: str) -> CUser:
    u = cognito.get_by_sub(sub)
    if not u:
        raise UserError(404, "User not found.")
    return u


def _other_enabled_admins(sub: str) -> int:
    return sum(1 for u in cognito.list_group("admin") if u.enabled and u.sub != sub)


def _guard_last_admin(target: CUser, what: str) -> None:
    if "admin" in target.groups and target.enabled and _other_enabled_admins(target.sub) == 0:
        raise UserError(409, f"The last enabled admin can't be {what}.")


# ---------- staff (admin only) ----------
def create_staff(actor: str, email: str, role: str, name: str | None) -> dict[str, Any]:
    if role not in STAFF:
        raise UserError(400, "Role must be admin or analyst. Client users are added per client.")
    email = normalize_email(email)
    check_staff_domain(email)
    if cognito.find_by_email(email):
        raise UserError(409, "A user with this email already exists.")
    try:
        u = cognito.create(email, name, role)
    except Exception as e:  # noqa: BLE001
        if cognito.is_conflict(e):
            raise UserError(409, "A user with this email already exists.") from None
        raise
    audit(actor, "create_staff", u.sub)
    return admin_view(u, None)


def change_role(actor: str, sub: str, role: str) -> dict[str, Any]:
    if role not in STAFF:
        raise UserError(400, "Role must be admin or analyst.")
    if actor == sub:
        raise UserError(400, "You can't change your own role.")
    u = _need_user(sub)
    if "client" in u.groups:
        raise UserError(409, "Client users can't be converted to staff (or the reverse).")
    if not _is_staff(u.groups):
        raise UserError(409, "This user has no role to change.")
    check_staff_domain(u.email)
    if u.groups == [role]:
        return admin_view(u, None)
    if role != "admin":
        _guard_last_admin(u, "demoted")
    if role not in u.groups:
        cognito.add_to_group(sub, role)  # add first: never leave the user without a group
    for g in u.groups:
        if g != role:
            cognito.remove_from_group(sub, g)
    u.groups = [role]
    audit(actor, f"role_change:{role}", sub)
    return admin_view(u, None)


def set_enabled(actor: str, sub: str, enabled: bool) -> dict[str, Any]:
    u = _need_user(sub)
    if not enabled:
        if actor == sub:
            raise UserError(400, "You can't disable yourself.")
        if u.enabled:
            _guard_last_admin(u, "disabled")
    if u.enabled != enabled:
        cognito.set_enabled(sub, enabled)  # disable also signs the user out everywhere
        u.enabled = enabled
        audit(actor, "enable" if enabled else "disable", sub)
    return admin_view(u, None)


def resend_invite(actor: str, sub: str, client: str | None = None) -> dict[str, str]:
    u = _need_user(sub)
    if u.status != "invited":
        raise UserError(409, "Only users who haven't signed in yet can be re-invited.")
    cognito.resend_invite(u.email)
    audit(actor, "resend_invite", sub, client)
    return {"status": "sent"}


def admin_view(u: CUser, clients: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "sub": u.sub,
        "email": u.email,
        "name": u.name,
        "role": role_of(u.groups),
        "status": u.status,
        "created_at": u.created,
    }
    if clients is not None:
        out["clients"] = clients
    return out


def list_users(role: str | None, search: str, cursor: int, limit: int) -> dict[str, Any]:
    if role and role not in ROLE_RANK:
        raise UserError(400, "role must be admin, analyst or client.")
    merged: dict[str, CUser] = {}
    for g in [role] if role else list(ROLE_RANK):
        for u in cognito.list_group(g):
            if u.sub in merged:
                merged[u.sub].groups = sorted({*merged[u.sub].groups, g})
            else:
                merged[u.sub] = u
    q = search.strip().lower()
    rows = [
        u for u in merged.values() if not q or q in u.email.lower() or q in (u.name or "").lower()
    ]
    rows.sort(key=lambda u: u.email)
    page = rows[cursor : cursor + limit]
    items = []
    for u in page:
        clients = store.list_access(u.sub) if "client" in u.groups else None
        items.append(admin_view(u, sorted(clients) if clients is not None else None))
    nxt = cursor + limit
    return {
        "users": items,
        "next_cursor": str(nxt) if nxt < len(rows) else None,
        "total": len(rows),
    }


# ---------- per-client (admin or analyst) ----------
def _client_view(m: dict[str, Any], u: CUser | None) -> dict[str, Any]:
    return {
        "sub": m["sub"],
        "email": (u.email if u else m["email"]),
        "status": u.status if u else "disabled",
        "added_at": m["added_at"],
        "added_by": m["added_by"],
    }


def list_client_users(client: str) -> list[dict[str, Any]]:
    rows = sorted(store.list_client_users(client), key=lambda m: m["email"])
    return [_client_view(m, cognito.get_by_sub(m["sub"], with_groups=False)) for m in rows]


def add_client_user(actor: str, client: str, raw_email: str, name: str | None) -> dict[str, Any]:
    email = normalize_email(raw_email)
    u = cognito.find_by_email(email)
    if u:
        if _is_staff(u.groups) or "client" not in u.groups:
            raise UserError(409, "That email belongs to a RADaR staff account, not a client user.")
        if not _is_client_only(u.groups):
            raise UserError(409, "That account has more than one role and can't be used here.")
    else:
        try:
            u = cognito.create(email, name, "client")
        except Exception as e:  # noqa: BLE001
            if cognito.is_conflict(e):
                raise UserError(409, "That email can't be added right now. Try again.") from None
            raise
    added = store.grant_access(u.sub, client, email, actor)  # no-op if they already had access
    audit(actor, "client_user_add" if added else "client_user_add_noop", u.sub, client)
    item = next((m for m in store.list_client_users(client) if m["sub"] == u.sub), None)
    return _client_view(
        item or {"sub": u.sub, "email": email, "added_at": "", "added_by": actor}, u
    )


def remove_client_access(actor: str, client: str, sub: str) -> None:
    if not store.has_access(sub, client):
        raise UserError(404, "User not found for this client.")
    store.revoke_access(sub, client)  # THIS client only; the Cognito account is untouched
    audit(actor, "client_user_remove", sub, client)


def resend_client_invite(actor: str, client: str, sub: str) -> dict[str, str]:
    if not store.has_access(sub, client):
        raise UserError(404, "User not found for this client.")
    return resend_invite(actor, sub, client)
