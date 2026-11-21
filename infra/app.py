#!/usr/bin/env python3
import os

import aws_cdk as cdk
from rdb_core_stack import RdbCoreStack

app = cdk.App()

# Account/region come from the CLI profile (concrete). Region is expected to be us-east-2.
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-2"),
)

RdbCoreStack(app, "rdb-core", env=env)

cdk.Tags.of(app).add("Project", "RDB")
cdk.Tags.of(app).add("Owner", "william")
cdk.Tags.of(app).add("Env", "dev")

app.synth()
