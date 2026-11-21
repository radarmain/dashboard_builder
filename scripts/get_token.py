"""Sign in with USER_PASSWORD_AUTH and print the ID token (for curl testing).

Prompts for email and password via getpass. Nothing is stored or logged. Prompts go to stderr so
this works in: TOKEN=$(.venv/bin/python scripts/get_token.py)
If the account has a temporary password, you'll be asked to choose a new one.
"""

import getpass
import os
import sys
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

region = os.environ.get("AWS_REGION", "us-east-2")
client_id = os.environ["COGNITO_APP_CLIENT_ID"]

sys.stderr.write("Email: ")
sys.stderr.flush()
email = sys.stdin.readline().strip()
password = getpass.getpass("Password: ", stream=sys.stderr)

# InitiateAuth is an unauthenticated API: no AWS credentials needed.
cognito = boto3.client("cognito-idp", region_name=region, config=Config(signature_version=UNSIGNED))
r = cognito.initiate_auth(
    ClientId=client_id,
    AuthFlow="USER_PASSWORD_AUTH",
    AuthParameters={"USERNAME": email, "PASSWORD": password},
)
if r.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
    new_pw = getpass.getpass("Temporary password: choose a NEW password: ", stream=sys.stderr)
    r = cognito.respond_to_auth_challenge(
        ClientId=client_id,
        ChallengeName="NEW_PASSWORD_REQUIRED",
        Session=r["Session"],
        ChallengeResponses={"USERNAME": email, "NEW_PASSWORD": new_pw},
    )
if "AuthenticationResult" not in r:
    print(f"unexpected challenge: {r.get('ChallengeName')}", file=sys.stderr)
    sys.exit(2)
print(r["AuthenticationResult"]["IdToken"])
