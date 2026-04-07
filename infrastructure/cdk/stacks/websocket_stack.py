from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
    aws_apigatewayv2 as apigwv2,
    aws_lambda as _lambda,
    aws_iam as iam,
)
from constructs import Construct


class WebSocketStack(Stack):
    """
    WebSocket API Stack for real-time order tracking.

    This stack creates the WebSocket API Gateway and integrates it
    with the tracking-service Lambda for real-time updates.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        database_stack,
        auth_stack,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        # ── WebSocket Authorizer Lambda ──
        # Create this Lambda in WebSocketStack to avoid circular dependency
        ws_authorizer_fn = _lambda.Function(
            self,
            "WebSocketAuthorizerFunction",
            function_name="FoodDelivery-websocket-authorizer",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset("../../services/websocket-authorizer"),
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={
                "COGNITO_USER_POOL_ID": auth_stack.user_pool.user_pool_id,
                "COGNITO_REGION": self.region,
            },
        )

        # ── Tracking Service Lambda ──
        # Create this Lambda in WebSocketStack to avoid circular dependency
        tracking_fn = _lambda.Function(
            self,
            "TrackingServiceFunction",
            function_name="FoodDelivery-tracking-service",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset("../../services/tracking-service"),
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={},
        )

        # Grant DynamoDB permissions to tracking service
        database_stack.tracking_connections_table.grant_read_write_data(tracking_fn)
        tracking_fn.add_environment(
            "TRACKING_CONNECTIONS_TABLE_NAME",
            database_stack.tracking_connections_table.table_name
        )
        database_stack.deliveries_table.grant_read_write_data(tracking_fn)
        tracking_fn.add_environment(
            "DELIVERIES_TABLE_NAME",
            database_stack.deliveries_table.table_name
        )

        # ── WebSocket API ──
        self.ws_api = apigwv2.CfnApi(
            self, "TrackingWebSocketApi",
            name="FoodDelivery-TrackingWS",
            protocol_type="WEBSOCKET",
            route_selection_expression="$request.body.action",
        )

        # Grant tracking service permission to send messages to WebSocket connections
        tracking_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["execute-api:ManageConnections"],
                resources=[
                    f"arn:aws:execute-api:{self.region}:{self.account}:{self.ws_api.ref}/prod/POST/@connections/*"
                ]
            )
        )

        # WebSocket Authorizer
        ws_authorizer_invoke_arn = f"arn:aws:apigateway:{self.region}:lambda:path/2015-03-31/functions/{ws_authorizer_fn.function_arn}/invocations"

        ws_authorizer = apigwv2.CfnAuthorizer(
            self, "WebSocketAuthorizer",
            api_id=self.ws_api.ref,
            authorizer_type="REQUEST",
            authorizer_uri=ws_authorizer_invoke_arn,
            identity_source=["route.request.querystring.token"],
            name="CognitoWebSocketAuthorizer",
        )

        # Grant API Gateway permission to invoke authorizer
        ws_authorizer_fn.add_permission(
            "ApiGatewayAuthorizerInvoke",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=f"arn:aws:execute-api:{self.region}:{self.account}:{self.ws_api.ref}/*",
        )

        # WebSocket Integration (tracking-service)
        tracking_invoke_arn = f"arn:aws:apigateway:{self.region}:lambda:path/2015-03-31/functions/{tracking_fn.function_arn}/invocations"

        ws_integration = apigwv2.CfnIntegration(
            self, "TrackingIntegration",
            api_id=self.ws_api.ref,
            integration_type="AWS_PROXY",
            integration_uri=tracking_invoke_arn,
        )

        # Grant API Gateway permission to invoke tracking-service
        tracking_fn.add_permission(
            "ApiGatewayWsInvoke",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=f"arn:aws:execute-api:{self.region}:{self.account}:{self.ws_api.ref}/*",
        )

        # WebSocket Routes
        connect_route = apigwv2.CfnRoute(
            self, "ConnectRoute",
            api_id=self.ws_api.ref,
            route_key="$connect",
            authorization_type="CUSTOM",
            authorizer_id=ws_authorizer.ref,
            target=f"integrations/{ws_integration.ref}",
        )

        disconnect_route = apigwv2.CfnRoute(
            self, "DisconnectRoute",
            api_id=self.ws_api.ref,
            route_key="$disconnect",
            target=f"integrations/{ws_integration.ref}",
        )

        subscribe_route = apigwv2.CfnRoute(
            self, "SubscribeRoute",
            api_id=self.ws_api.ref,
            route_key="subscribe",
            target=f"integrations/{ws_integration.ref}",
        )

        sendloc_route = apigwv2.CfnRoute(
            self, "SendLocationRoute",
            api_id=self.ws_api.ref,
            route_key="sendLocation",
            target=f"integrations/{ws_integration.ref}",
        )

        # WebSocket Deployment
        ws_deployment = apigwv2.CfnDeployment(
            self, "WebSocketDeployment",
            api_id=self.ws_api.ref,
        )
        # Ensure routes are created before deployment
        ws_deployment.node.add_dependency(connect_route)
        ws_deployment.node.add_dependency(disconnect_route)
        ws_deployment.node.add_dependency(subscribe_route)
        ws_deployment.node.add_dependency(sendloc_route)

        # WebSocket Stage
        ws_stage = apigwv2.CfnStage(
            self, "WebSocketStage",
            api_id=self.ws_api.ref,
            stage_name="prod",
            deployment_id=ws_deployment.ref,
            default_route_settings=apigwv2.CfnStage.RouteSettingsProperty(
                logging_level="INFO",
                data_trace_enabled=True,
            ),
        )

        # Store WebSocket URL
        self.ws_url = f"wss://{self.ws_api.ref}.execute-api.{self.region}.amazonaws.com/prod"

        # Output
        CfnOutput(
            self, "WebSocketUrl",
            value=self.ws_url,
            description="WebSocket API URL for real-time tracking",
            export_name="FoodDeliveryWebSocketUrl"
        )
