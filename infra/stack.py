from aws_cdk import (
    CfnOutput, Duration, RemovalPolicy, Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
)
from constructs import Construct

MODEL_ID = "us.anthropic.claude-sonnet-4-6"
PREFIX = "news-weather-briefing"


class BriefingStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Set at deploy time: cdk deploy -c sender_email=you@example.com
        sender_email = self.node.try_get_context("sender_email") or ""
        schedule_hour = int(self.node.try_get_context("schedule_hour_utc") or 7)
        schedule_minute = int(self.node.try_get_context("schedule_minute_utc") or 0)

        table = dynamodb.Table(
            self, "BriefingTable",
            table_name=f"{PREFIX}-table",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,  # demo project: stack delete removes data
        )

        fn = _lambda.Function(
            self, "BriefingFunction",
            function_name=f"{PREFIX}-agent",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset("lambda/briefing"),
            timeout=Duration.seconds(120),
            memory_size=256,
            environment={
                "TABLE_NAME": table.table_name,
                "MODEL_ID": MODEL_ID,
                "SENDER_EMAIL": sender_email,
                "MAX_ITERATIONS": "6",
            },
        )
        table.grant_read_write_data(fn)

        # The us.* inference profile routes across US regions, so the
        # underlying foundation-model ARN must be allowed in any region.
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=[
                "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6*",
                f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{MODEL_ID}",
            ],
        ))
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:SendEmail"],
            resources=[f"arn:aws:ses:{self.region}:{self.account}:identity/*"],
        ))

        # Static site: S3 website hosting for index.html. The page holds no secrets,
        # so the bucket is public-read (website endpoints cannot use a private bucket).
        site = s3.Bucket(
            self, "SiteBucket",
            bucket_name=f"{PREFIX}-site-{self.account}",
            website_index_document="index.html",
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=True, ignore_public_acls=True,
                block_public_policy=False, restrict_public_buckets=False,
            ),
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # The page is on a different origin than the API, so the API must allow it (CORS).
        api = apigw.HttpApi(
            self, "BriefingApi", api_name=f"{PREFIX}-api",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=[site.bucket_website_url],
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.OPTIONS],
                allow_headers=["Content-Type"],
                max_age=Duration.days(1),
            ),
        )
        integration = integrations.HttpLambdaIntegration("LambdaIntegration", fn)
        api.add_routes(path="/preview", methods=[apigw.HttpMethod.POST], integration=integration)
        api.add_routes(path="/settings", methods=[apigw.HttpMethod.POST], integration=integration)

        events.Rule(
            self, "DailySchedule",
            rule_name=f"{PREFIX}-daily",
            schedule=events.Schedule.cron(minute=str(schedule_minute), hour=str(schedule_hour)),
            targets=[targets.LambdaFunction(fn)],
        )

        # web/index.html is uploaded as it is. config.js is generated here so the page
        # knows the API address.
        s3deploy.BucketDeployment(
            self, "DeploySite",
            destination_bucket=site,
            sources=[
                s3deploy.Source.asset("web"),
                s3deploy.Source.data("config.js", f"window.API_URL = '{api.url}';"),
            ],
            cache_control=[s3deploy.CacheControl.no_cache()],
        )

        CfnOutput(self, "SiteUrl", value=site.bucket_website_url)
        CfnOutput(self, "ApiUrl", value=api.url or "")
        CfnOutput(self, "TableName", value=table.table_name)
