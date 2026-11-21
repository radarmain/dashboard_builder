"""Throwaway sanity check for the GBQ secret.

Prints ONLY: type == service_account, client_email, project_id.
Run from repo root: .venv/bin/python scripts/check_gbq_secret.py
"""

import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.gbq_credentials import load_credentials, load_sa_info  # noqa: E402

session = boto3.Session(profile_name="concrete", region_name="us-east-2")
info = load_sa_info(session=session)
print("type == service_account:", info.get("type") == "service_account")
print("client_email:", info.get("client_email"))
print("project_id:", info.get("project_id"))
load_credentials(session=session)  # confirms google-auth accepts it; prints nothing
print("credentials build: ok")
