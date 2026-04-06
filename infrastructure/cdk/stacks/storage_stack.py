from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
)
from constructs import Construct


class StorageStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # ── Frontend Hosting Bucket ──
        self.frontend_bucket = s3.Bucket(
            self, "FrontendBucket",
            website_index_document="index.html",
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                ignore_public_acls=False,
                block_public_policy=False,
                restrict_public_buckets=False,
            ),
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ── Image Upload Bucket ──
        self.images_bucket = s3.Bucket(
            self, "ImagesBucket",
            cors=[s3.CorsRule(
                allowed_methods=[s3.HttpMethods.PUT, s3.HttpMethods.GET],
                allowed_origins=["*"],
                allowed_headers=["*"],
            )],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ── CloudFront Distribution ──
        self.distribution = cloudfront.Distribution(
            self, "FrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(self.frontend_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            additional_behaviors={
                "/images/*": cloudfront.BehaviorOptions(
                    origin=origins.S3Origin(self.images_bucket),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                ),
            },
        )

        # ── Outputs ──
        CfnOutput(
            self, "CloudFrontUrl",
            value=f"https://{self.distribution.distribution_domain_name}",
            description="CloudFront distribution URL (HTTPS)"
        )

        CfnOutput(
            self, "S3WebsiteUrl",
            value=self.frontend_bucket.bucket_website_url,
            description="S3 website URL (HTTP only)"
        )
