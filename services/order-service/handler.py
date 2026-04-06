import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

# todos are left, you can decide to implement it or not.

dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")
sqs = boto3.client("sqs")
stepfunctions = boto3.client("stepfunctions")
ssm = boto3.client("ssm")

ORDERS_TABLE = os.environ.get("ORDERS_TABLE_NAME", "FoodDelivery-Orders")
CARTS_TABLE = os.environ.get("CARTS_TABLE_NAME", "FoodDelivery-Carts")
USERS_TABLE = os.environ.get("USERS_TABLE_NAME", "FoodDelivery-Users")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "FoodDeliveryEventBus")
ORDER_QUEUE_URL = os.environ.get("ORDER_QUEUE_URL", "")
ORDER_WORKFLOW_PARAM = "/fooddelivery/stepfunctions/order-workflow-arn"

# Cache for Step Functions ARN (loaded on first invocation)
_order_workflow_arn = None

orders_table = dynamodb.Table(ORDERS_TABLE)
carts_table = dynamodb.Table(CARTS_TABLE)
users_table = dynamodb.Table(USERS_TABLE)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get_order_workflow_arn():
    """Get Step Functions workflow ARN from SSM Parameter Store (cached)"""
    global _order_workflow_arn
    if _order_workflow_arn:
        return _order_workflow_arn

    try:
        response = ssm.get_parameter(Name=ORDER_WORKFLOW_PARAM)
        _order_workflow_arn = response["Parameter"]["Value"]
        return _order_workflow_arn
    except ClientError as e:
        print(f"Failed to get Step Functions ARN from SSM: {e}")
        return None


def _get_user_role(user_id: str):
    """Get user's role from the users table"""
    try:
        res = users_table.get_item(Key={"user_id": user_id})
        user = res.get("Item")
        if not user:
            return None
        return user.get("role", "customer")  # Default to customer if role not set
    except ClientError as e:
        print(f"Failed to get user role: {e}")
        return None


def handle_list_orders(user_id: str):
    print(f"Listing orders for user_id: {user_id}")

    if not user_id:
        print("Missing user_id for list orders")
        return response(401, {"error": "Unauthorized", "message": "Missing authenticated user"})
    try:
        res = orders_table.query(
            IndexName="user-orders-index",
            KeyConditionExpression=Key("user_id").eq(user_id),
            ScanIndexForward=False,
        )
        items = res.get("Items", [])
        while "LastEvaluatedKey" in res:
            res = orders_table.query(
                IndexName="user-orders-index",
                KeyConditionExpression=Key("user_id").eq(user_id),
                ExclusiveStartKey=res["LastEvaluatedKey"],
                ScanIndexForward=False,
            )
            items.extend(res.get("Items", []))

        print(f"Found {len(items)} orders for user {user_id}")
        return response(200, {"orders": items})
    except ClientError as e:
        print(f"Error querying orders: {str(e)}")
        raise


def handle_create_order(user_id: str, body: dict):
    print(f"Creating order for user_id: {user_id}, body: {body}")

    if not user_id:
        print("Missing user_id")
        return response(401, {"error": "Unauthorized", "message": "Missing authenticated user"})

    # Check user role - only customers can place orders
    user_role = _get_user_role(user_id)
    print(f"User role: {user_role}")

    if not user_role:
        print("User profile not found")
        return response(404, {"error": "NotFound", "message": "User profile not found"})

    if user_role != "customer":
        print(f"User role '{user_role}' is not allowed to place orders")
        return response(403, {
            "error": "Forbidden",
            "message": f"Only customers can place orders. Your role is '{user_role}'"
        })

    cart_res = carts_table.get_item(Key={"user_id": user_id})
    cart = cart_res.get("Item") or {"user_id": user_id, "items": []}
    lines = cart.get("items") or []
    print(f"Cart has {len(lines)} items")

    if not lines:
        print("Cart is empty")
        return response(400, {"error": "BadRequest", "message": "Cart is empty"})
    # TODO: enforce single-restaurant carts, re-fetch menu prices, payment pre-auth
    restaurant_id = body.get("restaurant_id") or lines[0].get("restaurant_id")
    if not restaurant_id:
        return response(400, {"error": "BadRequest", "message": "Unable to determine restaurant_id"})
    order_id = str(uuid.uuid4())
    created_at = _now_iso()

    # Calculate total amount
    total_amount = sum(item.get("quantity", 0) * item.get("unit_price_cents", 0) for item in lines) / 100
    total_amount_decimal = Decimal(str(total_amount))

    order = {
        "order_id": order_id,
        "user_id": user_id,
        "restaurant_id": restaurant_id,
        "items": lines,
        "total": total_amount_decimal,
        "status": "PLACED",
        "created_at": created_at,
        "updated_at": created_at,
        "notes": body.get("notes") or "",
    }
    print(f"Creating order: {order_id} for ${total_amount}")
    orders_table.put_item(Item=order)
    carts_table.put_item(Item={"user_id": user_id, "items": []})
    print("Order saved to DynamoDB")

    # ── Trigger Step Functions Workflow ──
    workflow_arn = _get_order_workflow_arn()
    if workflow_arn:
        try:
            stepfunctions.start_execution(
                stateMachineArn=workflow_arn,
                name=f"order-{order_id}",
                input=json.dumps({
                    "order_id": order_id,
                    "user_id": user_id,
                    "restaurant_id": restaurant_id,
                    "total_amount": float(total_amount),  # Convert Decimal to float for JSON
                    "items": lines,
                }, default=str)  # Handle any remaining Decimal types
            )
            print(f"Started Step Functions workflow for order {order_id}")
        except ClientError as e:
            print(f"Failed to start Step Functions workflow: {e}")
            # Don't fail the order creation if workflow fails to start
        except Exception as e:
            print(f"Unexpected error starting workflow: {e}")
            # Don't fail the order creation if workflow fails to start
    else:
        print("Step Functions workflow ARN not found, using fallback")

        # Fallback to legacy SQS method if Step Functions not configured
        if ORDER_QUEUE_URL:
            try:
                sqs.send_message(
                    QueueUrl=ORDER_QUEUE_URL,
                    MessageBody=json.dumps({"order_id": order_id, "event": "ORDER_PLACED"}, default=str),
                )
            except ClientError:
                # TODO: dead-letter / retry policy — order already persisted
                raise

    # TODO: emit ORDER_PLACED on EventBridge if downstream needs it
    print(f"Order {order_id} created successfully, returning 201")
    return response(201, {"order": order})


def handle_get_order(order_id: str, user_id: str):
    if not user_id:
        return response(401, {"error": "Unauthorized", "message": "Authentication required"})
    res = orders_table.get_item(Key={"order_id": order_id})
    item = res.get("Item")
    if not item:
        return response(404, {"error": "NotFound", "message": "Order not found"})
    # TODO: allow restaurant owner / driver to read by role
    if item.get("user_id") != user_id:
        return response(403, {"error": "Forbidden", "message": "Cannot access this order"})
    return response(200, {"order": item})


def handle_update_status(order_id: str, user_id: str, body: dict):
    # TODO: authorize status transitions by role (restaurant, driver, admin)
    new_status = body.get("status")
    if not new_status:
        return response(400, {"error": "BadRequest", "message": "status is required"})
    res = orders_table.get_item(Key={"order_id": order_id})
    item = res.get("Item")
    if not item:
        return response(404, {"error": "NotFound", "message": "Order not found"})
    old_status = item.get("status")
    updated_at = _now_iso()
    try:
        out = orders_table.update_item(
            Key={"order_id": order_id},
            UpdateExpression="SET #s = :st, #u = :u",
            ExpressionAttributeNames={"#s": "status", "#u": "updated_at"},
            ExpressionAttributeValues={":st": new_status, ":u": updated_at},
            ConditionExpression="attribute_exists(order_id)",
            ReturnValues="ALL_NEW",
        )
        new_item = out.get("Attributes", {})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return response(404, {"error": "NotFound", "message": "Order not found"})
        raise

    detail = {
        "order_id": order_id,
        "old_status": old_status,
        "new_status": new_status,
        "user_id": item.get("user_id"),
        "restaurant_id": item.get("restaurant_id"),
        "updated_at": updated_at,
    }
    try:
        events.put_events(
            Entries=[
                {
                    "Source": "fooddelivery.orders",
                    "DetailType": "OrderStatusChanged",
                    "EventBusName": EVENT_BUS_NAME,
                    "Detail": json.dumps(detail, default=str),
                }
            ]
        )
    except ClientError:
        # TODO: outbox pattern — do not silently ignore in production
        raise

    return response(200, {"order": new_item})


def handle_validate_order(order_id: str):
    """Called by Step Functions to validate an order"""
    res = orders_table.get_item(Key={"order_id": order_id})
    order = res.get("Item")
    if not order:
        return {"valid": False, "error": "Order not found"}

    # Validate order has items
    if not order.get("items"):
        return {"valid": False, "error": "Order has no items"}

    # Calculate total
    total_amount = order.get("total", 0)

    return {
        "valid": True,
        "order_id": order_id,
        "restaurant_id": order.get("restaurant_id"),
        "total_amount": total_amount,
        "restaurant_location": {
            "address": "456 Restaurant Ave",  # TODO: fetch from restaurants table
            "lat": 37.7849,
            "lng": -122.4094,
        },
        "delivery_address": {
            "address": "123 Main St",  # TODO: get from user profile
            "lat": 37.7749,
            "lng": -122.4194,
        },
        "items": order.get("items", []),
    }


def handle_update_order_status(order_id: str, status: str, error: str = None):
    """Called by Step Functions to update order status"""
    try:
        update_expr = "SET #s = :st, #u = :u"
        expr_names = {"#s": "status", "#u": "updated_at"}
        expr_values = {":st": status, ":u": _now_iso()}

        if error:
            update_expr += ", #e = :e"
            expr_names["#e"] = "error_message"
            expr_values[":e"] = error

        out = orders_table.update_item(
            Key={"order_id": order_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ConditionExpression="attribute_exists(order_id)",
            ReturnValues="ALL_NEW",
        )
        return {"success": True, "order": out.get("Attributes", {})}
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {"success": False, "error": "Order not found"}
        raise


def lambda_handler(event, context):
    try:
        # Check if this is a Step Functions invocation (has 'action' in body)
        if "action" in event:
            action = event.get("action")
            if action == "validate":
                return handle_validate_order(event.get("order_id"))
            elif action == "update_status":
                return handle_update_order_status(
                    event.get("order_id"),
                    event.get("status"),
                    event.get("error")
                )

        # Otherwise, it's an HTTP API call
        http_method = event.get("httpMethod", "")
        path = (event.get("path") or "").rstrip("/") or "/"
        body = json.loads(event.get("body") or "{}")
        user_id = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("claims", {})
            .get("sub", "")
        )

        print(f"Order Service - {http_method} {path} - User: {user_id}")

        if http_method == "GET" and path == "/orders":
            return handle_list_orders(user_id)
        if http_method == "POST" and path == "/orders":
            return handle_create_order(user_id, body)

        m = re.match(r"^/orders/([^/]+)$", path)
        if m:
            oid = m.group(1)
            if http_method == "GET":
                return handle_get_order(oid, user_id)
            if http_method == "PUT":
                return handle_update_status(oid, user_id, body)

        return response(404, {"error": "NotFound", "message": "No route matched"})
    except json.JSONDecodeError as e:
        print(f"JSON Decode Error: {str(e)}")
        return response(400, {"error": "BadRequest", "message": "Invalid JSON body"})
    except ClientError as e:
        print(f"AWS Client Error: {str(e)}")
        return response(502, {"error": "AWSError", "message": str(e)})
    except Exception as e:
        print(f"Unexpected Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return response(500, {"error": "InternalError", "message": str(e)})
