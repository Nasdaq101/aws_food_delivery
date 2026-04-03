import json
import os
import re
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

# todos are left, you can decide to implement it or not.

dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")
sqs = boto3.client("sqs")

ORDERS_TABLE = os.environ.get("ORDERS_TABLE_NAME", "FoodDelivery-Orders")
CARTS_TABLE = os.environ.get("CARTS_TABLE_NAME", "FoodDelivery-Carts")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "FoodDeliveryEventBus")
ORDER_QUEUE_URL = os.environ.get("ORDER_QUEUE_URL", "")

orders_table = dynamodb.Table(ORDERS_TABLE)
carts_table = dynamodb.Table(CARTS_TABLE)


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


def handle_list_orders(user_id: str):
    if not user_id:
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
        return response(200, {"orders": items})
    except ClientError:
        raise


def handle_create_order(user_id: str, body: dict):
    if not user_id:
        return response(401, {"error": "Unauthorized", "message": "Missing authenticated user"})
    cart_res = carts_table.get_item(Key={"user_id": user_id})
    cart = cart_res.get("Item") or {"user_id": user_id, "items": []}
    lines = cart.get("items") or []
    if not lines:
        return response(400, {"error": "BadRequest", "message": "Cart is empty"})
    # TODO: enforce single-restaurant carts, re-fetch menu prices, payment pre-auth
    restaurant_id = body.get("restaurant_id") or lines[0].get("restaurant_id")
    if not restaurant_id:
        return response(400, {"error": "BadRequest", "message": "Unable to determine restaurant_id"})
    order_id = str(uuid.uuid4())
    created_at = _now_iso()
    order = {
        "order_id": order_id,
        "user_id": user_id,
        "restaurant_id": restaurant_id,
        "items": lines,
        "status": "PLACED",
        "created_at": created_at,
        "updated_at": created_at,
        "notes": body.get("notes") or "",
    }
    orders_table.put_item(Item=order)
    carts_table.put_item(Item={"user_id": user_id, "items": []})

    if ORDER_QUEUE_URL:
        try:
            sqs.send_message(
                QueueUrl=ORDER_QUEUE_URL,
                MessageBody=json.dumps({"order_id": order_id, "event": "ORDER_PLACED"}),
            )
        except ClientError:
            # TODO: dead-letter / retry policy — order already persisted
            raise

    # TODO: emit ORDER_PLACED on EventBridge if downstream needs it
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
                    "Detail": json.dumps(detail),
                }
            ]
        )
    except ClientError:
        # TODO: outbox pattern — do not silently ignore in production
        raise

    return response(200, {"order": new_item})


def lambda_handler(event, context):
    try:
        http_method = event.get("httpMethod", "")
        path = (event.get("path") or "").rstrip("/") or "/"
        body = json.loads(event.get("body") or "{}")
        user_id = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("claims", {})
            .get("sub", "")
        )

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
    except json.JSONDecodeError:
        return response(400, {"error": "BadRequest", "message": "Invalid JSON body"})
    except ClientError as e:
        return response(502, {"error": "AWSError", "message": str(e)})
    except Exception as e:
        return response(500, {"error": "InternalError", "message": str(e)})
