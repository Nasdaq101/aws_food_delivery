from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class DatabaseStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # ── Users Table ──
        self.users_table = dynamodb.Table(
            self, "UsersTable",
            table_name="FoodDelivery-Users",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.users_table.add_global_secondary_index(
            index_name="email-index",
            partition_key=dynamodb.Attribute(name="email", type=dynamodb.AttributeType.STRING),
        )

        # ── Restaurants Table ──
        self.restaurants_table = dynamodb.Table(
            self, "RestaurantsTable",
            table_name="FoodDelivery-Restaurants",
            partition_key=dynamodb.Attribute(name="restaurant_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.restaurants_table.add_global_secondary_index(
            index_name="city-rating-index",
            partition_key=dynamodb.Attribute(name="city", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="avg_rating", type=dynamodb.AttributeType.NUMBER),
        )

        # ── Menus Table ──
        self.menus_table = dynamodb.Table(
            self, "MenusTable",
            table_name="FoodDelivery-Menus",
            partition_key=dynamodb.Attribute(name="restaurant_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="item_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── Orders Table ──
        self.orders_table = dynamodb.Table(
            self, "OrdersTable",
            table_name="FoodDelivery-Orders",
            partition_key=dynamodb.Attribute(name="order_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.orders_table.add_global_secondary_index(
            index_name="user-orders-index",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
        )
        self.orders_table.add_global_secondary_index(
            index_name="restaurant-orders-index",
            partition_key=dynamodb.Attribute(name="restaurant_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
        )

        # ── Carts Table ──
        self.carts_table = dynamodb.Table(
            self, "CartsTable",
            table_name="FoodDelivery-Carts",
            partition_key=dynamodb.Attribute(name="user_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── Drivers Table ──
        self.drivers_table = dynamodb.Table(
            self, "DriversTable",
            table_name="FoodDelivery-Drivers",
            partition_key=dynamodb.Attribute(name="driver_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.drivers_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
        )

        # ── Deliveries Table ──
        self.deliveries_table = dynamodb.Table(
            self, "DeliveriesTable",
            table_name="FoodDelivery-Deliveries",
            partition_key=dynamodb.Attribute(name="delivery_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.deliveries_table.add_global_secondary_index(
            index_name="order-index",
            partition_key=dynamodb.Attribute(name="order_id", type=dynamodb.AttributeType.STRING),
        )

        # ── Ratings Table ──
        self.ratings_table = dynamodb.Table(
            self, "RatingsTable",
            table_name="FoodDelivery-Ratings",
            partition_key=dynamodb.Attribute(name="target_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="rating_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── Promotions Table ──
        self.promotions_table = dynamodb.Table(
            self, "PromotionsTable",
            table_name="FoodDelivery-Promotions",
            partition_key=dynamodb.Attribute(name="promo_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── Payments Table ──
        self.payments_table = dynamodb.Table(
            self, "PaymentsTable",
            table_name="FoodDelivery-Payments",
            partition_key=dynamodb.Attribute(name="payment_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.payments_table.add_global_secondary_index(
            index_name="order-index",
            partition_key=dynamodb.Attribute(name="order_id", type=dynamodb.AttributeType.STRING),
        )

        # ── Analytics Table ──
        self.analytics_table = dynamodb.Table(
            self, "AnalyticsTable",
            table_name="FoodDelivery-Analytics",
            partition_key=dynamodb.Attribute(name="metric_type", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── Tracking Connections Table (WebSocket) ──
        self.tracking_connections_table = dynamodb.Table(
            self, "TrackingConnectionsTable",
            table_name="FoodDelivery-TrackingConnections",
            partition_key=dynamodb.Attribute(name="connection_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Add GSI for efficient lookup of connections by delivery_id (for broadcasting)
        self.tracking_connections_table.add_global_secondary_index(
            index_name="delivery-connections-index",
            partition_key=dynamodb.Attribute(name="subscribed_delivery_id", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.KEYS_ONLY,  # Only need connection_id
        )
