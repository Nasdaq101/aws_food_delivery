from aws_cdk import (
    Stack,
    Duration,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_logs as logs,
    aws_ssm as ssm,
    RemovalPolicy,
    CfnOutput,
)
from constructs import Construct


class StepFunctionsStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        compute_stack,
        messaging_stack,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        # ── Create Log Groups for Step Functions ──
        self.order_workflow_log_group = logs.LogGroup(
            self,
            "OrderWorkflowLogGroup",
            log_group_name="/aws/stepfunctions/order-processing",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.delivery_workflow_log_group = logs.LogGroup(
            self,
            "DeliveryWorkflowLogGroup",
            log_group_name="/aws/stepfunctions/delivery-processing",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── Build Delivery Processing State Machine First ──
        self.delivery_state_machine = self._create_delivery_processing_workflow(
            compute_stack, messaging_stack
        )

        # ── Build Order Processing State Machine (references delivery state machine) ──
        self.order_state_machine = self._create_order_processing_workflow(
            compute_stack, messaging_stack
        )

        # ── Store State Machine ARN in SSM Parameter Store ──
        # This breaks the circular dependency - Lambda reads ARN at runtime
        self.order_workflow_param = ssm.StringParameter(
            self,
            "OrderWorkflowArnParameter",
            parameter_name="/fooddelivery/stepfunctions/order-workflow-arn",
            string_value=self.order_state_machine.state_machine_arn,
            description="ARN of the Order Processing Step Functions workflow",
        )

        # ── Outputs ──
        CfnOutput(
            self,
            "OrderWorkflowArn",
            value=self.order_state_machine.state_machine_arn,
            description="Order Processing Workflow ARN",
            export_name="FoodDelivery-OrderWorkflowArn",
        )

        CfnOutput(
            self,
            "DeliveryWorkflowArn",
            value=self.delivery_state_machine.state_machine_arn,
            description="Delivery Processing Workflow ARN",
            export_name="FoodDelivery-DeliveryWorkflowArn",
        )

    def _create_order_processing_workflow(self, compute_stack, messaging_stack):
        """
        Creates the main order processing workflow:
        1. Validate Order
        2. Process Payment
        3. Notify Restaurant
        4. Start Delivery Assignment (via nested workflow)
        5. Send Customer Notification
        6. Handle errors with rollback
        """

        # ── Step 1: Validate Order ──
        validate_order = tasks.LambdaInvoke(
            self,
            "ValidateOrder",
            lambda_function=compute_stack.functions["order-service"],
            payload=sfn.TaskInput.from_object({
                "action": "validate",
                "order_id.$": "$.order_id",
                "user_id.$": "$.user_id",
            }),
            result_path="$.validation",
            retry_on_service_exceptions=True,
        )

        # ── Step 1.5: Check Validation Result ──
        check_validation = sfn.Choice(self, "CheckValidation")

        validation_failed = sfn.Fail(
            self,
            "ValidationFailed",
            cause="Order validation failed",
            error="OrderValidationError",
        )

        # ── Step 2: Process Payment ──
        process_payment = tasks.LambdaInvoke(
            self,
            "ProcessPayment",
            lambda_function=compute_stack.functions["payment-service"],
            payload=sfn.TaskInput.from_object({
                "action": "charge",
                "order_id.$": "$.order_id",
                "amount.$": "$.validation.Payload.total_amount",
                "user_id.$": "$.user_id",
            }),
            result_path="$.payment",
            retry_on_service_exceptions=False,  # We'll handle payment retries manually
        ).add_retry(
            errors=["PaymentServiceException"],
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0,
        )

        # ── Step 3: Notify Restaurant ──
        notify_restaurant = tasks.SqsSendMessage(
            self,
            "NotifyRestaurant",
            queue=messaging_stack.order_queue,
            message_body=sfn.TaskInput.from_object({
                "type": "NEW_ORDER",
                "order_id.$": "$.order_id",
                "restaurant_id.$": "$.validation.Payload.restaurant_id",
                "timestamp.$": "$$.State.EnteredTime",
            }),
            result_path="$.notification",
        )

        # ── Step 4: Update Order Status to CONFIRMED ──
        update_order_confirmed = tasks.LambdaInvoke(
            self,
            "UpdateOrderConfirmed",
            lambda_function=compute_stack.functions["order-service"],
            payload=sfn.TaskInput.from_object({
                "action": "update_status",
                "order_id.$": "$.order_id",
                "status": "CONFIRMED",
            }),
            result_path="$.status_update",
        )

        # ── Step 4.5: Update Order Status to PREPARING ──
        update_order_preparing = tasks.LambdaInvoke(
            self,
            "UpdateOrderPreparing",
            lambda_function=compute_stack.functions["order-service"],
            payload=sfn.TaskInput.from_object({
                "action": "update_status",
                "order_id.$": "$.order_id",
                "status": "PREPARING",
            }),
            result_path="$.status_update_preparing",
        )

        # ── Step 5: Start Delivery Processing (nested state machine) ──
        start_delivery = tasks.StepFunctionsStartExecution(
            self,
            "StartDeliveryProcessing",
            state_machine=self.delivery_state_machine,
            input=sfn.TaskInput.from_object({
                "order_id.$": "$.order_id",
                "restaurant_id.$": "$.validation.Payload.restaurant_id",
                "restaurant_location.$": "$.validation.Payload.restaurant_location",
                "delivery_address.$": "$.validation.Payload.delivery_address",
            }),
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,  # Wait for completion
            result_path="$.delivery_processing",
        )

        # ── Step 5b: Update Order with Delivery ID ──
        update_order_with_delivery = tasks.LambdaInvoke(
            self,
            "UpdateOrderWithDeliveryID",
            lambda_function=compute_stack.functions["order-service"],
            payload=sfn.TaskInput.from_object({
                "action": "update_status",
                "order_id.$": "$.order_id",
                "status": "DELIVERED",
                "delivery_id.$": "$.delivery_processing.Output.delivery.Payload.delivery_id",
            }),
            result_path="$.delivery_update",
        )

        # ── Step 6: Send Customer Notification ──
        send_customer_notification = tasks.SnsPublish(
            self,
            "SendCustomerNotification",
            topic=messaging_stack.notification_topic,
            message=sfn.TaskInput.from_object({
                "type": "ORDER_DELIVERED",
                "order_id.$": "$.order_id",
                "user_id.$": "$.user_id",
                "driver_name.$": "$.delivery_processing.Output.drivers.Payload.best_driver.name",
                "estimated_time.$": "$.delivery_processing.Output.drivers.Payload.best_driver.eta",
            }),
            result_path=sfn.JsonPath.DISCARD,
        )

        # ── Success State ──
        order_success = sfn.Succeed(
            self,
            "OrderProcessingComplete",
            comment="Order successfully processed and delivery assigned",
        )

        # ── Error Handling: Check if Payment Succeeded ──
        check_payment_succeeded = sfn.Choice(self, "CheckPaymentSucceeded")

        # ── Error Handling: Refund Payment ──
        refund_payment = tasks.LambdaInvoke(
            self,
            "RefundPayment",
            lambda_function=compute_stack.functions["payment-service"],
            payload=sfn.TaskInput.from_object({
                "action": "refund",
                "order_id.$": "$.order_id",
                "payment_id.$": "$.payment.Payload.payment_id",
            }),
            result_path="$.refund",
        )

        # ── Error Handling: Update Order to FAILED (with refund) ──
        update_order_failed_with_refund = tasks.LambdaInvoke(
            self,
            "UpdateOrderFailedWithRefund",
            lambda_function=compute_stack.functions["order-service"],
            payload=sfn.TaskInput.from_object({
                "action": "update_status",
                "order_id.$": "$.order_id",
                "status": "FAILED",
                "error.$": "$.error",
            }),
            result_path="$.status_update",
        )

        # ── Error Handling: Update Order to FAILED (without refund) ──
        update_order_failed_no_refund = tasks.LambdaInvoke(
            self,
            "UpdateOrderFailedNoRefund",
            lambda_function=compute_stack.functions["order-service"],
            payload=sfn.TaskInput.from_object({
                "action": "update_status",
                "order_id.$": "$.order_id",
                "status": "FAILED",
                "error.$": "$.error",
            }),
            result_path="$.status_update",
        )

        # ── Error Handling: Notify Customer of Failure (with refund) ──
        notify_failure_with_refund = tasks.SnsPublish(
            self,
            "NotifyCustomerFailureWithRefund",
            topic=messaging_stack.notification_topic,
            message=sfn.TaskInput.from_object({
                "type": "ORDER_FAILED",
                "order_id.$": "$.order_id",
                "user_id.$": "$.user_id",
                "reason.$": "$.error",
            }),
            result_path=sfn.JsonPath.DISCARD,
        )

        # ── Error Handling: Notify Customer of Failure (without refund) ──
        notify_failure_no_refund = tasks.SnsPublish(
            self,
            "NotifyCustomerFailureNoRefund",
            topic=messaging_stack.notification_topic,
            message=sfn.TaskInput.from_object({
                "type": "ORDER_FAILED",
                "order_id.$": "$.order_id",
                "user_id.$": "$.user_id",
                "reason.$": "$.error",
            }),
            result_path=sfn.JsonPath.DISCARD,
        )

        order_failed = sfn.Fail(
            self,
            "OrderProcessingFailed",
            cause="Order processing failed",
            error="OrderProcessingError",
        )

        # ── Build Error Handling Chain ──
        # Only refund if payment succeeded
        error_handler = check_payment_succeeded.when(
            sfn.Condition.is_present("$.payment.Payload.payment_id"),
            refund_payment.next(update_order_failed_with_refund).next(notify_failure_with_refund).next(order_failed)
        ).otherwise(
            update_order_failed_no_refund.next(notify_failure_no_refund).next(order_failed)
        )

        # ── Build Main Workflow ──
        definition = (
            validate_order
            .next(
                check_validation
                .when(
                    sfn.Condition.boolean_equals("$.validation.Payload.valid", False),
                    validation_failed,
                )
                .otherwise(process_payment)
            )
        )

        process_payment.next(notify_restaurant).next(update_order_confirmed).next(update_order_preparing).next(start_delivery).next(update_order_with_delivery).next(send_customer_notification).next(order_success)

        # Add error handling to critical steps
        process_payment.add_catch(error_handler, errors=["States.ALL"], result_path="$.error")
        start_delivery.add_catch(error_handler, errors=["States.ALL"], result_path="$.error")

        # ── Create State Machine ──
        state_machine = sfn.StateMachine(
            self,
            "OrderProcessingStateMachine",
            state_machine_name="FoodDelivery-OrderProcessing",
            definition=definition,
            logs=sfn.LogOptions(
                destination=self.order_workflow_log_group,
                level=sfn.LogLevel.ALL,
            ),
            tracing_enabled=True,
            timeout=Duration.minutes(15),
        )

        return state_machine

    def _create_delivery_processing_workflow(self, compute_stack, messaging_stack):
        """
        Complete delivery processing workflow:
        1. Find Available Drivers (up to 5)
        2. Create Delivery Record
        3. Iterate through drivers sequentially
        4. Create offer with task token (wait for callback)
        5. Handle acceptance/rejection/timeout
        6. Retry with next driver if rejected
        7. Wait for driver to pick up (with task token)
        8. Wait for driver to complete delivery (with task token)
        """

        # ── Step 1: Find Available Drivers ──
        find_drivers = tasks.LambdaInvoke(
            self,
            "FindAvailableDrivers",
            lambda_function=compute_stack.functions["driver-service"],
            payload=sfn.TaskInput.from_object({
                "action": "find_available",
                "restaurant_location.$": "$.restaurant_location",
                "delivery_address.$": "$.delivery_address",
                "max_drivers": 5,
            }),
            result_path="$.drivers",
        )

        # ── Step 2: Check if Drivers Found ──
        check_drivers = sfn.Choice(self, "CheckDriversAvailable")

        no_drivers_available = sfn.Fail(
            self,
            "NoDriversAvailable",
            cause="No drivers available for delivery",
            error="NoDriversError",
        )

        # ── Step 3: Create Delivery Record (before assignment) ──
        create_delivery = tasks.LambdaInvoke(
            self,
            "CreateDeliveryRecord",
            lambda_function=compute_stack.functions["delivery-service"],
            payload=sfn.TaskInput.from_object({
                "action": "create",
                "order_id.$": "$.order_id",
                "restaurant_id.$": "$.restaurant_id",
                "customer_address.$": "$.delivery_address.address",
            }),
            result_path="$.delivery",
        )

        # ── Step 4: Initialize Driver Iteration ──
        init_iteration = sfn.Pass(
            self,
            "InitializeDriverIteration",
            parameters={
                "driver_index": 0,
                "max_drivers.$": "$.drivers.Payload.driver_count",
                "available_drivers.$": "$.drivers.Payload.drivers",
                "delivery_id.$": "$.delivery.Payload.delivery_id",
                "order_id.$": "$.order_id",
                "restaurant_id.$": "$.restaurant_id",
                "restaurant_location.$": "$.restaurant_location",
                "delivery_address.$": "$.delivery_address",
            },
            result_path="$.iteration",
        )

        # ── Step 8: Increment Driver Index (if rejected/timeout) ──
        # Define this early so we can reference it in the catch handler
        increment_index = sfn.Pass(
            self,
            "IncrementDriverIndex",
            parameters={
                "driver_index.$": "States.MathAdd($.iteration.driver_index, 1)",
                "max_drivers.$": "$.iteration.max_drivers",
                "available_drivers.$": "$.iteration.available_drivers",
                "delivery_id.$": "$.iteration.delivery_id",
                "order_id.$": "$.iteration.order_id",
                "restaurant_id.$": "$.iteration.restaurant_id",
                "restaurant_location.$": "$.iteration.restaurant_location",
                "delivery_address.$": "$.iteration.delivery_address",
            },
            result_path="$.iteration",
        )

        # ── Step 5: Create Driver Offer (with task token callback) ──
        create_offer = tasks.LambdaInvoke(
            self,
            "CreateDriverOffer",
            lambda_function=compute_stack.functions["delivery-service"],
            payload=sfn.TaskInput.from_object({
                "action": "create_offer",
                "delivery_id.$": "$.iteration.delivery_id",
                "order_id.$": "$.iteration.order_id",
                "driver.$": "States.ArrayGetItem($.iteration.available_drivers, $.iteration.driver_index)",
                "task_token": sfn.JsonPath.task_token,
                "restaurant_location.$": "$.iteration.restaurant_location",
                "delivery_address.$": "$.iteration.delivery_address",
            }),
            result_path="$.offer",
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            timeout=Duration.minutes(2),
        ).add_catch(
            handler=increment_index,
            errors=["States.Timeout"],
            result_path="$.error",
        )

        # ── Step 6: Check Offer Result ──
        check_offer_result = sfn.Choice(self, "CheckOfferResult")

        # ── Step 7: Finalize Assignment (if accepted) ──
        finalize_assignment = tasks.LambdaInvoke(
            self,
            "FinalizeDriverAssignment",
            lambda_function=compute_stack.functions["delivery-service"],
            payload=sfn.TaskInput.from_object({
                "action": "finalize_assignment",
                "delivery_id.$": "$.iteration.delivery_id",
                "offer_id.$": "$.offer.offer_id",
                "driver_id.$": "$.offer.driver_id",
            }),
            result_path="$.assignment",
        )

        # ── Step 7b: Update Order Status to DRIVER_ASSIGNED ──
        update_order_driver_assigned = tasks.LambdaInvoke(
            self,
            "UpdateOrderDriverAssigned",
            lambda_function=compute_stack.functions["order-service"],
            payload=sfn.TaskInput.from_object({
                "action": "update_status",
                "order_id.$": "$.iteration.order_id",
                "status": "DRIVER_ASSIGNED",
            }),
            result_path=sfn.JsonPath.DISCARD,
        )

        # ── Step 8: Store Task Token and Wait for Pickup ──
        wait_for_pickup = tasks.LambdaInvoke(
            self,
            "WaitForPickup",
            lambda_function=compute_stack.functions["delivery-service"],
            payload=sfn.TaskInput.from_object({
                "action": "store_pickup_token",
                "delivery_id.$": "$.iteration.delivery_id",
                "task_token": sfn.JsonPath.task_token,
            }),
            result_path="$.pickup",
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            timeout=Duration.hours(2),
        )

        # ── Step 9: Store Task Token and Wait for Delivery Completion ──
        wait_for_completion = tasks.LambdaInvoke(
            self,
            "WaitForDeliveryCompletion",
            lambda_function=compute_stack.functions["delivery-service"],
            payload=sfn.TaskInput.from_object({
                "action": "store_completion_token",
                "delivery_id.$": "$.iteration.delivery_id",
                "task_token": sfn.JsonPath.task_token,
            }),
            result_path="$.completion",
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            timeout=Duration.hours(2),
        )

        delivery_success = sfn.Succeed(
            self,
            "DeliveryProcessingComplete",
            comment="Delivery completed successfully",
        )

        # ── Step 9: Check if More Drivers Available ──
        check_more_drivers = sfn.Choice(self, "CheckMoreDrivers")

        all_rejected = sfn.Fail(
            self,
            "AllDriversRejected",
            cause="All available drivers rejected or timed out",
            error="AllDriversRejectedError",
        )

        # ── Build Workflow ──
        definition = (
            find_drivers
            .next(
                check_drivers
                .when(
                    sfn.Condition.number_equals("$.drivers.Payload.driver_count", 0),
                    no_drivers_available,
                )
                .otherwise(create_delivery)
            )
        )

        create_delivery.next(init_iteration).next(create_offer)

        create_offer.next(check_offer_result
            .when(
                sfn.Condition.string_equals("$.offer.status", "accepted"),
                finalize_assignment.next(update_order_driver_assigned).next(wait_for_pickup).next(wait_for_completion).next(delivery_success),
            )
            .when(
                sfn.Condition.is_present("$.error"),
                increment_index,
            )
            .otherwise(increment_index)
        )

        increment_index.next(check_more_drivers
            .when(
                sfn.Condition.number_less_than_json_path(
                    "$.iteration.driver_index",
                    "$.iteration.max_drivers"
                ),
                create_offer,
            )
            .otherwise(all_rejected)
        )

        # ── Create State Machine ──
        state_machine = sfn.StateMachine(
            self,
            "DeliveryProcessingStateMachine",
            state_machine_name="FoodDelivery-DeliveryProcessing",
            definition=definition,
            logs=sfn.LogOptions(
                destination=self.delivery_workflow_log_group,
                level=sfn.LogLevel.ALL,
            ),
            tracing_enabled=True,
            timeout=Duration.hours(3),  # Extended for full delivery lifecycle
        )

        return state_machine
