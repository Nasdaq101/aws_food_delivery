from aws_cdk import (
    Stack,
    CfnOutput,
    CustomResource,
    aws_apigateway as apigw,
    aws_apigatewayv2 as apigwv2,
    aws_s3_deployment as s3deploy,
    aws_lambda as _lambda,
    aws_iam as iam,
    custom_resources as cr,
    Duration,
)
from constructs import Construct
import json


class ApiStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, compute_stack, auth_stack, storage_stack, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        fns = compute_stack.functions

        # ── REST API Gateway ──
        self.api = apigw.RestApi(
            self, "FoodDeliveryApi",
            rest_api_name="FoodDelivery-API",
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"],
            ),
        )

        # Cognito authorizer
        authorizer = apigw.CognitoUserPoolsAuthorizer(
            self, "CognitoAuthorizer",
            cognito_user_pools=[auth_stack.user_pool],
        )

        # ── Route Definitions ──

        # /auth
        auth = self.api.root.add_resource("auth")
        auth.add_resource("signup").add_method("POST", apigw.LambdaIntegration(fns["auth-service"]))
        auth.add_resource("login").add_method("POST", apigw.LambdaIntegration(fns["auth-service"]))
        auth.add_resource("verify").add_method("POST", apigw.LambdaIntegration(fns["auth-service"]))

        # /users
        users = self.api.root.add_resource("users")
        users.add_resource("me").add_method("GET", apigw.LambdaIntegration(fns["user-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        user_by_id = users.add_resource("{user_id}")
        user_by_id.add_method("GET", apigw.LambdaIntegration(fns["user-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        user_by_id.add_method("PUT", apigw.LambdaIntegration(fns["user-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # /restaurants
        restaurants = self.api.root.add_resource("restaurants")
        restaurants.add_method("GET", apigw.LambdaIntegration(fns["restaurant-service"]))
        restaurants.add_method("POST", apigw.LambdaIntegration(fns["restaurant-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        restaurant_by_id = restaurants.add_resource("{restaurant_id}")
        restaurant_by_id.add_method("GET", apigw.LambdaIntegration(fns["restaurant-service"]))
        restaurant_by_id.add_method("PUT", apigw.LambdaIntegration(fns["restaurant-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # /restaurants/{id}/menu
        menu = restaurant_by_id.add_resource("menu")
        menu.add_method("GET", apigw.LambdaIntegration(fns["menu-service"]))
        menu.add_method("POST", apigw.LambdaIntegration(fns["menu-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        menu_item = menu.add_resource("{item_id}")
        menu_item.add_method("PUT", apigw.LambdaIntegration(fns["menu-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        menu_item.add_method("DELETE", apigw.LambdaIntegration(fns["menu-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # /search
        search = self.api.root.add_resource("search")
        search.add_method("GET", apigw.LambdaIntegration(fns["search-service"]))

        # /cart
        cart = self.api.root.add_resource("cart")
        cart.add_method("GET", apigw.LambdaIntegration(fns["cart-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        cart.add_method("POST", apigw.LambdaIntegration(fns["cart-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        cart.add_method("PUT", apigw.LambdaIntegration(fns["cart-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        cart.add_method("DELETE", apigw.LambdaIntegration(fns["cart-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # /orders
        orders = self.api.root.add_resource("orders")
        orders.add_method("GET", apigw.LambdaIntegration(fns["order-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        orders.add_method("POST", apigw.LambdaIntegration(fns["order-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        order_by_id = orders.add_resource("{order_id}")
        order_by_id.add_method("GET", apigw.LambdaIntegration(fns["order-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        order_by_id.add_method("PUT", apigw.LambdaIntegration(fns["order-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # /payments
        payments = self.api.root.add_resource("payments")
        payments.add_method("POST", apigw.LambdaIntegration(fns["payment-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        payment_by_id = payments.add_resource("{payment_id}")
        payment_by_id.add_method("GET", apigw.LambdaIntegration(fns["payment-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # /deliveries
        deliveries = self.api.root.add_resource("deliveries")
        deliveries.add_method("GET", apigw.LambdaIntegration(fns["delivery-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        delivery_by_id = deliveries.add_resource("{delivery_id}")
        delivery_by_id.add_method("GET", apigw.LambdaIntegration(fns["delivery-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # /drivers
        drivers = self.api.root.add_resource("drivers")
        drivers.add_method("POST", apigw.LambdaIntegration(fns["driver-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        driver_by_id = drivers.add_resource("{driver_id}")
        driver_by_id.add_method("GET", apigw.LambdaIntegration(fns["driver-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        driver_by_id.add_method("PUT", apigw.LambdaIntegration(fns["driver-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # /ratings
        ratings = self.api.root.add_resource("ratings")
        ratings.add_method("POST", apigw.LambdaIntegration(fns["rating-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        ratings.add_resource("{target_id}").add_method("GET", apigw.LambdaIntegration(fns["rating-service"]))

        # /promotions
        promotions = self.api.root.add_resource("promotions")
        promotions.add_method("GET", apigw.LambdaIntegration(fns["promotion-service"]))
        promotions.add_method("POST", apigw.LambdaIntegration(fns["promotion-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        promotions.add_resource("validate").add_method("POST", apigw.LambdaIntegration(fns["promotion-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # /analytics
        analytics = self.api.root.add_resource("analytics")
        analytics.add_method("GET", apigw.LambdaIntegration(fns["analytics-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # /admin
        admin = self.api.root.add_resource("admin")
        admin.add_resource("dashboard").add_method("GET", apigw.LambdaIntegration(fns["admin-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        admin.add_resource("users").add_method("GET", apigw.LambdaIntegration(fns["admin-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)
        admin.add_resource("orders").add_method("GET", apigw.LambdaIntegration(fns["admin-service"]), authorizer=authorizer, authorization_type=apigw.AuthorizationType.COGNITO)

        # ── WebSocket API (for real-time tracking) ──
        self.ws_api = apigwv2.CfnApi(
            self, "TrackingWebSocketApi",
            name="FoodDelivery-TrackingWS",
            protocol_type="WEBSOCKET",
            route_selection_expression="$request.body.action",
        )

        # ── Outputs ──
        CfnOutput(
            self, "ApiUrl",
            value=self.api.url,
            description="Food Delivery API Gateway URL",
            export_name="FoodDeliveryApiUrl"
        )

        CfnOutput(
            self, "FrontendUrl",
            value=f"https://{storage_stack.frontend_bucket.bucket_website_domain_name}",
            description="Frontend website URL"
        )

        # ── Deploy entire frontend to S3 ──
        # First deploy the static frontend files
        import os
        frontend_path = os.path.join(os.path.dirname(__file__), "../../../frontend")

        frontend_deployment = s3deploy.BucketDeployment(
            self, "FrontendDeployment",
            sources=[s3deploy.Source.asset(frontend_path)],
            destination_bucket=storage_stack.frontend_bucket,
            distribution=storage_stack.distribution,
            distribution_paths=["/*"],  # Invalidate all paths after deployment
            exclude=["js/config.js"],  # Exclude - generated separately below
        )

        # ── Auto-deploy frontend config with API URL ──
        # Use a custom resource to write config.js directly to S3
        config_content = f'''// Auto-generated configuration file
// This file is automatically deployed by CDK
// DO NOT EDIT MANUALLY - regenerated on every deployment

window.APP_CONFIG = {{
    API_BASE_URL: "{self.api.url.rstrip('/')}",
    COGNITO_USER_POOL_ID: "{auth_stack.user_pool.user_pool_id}",
    COGNITO_CLIENT_ID: "{auth_stack.user_pool_client.user_pool_client_id}",
    COGNITO_REGION: "{self.region}"
}};
'''

        # Lambda function to write config to S3
        config_writer = _lambda.Function(
            self, "ConfigWriter",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=_lambda.Code.from_inline('''
import boto3
import json
import cfnresponse

s3 = boto3.client('s3')

def handler(event, context):
    try:
        if event['RequestType'] in ['Create', 'Update']:
            bucket = event['ResourceProperties']['Bucket']
            key = event['ResourceProperties']['Key']
            content = event['ResourceProperties']['Content']

            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType='application/javascript'
            )

        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
    except Exception as e:
        print(f"Error: {e}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {})
'''),
            timeout=Duration.seconds(60),
        )

        # Grant write permission to S3 bucket
        storage_stack.frontend_bucket.grant_write(config_writer)

        # Custom resource to trigger the Lambda
        config_custom_resource = CustomResource(
            self, "FrontendConfigResource",
            service_token=config_writer.function_arn,
            properties={
                'Bucket': storage_stack.frontend_bucket.bucket_name,
                'Key': 'js/config.js',
                'Content': config_content,
            }
        )

        # Ensure config is written after frontend files
        config_custom_resource.node.add_dependency(frontend_deployment)
