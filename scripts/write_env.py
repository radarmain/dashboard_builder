"""Write rdb-core stack outputs into a gitignored .env (merging; other lines are preserved).

Run: AWS_PROFILE=concrete AWS_REGION=us-east-2 .venv/bin/python scripts/write_env.py
"""

import os
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"
REGION = os.environ.get("AWS_REGION", "us-east-2")

OUTPUT_TO_VARS = {
    "TableName": ["RDB_TABLE_NAME"],
    "DataBucketName": ["RDB_DATA_BUCKET"],
    "UserPoolId": ["COGNITO_USER_POOL_ID", "VITE_COGNITO_USER_POOL_ID"],
    "AppClientId": ["COGNITO_APP_CLIENT_ID", "VITE_COGNITO_APP_CLIENT_ID"],
    "ApiRoleArn": ["RDB_API_ROLE_ARN"],
}


def main() -> None:
    session = boto3.Session(profile_name="concrete", region_name=REGION)
    stack = session.client("cloudformation").describe_stacks(StackName="rdb-core")["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}

    values = {"AWS_PROFILE": "concrete", "AWS_REGION": REGION, "AWS_DEFAULT_REGION": REGION}
    values["VITE_AWS_REGION"] = REGION
    for key, names in OUTPUT_TO_VARS.items():
        for name in names:
            if key in outputs:
                values[name] = outputs[key]

    src = ENV if ENV.exists() else EXAMPLE
    lines = src.read_text().splitlines()
    seen: set[str] = set()
    new_lines = []
    for line in lines:
        name = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and name in values:
            new_lines.append(f"{name}={values[name]}")
            seen.add(name)
        else:
            new_lines.append(line)
    for name, val in values.items():
        if name not in seen:
            new_lines.append(f"{name}={val}")
    ENV.write_text("\n".join(new_lines) + "\n")
    print(f"wrote {ENV.name}: {', '.join(sorted(values))}")

    # Builder and Viewer read only public VITE_ values from their own gitignored .env.local.
    # VITE_API_URL is left unset on purpose: the dev server proxies /api to localhost:8000.
    vite = {k: v for k, v in values.items() if k.startswith("VITE_") and k != "VITE_API_URL"}
    for k in ("VITE_COGNITO_USER_POOL_ID", "VITE_COGNITO_APP_CLIENT_ID", "VITE_AWS_REGION"):
        vite.setdefault(k, values.get(k.replace("VITE_", ""), ""))
    for app in ("builder", "viewer"):
        target = ROOT / "frontend" / app / ".env.local"
        target.write_text("".join(f"{k}={v}\n" for k, v in sorted(vite.items())))
        print(f"wrote {target.relative_to(ROOT)}: {', '.join(sorted(vite))}")


if __name__ == "__main__":
    main()
