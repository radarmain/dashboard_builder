"""User management routes (all under /api). Rules live in app/users.py."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app import lifecycle, store, users
from app.auth import Admin, Builder
from app.users import UserError

router = APIRouter(prefix="/api")


class ClientUserCreate(BaseModel):
    email: str
    name: str | None = None


class StaffCreate(BaseModel):
    email: str
    role: str
    name: str | None = None


class RoleChange(BaseModel):
    role: str


def _run(fn: Any, *a: Any, **kw: Any) -> Any:
    try:
        return fn(*a, **kw)
    except UserError as e:
        raise HTTPException(e.status, e.message) from None


def _need_client(c: str, write: bool = False) -> None:
    item = store.get_client(c)
    if not item or lifecycle.state(item) == lifecycle.DELETING:
        raise HTTPException(404, "client not found")
    if write and lifecycle.state(item) == lifecycle.ARCHIVED:
        raise HTTPException(409, "archived")


# ---------- per client: admin or analyst ----------
@router.get("/clients/{c}/users")
def client_users(c: str, _: Builder) -> list[dict[str, Any]]:
    _need_client(c)
    return users.list_client_users(c)


@router.post("/clients/{c}/users", status_code=201)
def add_client_user(c: str, body: ClientUserCreate, user: Builder) -> dict[str, Any]:
    _need_client(c, True)
    return _run(users.add_client_user, user.sub, c, body.email, body.name)


@router.delete("/clients/{c}/users/{sub}")
def remove_client_user(c: str, sub: str, user: Builder) -> dict[str, str]:
    _need_client(c, True)
    _run(users.remove_client_access, user.sub, c, sub)
    return {"removed": "ok"}


@router.post("/clients/{c}/users/{sub}/resend-invite")
def resend_client_invite(c: str, sub: str, user: Builder) -> dict[str, str]:
    _need_client(c, True)
    return _run(users.resend_client_invite, user.sub, c, sub)


# ---------- admin only ----------
@router.get("/admin/users")
def admin_list(
    _: Admin,
    role: str | None = None,
    search: str = "",
    cursor: str | None = None,
    limit: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    return _run(users.list_users, role, search, int(cursor or 0), limit)


@router.post("/admin/users", status_code=201)
def admin_create(body: StaffCreate, user: Admin) -> dict[str, Any]:
    return _run(users.create_staff, user.sub, body.email, body.role, body.name)


@router.post("/admin/users/{sub}/role")
def admin_role(sub: str, body: RoleChange, user: Admin) -> dict[str, Any]:
    return _run(users.change_role, user.sub, sub, body.role)


@router.post("/admin/users/{sub}/disable")
def admin_disable(sub: str, user: Admin) -> dict[str, Any]:
    return _run(users.set_enabled, user.sub, sub, False)


@router.post("/admin/users/{sub}/enable")
def admin_enable(sub: str, user: Admin) -> dict[str, Any]:
    return _run(users.set_enabled, user.sub, sub, True)


@router.post("/admin/users/{sub}/resend-invite")
def admin_resend(sub: str, user: Admin) -> dict[str, str]:
    return _run(users.resend_invite, user.sub, sub)
