from aws_cdk import (
    Stack,
    aws_events as events,
    aws_events_targets as targets,
)
from constructs import Construct


class IntegrationStack(Stack):
    """
    Integration stack for cross-stack dependencies.

    This stack handles integrations between different stacks,
    particularly EventBridge rules that connect services.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        compute_stack,
        messaging_stack,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        broadcaster_lambda = compute_stack.functions["websocket-broadcaster"]

        # ── EventBridge Rules for WebSocket Broadcasting ──

        # Rule 1: OrderStatusChanged → WebSocket broadcaster
        events.Rule(
            self,
            "OrderStatusChangedRule",
            event_bus=messaging_stack.event_bus,
            event_pattern=events.EventPattern(
                source=["fooddelivery.orders"], detail_type=["OrderStatusChanged"]
            ),
            targets=[targets.LambdaFunction(broadcaster_lambda)],
        )

        # Rule 2: DriverLocationUpdate → WebSocket broadcaster
        events.Rule(
            self,
            "DriverLocationRule",
            event_bus=messaging_stack.event_bus,
            event_pattern=events.EventPattern(
                source=["fooddelivery.driver"], detail_type=["DriverLocationUpdate"]
            ),
            targets=[targets.LambdaFunction(broadcaster_lambda)],
        )

        # Rule 3: DriverOfferCreated → WebSocket broadcaster
        events.Rule(
            self,
            "DriverOfferCreatedRule",
            event_bus=messaging_stack.event_bus,
            event_pattern=events.EventPattern(
                source=["fooddelivery.delivery"], detail_type=["DriverOfferCreated"]
            ),
            targets=[targets.LambdaFunction(broadcaster_lambda)],
        )

        # Rule 4: DeliveryPickedUp → WebSocket broadcaster
        events.Rule(
            self,
            "DeliveryPickedUpRule",
            event_bus=messaging_stack.event_bus,
            event_pattern=events.EventPattern(
                source=["fooddelivery.delivery"], detail_type=["DeliveryPickedUp"]
            ),
            targets=[targets.LambdaFunction(broadcaster_lambda)],
        )

        # Rule 5: DeliveryCompleted → WebSocket broadcaster
        events.Rule(
            self,
            "DeliveryCompletedRule",
            event_bus=messaging_stack.event_bus,
            event_pattern=events.EventPattern(
                source=["fooddelivery.delivery"], detail_type=["DeliveryCompleted"]
            ),
            targets=[targets.LambdaFunction(broadcaster_lambda)],
        )
