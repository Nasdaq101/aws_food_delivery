from aws_cdk import (
    Stack,
    aws_apigateway as apigw,
    aws_apigatewayv2 as apigwv2,
)
from constructs import Construct


class ApiStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, compute_stack, auth_stack, **kwargs):
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
        auth_method_options = apigw.MethodOptions(authorizer=authorizer)

        # ── Route Definitions ──

        # /auth
        auth = self.api.root.add_resource("auth")
        auth.add_resource("signup").add_method("POST", apigw.LambdaIntegration(fns["auth-service"]))
        auth.add_resource("login").add_method("POST", apigw.LambdaIntegration(fns["auth-service"]))

        # /users
        users = self.api.root.add_resource("users")
        users.add_resource("me").add_method("GET", apigw.LambdaIntegration(fns["user-service"]), auth_method_options)
        user_by_id = users.add_resource("{user_id}")
        user_by_id.add_method("GET", apigw.LambdaIntegration(fns["user-service"]), auth_method_options)
        user_by_id.add_method("PUT", apigw.LambdaIntegration(fns["user-service"]), auth_method_options)

        # /restaurants
        restaurants = self.api.root.add_resource("restaurants")
        restaurants.add_method("GET", apigw.LambdaIntegration(fns["restaurant-service"]))
        restaurants.add_method("POST", apigw.LambdaIntegration(fns["restaurant-service"]), auth_method_options)
        restaurant_by_id = restaurants.add_resource("{restaurant_id}")
        restaurant_by_id.add_method("GET", apigw.LambdaIntegration(fns["restaurant-service"]))
        restaurant_by_id.add_method("PUT", apigw.LambdaIntegration(fns["restaurant-service"]), auth_method_options)

        # /restaurants/{id}/menu
        menu = restaurant_by_id.add_resource("menu")
        menu.add_method("GET", apigw.LambdaIntegration(fns["menu-service"]))
        menu.add_method("POST", apigw.LambdaIntegration(fns["menu-service"]), auth_method_options)
        menu_item = menu.add_resource("{item_id}")
        menu_item.add_method("PUT", apigw.LambdaIntegration(fns["menu-service"]), auth_method_options)
        menu_item.add_method("DELETE", apigw.LambdaIntegration(fns["menu-service"]), auth_method_options)

        # /search
        search = self.api.root.add_resource("search")
        search.add_method("GET", apigw.LambdaIntegration(fns["search-service"]))

        # /cart
        cart = self.api.root.add_resource("cart")
        cart.add_method("GET", apigw.LambdaIntegration(fns["cart-service"]), auth_method_options)
        cart.add_method("POST", apigw.LambdaIntegration(fns["cart-service"]), auth_method_options)
        cart.add_method("PUT", apigw.LambdaIntegration(fns["cart-service"]), auth_method_options)
        cart.add_method("DELETE", apigw.LambdaIntegration(fns["cart-service"]), auth_method_options)

        # /orders
        orders = self.api.root.add_resource("orders")
        orders.add_method("GET", apigw.LambdaIntegration(fns["order-service"]), auth_method_options)
        orders.add_method("POST", apigw.LambdaIntegration(fns["order-service"]), auth_method_options)
        order_by_id = orders.add_resource("{order_id}")
        order_by_id.add_method("GET", apigw.LambdaIntegration(fns["order-service"]), auth_method_options)
        order_by_id.add_method("PUT", apigw.LambdaIntegration(fns["order-service"]), auth_method_options)

        # /payments
        payments = self.api.root.add_resource("payments")
        payments.add_method("POST", apigw.LambdaIntegration(fns["payment-service"]), auth_method_options)
        payment_by_id = payments.add_resource("{payment_id}")
        payment_by_id.add_method("GET", apigw.LambdaIntegration(fns["payment-service"]), auth_method_options)

        # /deliveries
        deliveries = self.api.root.add_resource("deliveries")
        deliveries.add_method("GET", apigw.LambdaIntegration(fns["delivery-service"]), auth_method_options)
        delivery_by_id = deliveries.add_resource("{delivery_id}")
        delivery_by_id.add_method("GET", apigw.LambdaIntegration(fns["delivery-service"]), auth_method_options)

        # /drivers
        drivers = self.api.root.add_resource("drivers")
        drivers.add_method("POST", apigw.LambdaIntegration(fns["driver-service"]), auth_method_options)
        driver_by_id = drivers.add_resource("{driver_id}")
        driver_by_id.add_method("GET", apigw.LambdaIntegration(fns["driver-service"]), auth_method_options)
        driver_by_id.add_method("PUT", apigw.LambdaIntegration(fns["driver-service"]), auth_method_options)

        # /ratings
        ratings = self.api.root.add_resource("ratings")
        ratings.add_method("POST", apigw.LambdaIntegration(fns["rating-service"]), auth_method_options)
        ratings.add_resource("{target_id}").add_method("GET", apigw.LambdaIntegration(fns["rating-service"]))

        # /promotions
        promotions = self.api.root.add_resource("promotions")
        promotions.add_method("GET", apigw.LambdaIntegration(fns["promotion-service"]))
        promotions.add_method("POST", apigw.LambdaIntegration(fns["promotion-service"]), auth_method_options)
        promotions.add_resource("validate").add_method("POST", apigw.LambdaIntegration(fns["promotion-service"]), auth_method_options)

        # /analytics
        analytics = self.api.root.add_resource("analytics")
        analytics.add_method("GET", apigw.LambdaIntegration(fns["analytics-service"]), auth_method_options)

        # /admin
        admin = self.api.root.add_resource("admin")
        admin.add_resource("dashboard").add_method("GET", apigw.LambdaIntegration(fns["admin-service"]), auth_method_options)
        admin.add_resource("users").add_method("GET", apigw.LambdaIntegration(fns["admin-service"]), auth_method_options)
        admin.add_resource("orders").add_method("GET", apigw.LambdaIntegration(fns["admin-service"]), auth_method_options)

        # ── WebSocket API (for real-time tracking) ──
        self.ws_api = apigwv2.CfnApi(
            self, "TrackingWebSocketApi",
            name="FoodDelivery-TrackingWS",
            protocol_type="WEBSOCKET",
            route_selection_expression="$request.body.action",
        )
