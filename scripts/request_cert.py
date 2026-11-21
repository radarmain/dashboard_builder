"""Request (or reuse) the DNS-validated wildcard certificate for *.radar-analytics.com in us-east-1.

Never touches DNS: it PRINTS the validation record; you add it in Cloudflare (DNS only / grey cloud).
Idempotent: reuses an existing rdb-tagged cert (ISSUED, else PENDING_VALIDATION) and uses an
idempotency token on request. When the cert is ISSUED, writes infra/config.json {"cert_arn": ...}.

Usage: AWS_PROFILE=concrete .venv/bin/python scripts/request_cert.py [--timeout-minutes 20]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import boto3

DOMAIN = "*.radar-analytics.com"
REGION = "us-east-1"  # CloudFront certificates must live here
TAGS = [{"Key": "Project", "Value": "RDB"}, {"Key": "Name", "Value": "rdb_wildcard_cert"}]
TOKEN = "rdbwildcardcert"  # idempotency token (alphanumeric, valid ~1h)
CONFIG = Path(__file__).resolve().parents[1] / "infra" / "config.json"

acm = boto3.Session(profile_name="concrete", region_name=REGION).client("acm")


def find_existing() -> dict | None:
    """Newest rdb-tagged cert for DOMAIN that is ISSUED, else PENDING_VALIDATION."""
    found: dict[str, dict] = {}
    pages = acm.get_paginator("list_certificates").paginate(
        CertificateStatuses=["ISSUED", "PENDING_VALIDATION"]
    )
    for page in pages:
        for s in page["CertificateSummaryList"]:
            if s["DomainName"] != DOMAIN:
                continue
            tags = acm.list_tags_for_certificate(CertificateArn=s["CertificateArn"])["Tags"]
            if {"Key": "Project", "Value": "RDB"} in tags:
                found[s["CertificateArn"]] = s
    for status in ("ISSUED", "PENDING_VALIDATION"):
        hits = [s for s in found.values() if s["Status"] == status]
        if hits:
            return max(hits, key=lambda s: s.get("CreatedAt") or s.get("IssuedAt"))
    return None


def dns_cname(name: str) -> str:
    r = subprocess.run(["dig", "+short", "CNAME", name], capture_output=True, text=True)
    return r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""


def validation_record(arn: str) -> dict | None:
    for _ in range(30):
        cert = acm.describe_certificate(CertificateArn=arn)["Certificate"]
        rr = cert["DomainValidationOptions"][0].get("ResourceRecord")
        if rr:
            return rr
        time.sleep(2)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout-minutes", type=float, default=20)
    a = ap.parse_args()

    existing = find_existing()
    if existing:
        arn = existing["CertificateArn"]
        print(f"Reusing existing rdb certificate ({existing['Status']}): {arn}")
    else:
        arn = acm.request_certificate(
            DomainName=DOMAIN, ValidationMethod="DNS", IdempotencyToken=TOKEN, Tags=TAGS
        )["CertificateArn"]
        print(f"Requested new certificate for {DOMAIN}: {arn}")

    cert = acm.describe_certificate(CertificateArn=arn)["Certificate"]
    if cert["Status"] != "ISSUED":
        rr = validation_record(arn)
        if not rr:
            sys.exit("ACM did not return a validation record yet; rerun in a minute.")
        name = rr["Name"].rstrip(".")
        value = rr["Value"].rstrip(".")
        current = dns_cname(name + ".").rstrip(".")
        print("\nDNS validation record (Cloudflare: type CNAME, DNS only / grey cloud):")
        print(f"  {'Name':<62} {'Type':<6} Value")
        print(f"  {name:<62} {rr['Type']:<6} {value}")
        if current == value:
            print("\nStatus: already in place in DNS. Waiting for ACM to validate...")
        else:
            print(
                f"\nStatus: NOT in DNS yet (currently: {current or 'no record'}).\n"
                ">>> ADD THE RECORD ABOVE IN CLOUDFLARE (DNS only). Polling until ISSUED..."
            )

    deadline = time.monotonic() + a.timeout_minutes * 60
    while True:
        status = acm.describe_certificate(CertificateArn=arn)["Certificate"]["Status"]
        print(f"[{time.strftime('%H:%M:%S')}] status: {status}", flush=True)
        if status == "ISSUED":
            break
        if status not in ("PENDING_VALIDATION",):
            sys.exit(f"Certificate ended in status {status}; not usable.")
        if time.monotonic() > deadline:
            sys.exit("Timed out waiting for ISSUED. Add the DNS record and rerun this script.")
        time.sleep(20)

    CONFIG.write_text(json.dumps({"cert_arn": arn}, indent=2) + "\n")
    print(f"\nISSUED. Wrote {CONFIG.relative_to(CONFIG.parents[1])}: cert_arn={arn}")


if __name__ == "__main__":
    main()
