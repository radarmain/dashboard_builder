"""rdb-app: API (Lambda container + Web Adapter, streaming Function URL), two SPA buckets behind
CloudFront (OAC), same-origin /api via the Function URL, and the GitHub Actions deploy role.
Reuses rdb-core's table, bucket, user pool, app client and rdb-api-role (looked up by name)."""

import os

from aws_cdk import (
    CfnOutput,
    Duration,
    Fn,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_certificatemanager as acm,
)
from aws_cdk import (
    aws_cloudfront as cf,
)
from aws_cdk import (
    aws_cloudfront_origins as origins,
)
from aws_cdk import (
    aws_ecr_assets as ecr_assets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct

SECRET_NAME = "GBQ_lambda_secrets"
SECRET_KEY = "GLOBAL_GBQ_KEY"
BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-5-5"
RESERVED_CONCURRENCY = 50  # cost + BigQuery guardrail (account has 1000 unreserved)
GITHUB_REPO = "radarmain/dashboard_builder"  # trusted for refs/heads/main only
BOOTSTRAP_QUALIFIER = "hnb659fds"
OIDC_HOST = "token.actions.githubusercontent.com"
BASE_DOMAIN = "radar-analytics.com"
# CloudFront aliases, only when infra/config.json has a cert_arn: builder.<base> and *.<base>
ALIASES = {"builder": f"builder.{BASE_DOMAIN}", "viewer": f"*.{BASE_DOMAIN}"}

# Viewer-request function (default behavior only, never /api): SPA fallback without touching API
# status codes. Extensionless paths -> /index.html. (Distribution-level 403/404 error pages would
# also rewrite the API's own 404/403 responses.)
SPA_FUNCTION = """
function handler(event) {
  var r = event.request;
  var last = r.uri.split('/').pop();
  if (last.indexOf('.') === -1) { r.uri = '/index.html'; }
  return r;
}
"""


class RdbAppStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        table_name: str,
        data_bucket_name: str,
        user_pool_id: str,
        app_client_id: str,
        cert_arn: str = "",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- API: Lambda container image + Web Adapter, streaming Function URL ---
        role = iam.Role.from_role_arn(
            self, "ApiRole", f"arn:aws:iam::{self.account}:role/rdb-api-role", mutable=False
        )
        log_group = logs.LogGroup(
            self,
            "ApiLogs",
            log_group_name="/aws/lambda/rdb-api",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )
        backend_dir = os.path.join(os.path.dirname(__file__), "..", "backend")
        image = ecr_assets.DockerImageAsset(
            self, "ApiImage", directory=backend_dir, platform=ecr_assets.Platform.LINUX_ARM64
        )
        fn = lambda_.DockerImageFunction(
            self,
            "Api",
            function_name="rdb-api",
            code=lambda_.DockerImageCode.from_ecr(image.repository, tag_or_digest=image.asset_hash),
            architecture=lambda_.Architecture.ARM_64,
            memory_size=1024,
            timeout=Duration.seconds(300),
            role=role,
            log_group=log_group,
            reserved_concurrent_executions=RESERVED_CONCURRENCY,
            environment={
                "AWS_LWA_INVOKE_MODE": "response_stream",
                "RDB_TABLE_NAME": table_name,
                "RDB_DATA_BUCKET": data_bucket_name,
                "COGNITO_USER_POOL_ID": user_pool_id,
                "COGNITO_APP_CLIENT_ID": app_client_id,
                "RDB_BEDROCK_MODEL_ID": BEDROCK_MODEL_ID,
                "RDB_GBQ_SECRET_NAME": SECRET_NAME,
                "RDB_GBQ_SECRET_KEY": SECRET_KEY,
                "RDB_TABLEAU_SECRET_NAME": "rdb_tableau",
                "RDB_GEMINI_SECRET_NAME": "rdb_gemini",
                "BQ_DEFAULT_MAX_BYTES_BILLED": "10000000000",
                "RDB_CHAT_MAX_BYTES_BILLED": "2000000000",
            },
        )
        # Auth type NONE: the app verifies Cognito JWTs itself on every route except /health.
        fn_url = fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM,
        )
        api_origin = origins.HttpOrigin(
            Fn.select(2, Fn.split("/", fn_url.url)),
            protocol_policy=cf.OriginProtocolPolicy.HTTPS_ONLY,
            read_timeout=Duration.seconds(60),
        )

        # --- Frontends: private bucket + CloudFront (OAC) each, with same-origin /api ---
        spa_fn = cf.Function(
            self,
            "SpaRewrite",
            code=cf.FunctionCode.from_inline(SPA_FUNCTION),
            runtime=cf.FunctionRuntime.JS_2_0,
        )

        # Security headers for the SPA (default) behaviors only, never /api. HSTS has no
        # includeSubDomains on purpose: other subdomains on this domain are not ours.
        headers = cf.ResponseHeadersPolicy(
            self,
            "SecurityHeaders",
            response_headers_policy_name="rdb-security-headers",
            comment="RDB SPA security headers",
            security_headers_behavior=cf.ResponseSecurityHeadersBehavior(
                strict_transport_security=cf.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.seconds(31536000),
                    include_subdomains=False,
                    override=True,
                ),
                content_type_options=cf.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cf.ResponseHeadersFrameOptions(
                    frame_option=cf.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cf.ResponseHeadersReferrerPolicy(
                    referrer_policy=cf.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
            ),
        )
        certificate = (
            acm.Certificate.from_certificate_arn(self, "WildcardCert", cert_arn)
            if cert_arn
            else None
        )

        def web(name: str) -> tuple[s3.Bucket, cf.Distribution]:
            bucket = s3.Bucket(
                self,
                f"{name.title()}Bucket",
                bucket_name=f"rdb-{self.account}-{self.region}-{name}-web",
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                encryption=s3.BucketEncryption.S3_MANAGED,
                enforce_ssl=True,
                removal_policy=RemovalPolicy.RETAIN,
            )
            api_behavior = cf.BehaviorOptions(
                origin=api_origin,
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cf.AllowedMethods.ALLOW_ALL,
                cache_policy=cf.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                compress=False,  # never buffer/compress the SSE stream
            )
            dist = cf.Distribution(
                self,
                f"{name.title()}Dist",
                comment=f"rdb {name}",
                default_root_object="index.html",
                price_class=cf.PriceClass.PRICE_CLASS_100,
                http_version=cf.HttpVersion.HTTP2_AND_3,
                **(
                    {
                        "certificate": certificate,
                        "domain_names": [ALIASES[name]],
                        "minimum_protocol_version": cf.SecurityPolicyProtocol.TLS_V1_2_2021,
                    }
                    if certificate
                    else {}
                ),
                default_behavior=cf.BehaviorOptions(
                    origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                    viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
                    response_headers_policy=headers,
                    function_associations=[
                        cf.FunctionAssociation(
                            function=spa_fn, event_type=cf.FunctionEventType.VIEWER_REQUEST
                        )
                    ],
                ),
                additional_behaviors={"/api/*": api_behavior, "/health": api_behavior},
            )
            CfnOutput(self, f"{name.title()}BucketName", value=bucket.bucket_name)
            CfnOutput(self, f"{name.title()}DistributionId", value=dist.distribution_id)
            CfnOutput(self, f"{name.title()}Url", value=f"https://{dist.distribution_domain_name}")
            CfnOutput(self, f"{name.title()}Domain", value=dist.distribution_domain_name)
            return bucket, dist

        b_bucket, b_dist = web("builder")
        v_bucket, v_dist = web("viewer")

        # --- GitHub Actions deploy role (existing OIDC provider is reused, never modified) ---
        provider_arn = f"arn:aws:iam::{self.account}:oidc-provider/{OIDC_HOST}"
        deploy_role = iam.Role(
            self,
            "GithubDeployRole",
            role_name="rdb-github-deploy",
            description="GitHub Actions deploys for RDB (main branch only)",
            max_session_duration=Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                provider_arn,
                {
                    "StringEquals": {
                        f"{OIDC_HOST}:aud": "sts.amazonaws.com",
                        f"{OIDC_HOST}:sub": f"repo:{GITHUB_REPO}:ref:refs/heads/main",
                    }
                },
            ),
        )
        q = BOOTSTRAP_QUALIFIER
        deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[
                    f"arn:aws:iam::{self.account}:role/cdk-{q}-{kind}-role-{self.account}-{self.region}"
                    for kind in ("deploy", "file-publishing", "image-publishing", "lookup")
                ],
            )
        )
        deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudformation:DescribeStacks"],
                resources=[
                    f"arn:aws:cloudformation:{self.region}:{self.account}:stack/rdb-core/*",
                    f"arn:aws:cloudformation:{self.region}:{self.account}:stack/rdb-app/*",
                ],
            )
        )
        deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/cdk-bootstrap/{q}/version"
                ],
            )
        )
        for bucket in (b_bucket, v_bucket):
            deploy_role.add_to_policy(
                iam.PolicyStatement(actions=["s3:ListBucket"], resources=[bucket.bucket_arn])
            )
            deploy_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
                    resources=[bucket.arn_for_objects("*")],
                )
            )
        deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudfront:CreateInvalidation"],
                resources=[
                    f"arn:aws:cloudfront::{self.account}:distribution/{d.distribution_id}"
                    for d in (b_dist, v_dist)
                ],
            )
        )

        CfnOutput(self, "ApiFunctionName", value=fn.function_name)
        CfnOutput(self, "ApiFunctionUrl", value=fn_url.url)
        CfnOutput(self, "EcrRepository", value=image.repository.repository_name)
        CfnOutput(self, "ApiImageUri", value=image.image_uri)
        CfnOutput(self, "GithubDeployRoleArn", value=deploy_role.role_arn)
