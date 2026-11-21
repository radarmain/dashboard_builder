#!/usr/bin/env python3
import json
import os
from pathlib import Path

import aws_cdk as cdk
from rdb_app_stack import RdbAppStack
from rdb_core_stack import RdbCoreStack

app = cdk.App()

# Account/region come from the CLI profile (concrete). Region is expected to be us-east-2.
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-2"),
)

# infra/config.json {"cert_arn": "..."} is written by scripts/request_cert.py.
# Empty cert_arn = default CloudFront domains only (no aliases).
cert_arn = json.loads((Path(__file__).parent / "config.json").read_text()).get("cert_arn", "")

core = RdbCoreStack(app, "rdb-core", env=env)
RdbAppStack(
    app,
    "rdb-app",
    env=env,
    table_name=core.table_name,
    data_bucket_name=core.data_bucket_name,
    user_pool_id=core.user_pool_id,
    app_client_id=core.app_client_id,
    cert_arn=cert_arn,
)

cdk.Tags.of(app).add("Project", "RDB")
cdk.Tags.of(app).add("Owner", "william")
cdk.Tags.of(app).add("Env", "dev")

app.synth()
