from aws_cdk import (
    Stack,
    Duration,
    aws_sqs as sqs,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_events as events,
)
from constructs import Construct


class MessagingStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # ── EventBridge Event Bus ──
        self.event_bus = events.EventBus(
            self, "FoodDeliveryEventBus",
            event_bus_name="FoodDeliveryEventBus",
        )

        # ── SQS Queues ──

        # Order processing queue
        self.order_queue_dlq = sqs.Queue(self, "OrderQueueDLQ", queue_name="FoodDelivery-OrderQueue-DLQ")
        self.order_queue = sqs.Queue(
            self, "OrderQueue",
            queue_name="FoodDelivery-OrderQueue",
            visibility_timeout=Duration.seconds(300),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=self.order_queue_dlq),
        )

        # Notification queue
        self.notification_queue_dlq = sqs.Queue(self, "NotificationQueueDLQ", queue_name="FoodDelivery-NotificationQueue-DLQ")
        self.notification_queue = sqs.Queue(
            self, "NotificationQueue",
            queue_name="FoodDelivery-NotificationQueue",
            visibility_timeout=Duration.seconds(60),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=self.notification_queue_dlq),
        )

        # Payment processing queue
        self.payment_queue_dlq = sqs.Queue(self, "PaymentQueueDLQ", queue_name="FoodDelivery-PaymentQueue-DLQ")
        self.payment_queue = sqs.Queue(
            self, "PaymentQueue",
            queue_name="FoodDelivery-PaymentQueue",
            visibility_timeout=Duration.seconds(120),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=self.payment_queue_dlq),
        )

        # Analytics queue
        self.analytics_queue = sqs.Queue(
            self, "AnalyticsQueue",
            queue_name="FoodDelivery-AnalyticsQueue",
            visibility_timeout=Duration.seconds(60),
        )

        # ── SNS Topics ──

        self.order_topic = sns.Topic(self, "OrderTopic", topic_name="FoodDelivery-OrderEvents")
        self.delivery_topic = sns.Topic(self, "DeliveryTopic", topic_name="FoodDelivery-DeliveryEvents")
        self.notification_topic = sns.Topic(self, "NotificationTopic", topic_name="FoodDelivery-Notifications")

        # SNS → SQS subscriptions
        self.notification_topic.add_subscription(subs.SqsSubscription(self.notification_queue))
        self.order_topic.add_subscription(subs.SqsSubscription(self.analytics_queue))
