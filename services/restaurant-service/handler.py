import json
import os
import re
import uuid
import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

#  three todos are left, you can decide to implement it or not.

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("RESTAURANTS_TABLE_NAME", "FoodDelivery-Restaurants")
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


def handle_list(query_params: dict):
    city = (query_params.get("city") or "").strip()
    cuisine = (query_params.get("cuisine") or "").strip()
    try:
        if city:
            # GSI: partition city, sort avg_rating
            q = table.query(
                IndexName="city-rating-index",
                KeyConditionExpression=Key("city").eq(city),
                ScanIndexForward=False,
            )
            items = q.get("Items", [])
            if cuisine:
                items = [i for i in items if (i.get("cuisine") or "").lower() == cuisine.lower()]
            return response(200, {"restaurants": items})
        # TODO: add cuisine GSI if filtering by cuisine without city becomes hot path
        scan_kwargs = {}
        fe_parts = []
        expr_vals = {}
        if cuisine:
            fe_parts.append("contains(#cuisine, :cuisine)")
            expr_vals[":cuisine"] = cuisine
        if fe_parts:
            scan_kwargs["FilterExpression"] = " AND ".join(fe_parts)
            scan_kwargs["ExpressionAttributeNames"] = {"#cuisine": "cuisine"}
            scan_kwargs["ExpressionAttributeValues"] = expr_vals
        res = table.scan(**scan_kwargs)
        items = res.get("Items", [])
        return response(200, {"restaurants": items})
    except ClientError as e:
        raise


def handle_create(body: dict, owner_sub: str):
    # TODO: authorize restaurant_owner role, validate address/geo, etc.
    name = body.get("name")
    if not name:
        return response(400, {"error": "BadRequest", "message": "name is required"})
    rid = str(uuid.uuid4())
    item = {
        "restaurant_id": rid,
        "name": name,
        "city": body.get("city") or "",
        "cuisine": body.get("cuisine") or "",
        "avg_rating": float(body.get("avg_rating", 0)),
        "owner_id": body.get("owner_id") or owner_sub,
        "description": body.get("description") or "",
        "address": body.get("address") or "",
    }
    table.put_item(Item=item)
    return response(201, {"restaurant": item})


def handle_get(restaurant_id: str):
    res = table.get_item(Key={"restaurant_id": restaurant_id})
    item = res.get("Item")
    if not item:
        return response(404, {"error": "NotFound", "message": "Restaurant not found"})
    return response(200, {"restaurant": item})


def handle_update(restaurant_id: str, body: dict, requester_sub: str):
    # TODO: verify requester owns restaurant or is admin
    allowed = {"name", "city", "cuisine", "description", "address", "avg_rating"}
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
            Key={"restaurant_id": restaurant_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_vals,
            ConditionExpression="attribute_exists(restaurant_id)",
            ReturnValues="ALL_NEW",
        )
        return response(200, {"restaurant": out.get("Attributes", {})})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return response(404, {"error": "NotFound", "message": "Restaurant not found"})
        raise


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

        if http_method == "GET" and path == "/restaurants":
            return handle_list(query_params)
        if http_method == "POST" and path == "/restaurants":
            return handle_create(body, user_id)

        m = re.match(r"^/restaurants/([^/]+)$", path)
        if m:
            rid = m.group(1)
            if http_method == "GET":
                return handle_get(rid)
            if http_method == "PUT":
                return handle_update(rid, body, user_id)

        return response(404, {"error": "NotFound", "message": "No route matched"})
    except json.JSONDecodeError:
        return response(400, {"error": "BadRequest", "message": "Invalid JSON body"})
    except ClientError as e:
        return response(502, {"error": "DynamoDBError", "message": str(e)})
    except Exception as e:
        return response(500, {"error": "InternalError", "message": str(e)})
