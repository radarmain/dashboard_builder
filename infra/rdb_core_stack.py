from aws_cdk import (
    ArnFormat,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_cognito as cognito,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct

SECRET_NAME = "GBQ_lambda_secrets"  # existing secret; referenced by name only, never modified
BEDROCK_PROFILE = "us.anthropic.claude-sonnet-5-5"


class RdbCoreStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- DynamoDB: single table ---
        table = dynamodb.Table(
            self,
            "MainTable",
            table_name="rdb_main",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # --- S3: data bucket (specs, revisions, schemas, cache) ---
        bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=f"rdb-{self.account}-{self.region}-data",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="rdb-expire-cache",
                    prefix="cache/",
                    expiration=Duration.days(7),
                    noncurrent_version_expiration=Duration.days(1),
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                )
            ],
        )

        # --- Cognito: user pool, groups, app client ---
        pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name="rdb-users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            # Invitation email (from Cognito's default sender; SES is a later improvement).
            # Keep {username} and {####} exactly: Cognito fills in the email and temporary password.
            user_invitation=cognito.UserInvitationConfig(
                email_subject="You've been invited to RADaR Dashboards",
                email_body=(
                    "Hi,<br><br>"
                    "You've been invited to RADaR Dashboards. Open the dashboard link your RADaR "
                    "contact shared with you and sign in with these details:<br><br>"
                    "Email: {username}<br>"
                    "Temporary password: {####}<br><br>"
                    "You'll be asked to choose a new password when you first sign in.<br><br>"
                    "Thanks,<br>RADaR"
                ),
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        for group in ("admin", "analyst", "client"):
            cognito.CfnUserPoolGroup(
                self,
                f"Group-{group}",
                user_pool_id=pool.user_pool_id,
                group_name=group,
                description=f"RDB {group} users",
            )
        app_client = pool.add_client(
            "AppClient",
            user_pool_client_name="rdb-app",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=True, user_password=True),
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
        )

        # --- IAM: API role (ECS task or Lambda) ---
        api_role = iam.Role(
            self,
            "ApiRole",
            role_name="rdb-api-role",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
                iam.ServicePrincipal("lambda.amazonaws.com"),
            ),
            description="RDB API runtime role",
        )
        table.grant_read_write_data(api_role)
        bucket.grant_read_write(api_role)
        # Read-only access to the EXISTING secret (ARN has a random 6-char suffix, hence ??????).
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    self.format_arn(
                        service="secretsmanager",
                        resource="secret",
                        resource_name=f"{SECRET_NAME}-??????",
                        arn_format=ArnFormat.COLON_RESOURCE_NAME,
                    )
                ],
            )
        )
        # Bedrock: only the Sonnet 5.5 US inference profile (+ the foundation models it routes to).
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/anthropic.*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{BEDROCK_PROFILE}",
                ],
            )
        )
        # Lambda log writes (the rdb-app stack creates the /aws/lambda/rdb-api log group).
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/rdb-api:*"
                ],
            )
        )
        # Cognito admin ops for user management, scoped to our pool. NO delete actions: users are
        # disabled, never deleted.
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    f"cognito-idp:{a}"
                    for a in (
                        "AdminCreateUser",
                        "AdminGetUser",
                        "AdminAddUserToGroup",
                        "AdminRemoveUserFromGroup",
                        "AdminListGroupsForUser",
                        "AdminDisableUser",
                        "AdminEnableUser",
                        "AdminUserGlobalSignOut",
                        "ListUsers",
                        "ListUsersInGroup",
                    )
                ],
                resources=[pool.user_pool_arn],
            )
        )

        # Referenced by the rdb-app stack (CDK turns these into exports)
        self.table_name = table.table_name
        self.data_bucket_name = bucket.bucket_name
        self.user_pool_id = pool.user_pool_id
        self.app_client_id = app_client.user_pool_client_id

        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "DataBucketName", value=bucket.bucket_name)
        CfnOutput(self, "UserPoolId", value=pool.user_pool_id)
        CfnOutput(self, "AppClientId", value=app_client.user_pool_client_id)
        CfnOutput(self, "ApiRoleArn", value=api_role.role_arn)
