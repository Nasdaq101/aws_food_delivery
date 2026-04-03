import json
import os
import re
import uuid
import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

# ownership verification is left, you can decide to implement it or not.

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("MENUS_TABLE_NAME", "FoodDelivery-Menus")
table = dynamodb.Table(TABLE_NAME)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def handle_get_menu(restaurant_id: str):
    try:
        res = table.query(KeyConditionExpression=Key("restaurant_id").eq(restaurant_id))
        items = res.get("Items", [])
        while "LastEvaluatedKey" in res:
            res = table.query(
                KeyConditionExpression=Key("restaurant_id").eq(restaurant_id),
                ExclusiveStartKey=res["LastEvaluatedKey"],
            )
            items.extend(res.get("Items", []))
        return response(200, {"restaurant_id": restaurant_id, "items": items})
    except ClientError:
        raise


def handle_add_item(restaurant_id: str, body: dict, requester_sub: str):
    # TODO: verify requester owns restaurant
    name = body.get("name")
    if not name:
        return response(400, {"error": "BadRequest", "message": "name is required"})
    item_id = body.get("item_id") or str(uuid.uuid4())
    item = {
        "restaurant_id": restaurant_id,
        "item_id": item_id,
        "name": name,
        "description": body.get("description") or "",
        "price_cents": int(body.get("price_cents", 0)),
        "category": body.get("category") or "",
        "available": body.get("available", True),
        "updated_by": requester_sub,
    }
    table.put_item(Item=item)
    return response(201, {"item": item})


def handle_update_item(restaurant_id: str, item_id: str, body: dict, requester_sub: str):
    # TODO: verify ownership
    allowed = {"name", "description", "price_cents", "category", "available"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        return response(400, {"error": "BadRequest", "message": "No valid fields to update"})
    expr_names = {}
    expr_vals = {}
    set_parts = []
    for i, (k, v) in enumerate(updates.items()):
        nk = f"#f{i}"
        vk = f":v{i}"
        expr_names[nk] = k
        expr_vals[vk] = v
        set_parts.append(f"{nk} = {vk}")
    update_expression = "SET " + ", ".join(set_parts)
    try:
        out = table.update_item(
            Key={"restaurant_id": restaurant_id, "item_id": item_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_vals,
            ConditionExpression="attribute_exists(item_id)",
            ReturnValues="ALL_NEW",
        )
        return response(200, {"item": out.get("Attributes", {})})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return response(404, {"error": "NotFound", "message": "Menu item not found"})
        raise


def handle_delete_item(restaurant_id: str, item_id: str):
    # TODO: verify ownership
    try:
        table.delete_item(
            Key={"restaurant_id": restaurant_id, "item_id": item_id},
            ConditionExpression="attribute_exists(item_id)",
        )
        return response(204, {})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return response(404, {"error": "NotFound", "message": "Menu item not found"})
        raise


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

        m_menu = re.match(r"^/restaurants/([^/]+)/menu$", path)
        if m_menu:
            restaurant_id = m_menu.group(1)
            if http_method == "GET":
                return handle_get_menu(restaurant_id)
            if http_method == "POST":
                return handle_add_item(restaurant_id, body, user_id)

        m_item = re.match(r"^/restaurants/([^/]+)/menu/([^/]+)$", path)
        if m_item:
            restaurant_id, item_id = m_item.group(1), m_item.group(2)
            if http_method == "PUT":
                return handle_update_item(restaurant_id, item_id, body, user_id)
            if http_method == "DELETE":
                return handle_delete_item(restaurant_id, item_id)

        return response(404, {"error": "NotFound", "message": "No route matched"})
    except json.JSONDecodeError:
        return response(400, {"error": "BadRequest", "message": "Invalid JSON body"})
    except ClientError as e:
        return response(502, {"error": "DynamoDBError", "message": str(e)})
    except Exception as e:
        return response(500, {"error": "InternalError", "message": str(e)})
