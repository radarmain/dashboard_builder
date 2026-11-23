"""Create (or reuse) the SES domain identity for Cognito invite emails. Idempotent.

  AWS_PROFILE=concrete python scripts/setup_ses_identity.py [--wait MINUTES]

Domain notify.radar-analytics.com, Easy DKIM 2048-bit, tagged Project=RDB, no custom MAIL FROM.
Prints the three DKIM CNAMEs to add in Cloudflare (DNS only). Never touches DNS or other identities.
--wait polls until DKIM status is SUCCESS (default timeout 30 min).
"""

import sys
import time

import boto3
from botocore.exceptions import ClientError

DOMAIN = "notify.radar-analytics.com"
REGION = "us-east-2"
ses = boto3.Session(profile_name="concrete", region_name=REGION).client("sesv2")


def get_or_create() -> dict:
    try:
        ident = ses.get_email_identity(EmailIdentity=DOMAIN)
        print(f"Identity {DOMAIN} already exists in {REGION}: reusing it.")
        return ident
    except ClientError as e:
        if e.response["Error"]["Code"] != "NotFoundException":
            raise
    ses.create_email_identity(
        EmailIdentity=DOMAIN,
        DkimSigningAttributes={"NextSigningKeyLength": "RSA_2048_BIT"},
        Tags=[{"Key": "Project", "Value": "RDB"}],
    )
    print(f"Created identity {DOMAIN} in {REGION} (Easy DKIM, 2048-bit, Project=RDB).")
    return ses.get_email_identity(EmailIdentity=DOMAIN)


def records(ident: dict) -> list[tuple[str, str, str]]:
    return [
        (f"{t}._domainkey.{DOMAIN}", "CNAME", f"{t}.dkim.amazonses.com")
        for t in ident["DkimAttributes"].get("Tokens", [])
    ]


def main() -> None:
    ident = get_or_create()
    d = ident["DkimAttributes"]
    print(
        f"DKIM status: {d.get('Status')}  verified for sending: {ident.get('VerifiedForSendingStatus')}"
    )
    print(
        "\nAdd these in Cloudflare (all 'DNS only' / grey cloud). They are the ONLY DNS changes:\n"
    )
    print("| Name | Type | Value |\n|---|---|---|")
    for n, t, v in records(ident):
        print(f"| {n} | {t} | {v} |")
    acct = ses.get_account()
    print(
        f"\nProduction access: {acct['ProductionAccessEnabled']} (False = SES sandbox) "
        f"daily quota {acct['SendQuota']['Max24HourSend']:.0f}"
    )
    if "--wait" in sys.argv:
        deadline = time.time() + 30 * 60
        while time.time() < deadline:
            st = ses.get_email_identity(EmailIdentity=DOMAIN)["DkimAttributes"].get("Status")
            print(f"  DKIM {st}", flush=True)
            if st == "SUCCESS":
                return
            if st == "FAILED":
                sys.exit("DKIM FAILED: check the three CNAME records")
            time.sleep(30)
        sys.exit("Timed out after 30 min waiting for DKIM SUCCESS")


if __name__ == "__main__":
    main()
