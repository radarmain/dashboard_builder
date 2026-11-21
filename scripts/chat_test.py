"""Send one chat message to a view's /chat endpoint and print the SSE stream readably.

Usage:
  .venv/bin/python scripts/chat_test.py CLIENT PROJECT VIEW "your message" [--history FILE.json]
                                        [--base-url https://xxxx.cloudfront.net]

Auth: runs scripts/get_token.py (prompts for email/password via getpass; nothing is stored).
  --temp-user   instead create a TEMPORARY analyst Cognito user (random password, in memory only),
                use it, and delete it afterwards. Handy for unattended runs.
The view's current saved draft (if any) is sent as current_spec; nothing is saved by this script.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

import boto3
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
API = os.environ.get("RDB_API", "http://localhost:8000")


def token_from_get_token() -> str:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "get_token.py")], stdout=subprocess.PIPE, text=True
    )
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit("could not get a token")
    return r.stdout.strip()


def temp_user_token() -> tuple[str, str, object]:
    idp = boto3.Session(profile_name="concrete", region_name="us-east-2").client("cognito-idp")
    pool, app = os.environ["COGNITO_USER_POOL_ID"], os.environ["COGNITO_APP_CLIENT_ID"]
    email = f"rdb-chat-{uuid.uuid4().hex[:6]}@example.invalid"
    pw = "Aa1!" + secrets.token_urlsafe(18)
    idp.admin_create_user(
        UserPoolId=pool,
        Username=email,
        MessageAction="SUPPRESS",
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ],
    )
    idp.admin_set_user_password(UserPoolId=pool, Username=email, Password=pw, Permanent=True)
    idp.admin_add_user_to_group(UserPoolId=pool, Username=email, GroupName="analyst")
    cleanup = lambda: idp.admin_delete_user(UserPoolId=pool, Username=email)  # noqa: E731
    try:
        r = idp.initiate_auth(
            ClientId=app,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": pw},
        )
    except Exception:
        cleanup()
        raise
    return r["AuthenticationResult"]["IdToken"], email, cleanup


def show_change(op: dict) -> None:
    if op["op"] == "remove_element":
        print(f"    - remove {op['id']}")
        return
    e = op["element"]
    chart = e.get("chart") or {}
    desc = f"{chart.get('kind', e['type'])}" + (
        f" x={chart.get('x')} y={chart.get('y')} series={chart.get('series')}" if chart else ""
    )
    print(f"    + {op['op']} {e['id']} [{desc}] {e.get('title')!r} layout={op.get('layout')}")
    print("      SQL: " + " ".join(e["sql"].split())[:300])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("client")
    ap.add_argument("project")
    ap.add_argument("view")
    ap.add_argument("message")
    ap.add_argument("--history", help="JSON file: [{role, content}, ...]")
    ap.add_argument("--temp-user", action="store_true")
    ap.add_argument(
        "--base-url", default=API, help="API/CloudFront origin (default: RDB_API or local)"
    )
    a = ap.parse_args()
    api = a.base_url.rstrip("/")

    cleanup = None
    if a.temp_user:
        token, email, cleanup = temp_user_token()
        print(f"(temporary analyst {email})", file=sys.stderr)
    else:
        token = token_from_get_token()
    headers = {"Authorization": f"Bearer {token}"}
    base = f"{api}/api/clients/{a.client}/projects/{a.project}/views/{a.view}"
    try:
        d = httpx.get(f"{base}/draft", headers=headers, timeout=30)
        if d.status_code == 200:
            spec = d.json()
        else:
            spec = {
                "schema_version": 1,
                "view": {"name": a.view, "slug": a.view},
                "layout": [],
                "elements": [],
            }
        history = json.loads(Path(a.history).read_text()) if a.history else []
        print(f"\nYOU: {a.message}\n")
        n_changes = 0
        t0 = time.monotonic()
        t_first: float | None = None
        n_events = n_keepalive = 0
        with httpx.stream(
            "POST",
            f"{base}/chat",
            headers=headers,
            timeout=180,
            json={"message": a.message, "history": history, "current_spec": spec},
        ) as r:
            if r.status_code != 200:
                r.read()
                sys.exit(f"HTTP {r.status_code}: {r.text[:500]}")
            in_text = False
            for line in r.iter_lines():
                if line.startswith(":"):
                    n_keepalive += 1
                if not line.startswith("data: "):
                    continue
                n_events += 1
                if t_first is None:
                    t_first = time.monotonic() - t0
                ev = json.loads(line[6:])
                t = ev["type"]
                if t == "text":
                    if not in_text:
                        print("AI: ", end="")
                        in_text = True
                    print(ev["delta"], end="", flush=True)
                    continue
                if in_text:
                    print("\n")
                    in_text = False
                if t == "status":
                    print(f"  [{ev['text']}]")
                elif t == "changes":
                    n_changes += len(ev["operations"])
                    print("  CHANGES:")
                    for op in ev["operations"]:
                        show_change(op)
                elif t == "error":
                    print(f"  ERROR: {ev['message']}")
                elif t == "done":
                    print(f"  [done; {n_changes} validated change(s) proposed, nothing saved]")
        total = time.monotonic() - t0
        print(
            f"\nTIMING: first event {t_first if t_first is not None else float('nan'):.2f}s, "
            f"total {total:.2f}s, {n_events} events, {n_keepalive} keep-alives"
        )
    finally:
        if cleanup:
            cleanup()
            print("(temporary user deleted)", file=sys.stderr)


if __name__ == "__main__":
    main()
