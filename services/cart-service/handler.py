import json
import os
import boto3
from botocore.exceptions import ClientError
dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("CARTS_TABLE_NAME", "FoodDelivery-Carts")
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


def _default_cart(user_id: str):
    return {"user_id": user_id, "items": []}


def handle_get_cart(user_id: str):
    if not user_id:
        return response(401, {"error": "Unauthorized", "message": "Missing authenticated user"})
    res = table.get_item(Key={"user_id": user_id})
    item = res.get("Item") or _default_cart(user_id)
    return response(200, {"cart": item})


def handle_post_add(user_id: str, body: dict):
    if not user_id:
        return response(401, {"error": "Unauthorized", "message": "Missing authenticated user"})
    restaurant_id = body.get("restaurant_id")
    menu_item_id = body.get("menu_item_id")
    qty = int(body.get("quantity", 1))
    if not restaurant_id or not menu_item_id or qty < 1:
        return response(
            400,
            {"error": "BadRequest", "message": "restaurant_id, menu_item_id, and positive quantity required"},
        )
    line = {
        "line_id": body.get("line_id") or f"{restaurant_id}#{menu_item_id}",
        "restaurant_id": restaurant_id,
        "menu_item_id": menu_item_id,
        "quantity": qty,
        "name": body.get("name") or "",
        "unit_price_cents": int(body.get("unit_price_cents", 0)),
    }
    try:
        table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET #items = list_append(if_not_exists(#items, :empty), :new_line)",
            ExpressionAttributeNames={"#items": "items"},
            ExpressionAttributeValues={
                ":empty": [],
                ":new_line": [line],
            },
        )
    except ClientError:
        raise
    res = table.get_item(Key={"user_id": user_id})
    return response(200, {"cart": res.get("Item", _default_cart(user_id))})


def handle_put_quantity(user_id: str, body: dict):
    if not user_id:
        return response(401, {"error": "Unauthorized", "message": "Missing authenticated user"})
    line_id = body.get("line_id")
    qty = body.get("quantity")
    if not line_id or qty is None:
        return response(400, {"error": "BadRequest", "message": "line_id and quantity required"})
    qty = int(qty)
    res = table.get_item(Key={"user_id": user_id})
    cart = res.get("Item") or _default_cart(user_id)
    items = list(cart.get("items") or [])
    found = False
    for i, line in enumerate(items):
        if line.get("line_id") == line_id:
            if qty <= 0:
                items.pop(i)
            else:
                items[i] = {**line, "quantity": qty}
            found = True
            break
    if not found and qty > 0:
        return response(404, {"error": "NotFound", "message": "Cart line not found"})
    table.put_item(Item={"user_id": user_id, "items": items})
    return response(200, {"cart": {"user_id": user_id, "items": items}})


def handle_delete_cart(user_id: str, query_params: dict):
    if not user_id:
        return response(401, {"error": "Unauthorized", "message": "Missing authenticated user"})
    line_id = (query_params.get("line_id") or "").strip()
    if line_id:
        res = table.get_item(Key={"user_id": user_id})
        cart = res.get("Item") or _default_cart(user_id)
        items = [x for x in (cart.get("items") or []) if x.get("line_id") != line_id]
        table.put_item(Item={"user_id": user_id, "items": items})
        return response(200, {"cart": {"user_id": user_id, "items": items}})
    table.put_item(Item={"user_id": user_id, "items": []})
    return response(200, {"cart": {"user_id": user_id, "items": []}})


def lambda_handler(event, context):
    try:
        http_method = event.get("httpMethod", "")
        path = (event.get("path") or "").rstrip("/") or "/"
        query_params = event.get("queryStringParameters") or {}
        body = json.loads(event.get("body") or "{}")
        user_id = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("claims", {})
            .get("sub", "")
        )

        if path != "/cart":
            return response(404, {"error": "NotFound", "message": "No route matched"})

        if http_method == "GET":
            return handle_get_cart(user_id)
        if http_method == "POST":
            return handle_post_add(user_id, body)
        if http_method == "PUT":
            return handle_put_quantity(user_id, body)
        if http_method == "DELETE":
            return handle_delete_cart(user_id, query_params)

        return response(405, {"error": "MethodNotAllowed", "message": http_method})
    except json.JSONDecodeError:
        return response(400, {"error": "BadRequest", "message": "Invalid JSON body"})
    except ClientError as e:
        return response(502, {"error": "DynamoDBError", "message": str(e)})
    except Exception as e:
        return response(500, {"error": "InternalError", "message": str(e)})
