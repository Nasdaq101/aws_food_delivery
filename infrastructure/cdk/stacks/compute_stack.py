from aws_cdk import (
    Stack,
    Duration,
    aws_lambda as _lambda,
    aws_iam as iam,
)
from constructs import Construct

SERVICES = [
    "user-service",
    "auth-service",
    "restaurant-service",
    "menu-service",
    "search-service",
    "cart-service",
    "order-service",
    "payment-service",
    "delivery-service",
    "driver-service",
    "notification-service",
    "rating-service",
    "promotion-service",
    "tracking-service",
    "analytics-service",
    "geolocation-service",
    "admin-service",
]


class ComputeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        database_stack,
        storage_stack,
        auth_stack,
        messaging_stack,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        self.functions: dict[str, _lambda.Function] = {}

        common_env = {
            "EVENT_BUS_NAME": messaging_stack.event_bus.event_bus_name,
            "ORDER_QUEUE_URL": messaging_stack.order_queue.queue_url,
            "NOTIFICATION_QUEUE_URL": messaging_stack.notification_queue.queue_url,
            "IMAGES_BUCKET": storage_stack.images_bucket.bucket_name,
        }

        for svc in SERVICES:
            # Add Cognito env vars for auth-service
            env_vars = {**common_env}
            if svc == "auth-service":
                env_vars["COGNITO_USER_POOL_ID"] = auth_stack.user_pool.user_pool_id
                env_vars["COGNITO_CLIENT_ID"] = auth_stack.user_pool_client.user_pool_client_id

            fn = _lambda.Function(
                self,
                f"Fn-{svc}",
                function_name=f"FoodDelivery-{svc}",
                runtime=_lambda.Runtime.PYTHON_3_12,
                handler="handler.lambda_handler",
                code=_lambda.Code.from_asset(f"../../services/{svc}"),
                timeout=Duration.seconds(30),
                memory_size=256,
                environment=env_vars,
            )
            self.functions[svc] = fn

        # ── Grant permissions ──

        # DynamoDB access
        table_service_map = {
            "users_table": ["user-service", "auth-service", "admin-service", "order-service"],
            "restaurants_table": ["restaurant-service", "search-service", "admin-service"],
            "menus_table": ["menu-service", "search-service"],
            "orders_table": ["order-service", "admin-service", "analytics-service"],
            "carts_table": ["cart-service", "order-service"],
            "drivers_table": ["driver-service", "delivery-service"],
            "deliveries_table": ["delivery-service", "tracking-service"],
            "ratings_table": ["rating-service"],
            "promotions_table": ["promotion-service", "order-service"],
            "payments_table": ["payment-service"],
            "analytics_table": ["analytics-service"],
            "tracking_connections_table": ["tracking-service"],
        }

        for table_attr, svc_list in table_service_map.items():
            table = getattr(database_stack, table_attr)
            env_var_name = table_attr.upper() + "_NAME"  # e.g., "users_table" -> "USERS_TABLE_NAME"
            for svc in svc_list:
                table.grant_read_write_data(self.functions[svc])
                # Add table name to Lambda environment
                self.functions[svc].add_environment(env_var_name, table.table_name)

        # S3 access
        storage_stack.images_bucket.grant_read(self.functions["restaurant-service"])
        storage_stack.images_bucket.grant_read(self.functions["menu-service"])

        # EventBridge access
        messaging_stack.event_bus.grant_put_events_to(self.functions["order-service"])
        messaging_stack.event_bus.grant_put_events_to(self.functions["delivery-service"])
        messaging_stack.event_bus.grant_put_events_to(self.functions["payment-service"])

        # SQS access
        messaging_stack.order_queue.grant_send_messages(self.functions["order-service"])
        messaging_stack.notification_queue.grant_send_messages(self.functions["notification-service"])
        messaging_stack.payment_queue.grant_send_messages(self.functions["payment-service"])
        messaging_stack.payment_queue.grant_consume_messages(self.functions["payment-service"])

        # SNS access
        messaging_stack.order_topic.grant_publish(self.functions["order-service"])
        messaging_stack.delivery_topic.grant_publish(self.functions["delivery-service"])
        messaging_stack.notification_topic.grant_publish(self.functions["notification-service"])

        # ── Step Functions & SSM Permissions for order-service ──
        # Allow order-service to start Step Functions workflows
        self.functions["order-service"].add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["states:StartExecution"],
                resources=[f"arn:aws:states:{self.region}:{self.account}:stateMachine:FoodDelivery-*"],
            )
        )

        # Allow order-service to read SSM parameters
        self.functions["order-service"].add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["ssm:GetParameter", "ssm:GetParameters"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/fooddelivery/*"],
            )
        )
