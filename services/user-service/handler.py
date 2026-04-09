import json
import os
import re
import boto3
from botocore.exceptions import ClientError

#  i left admin and validation part, you can decide to implement it or not.

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("USERS_TABLE_NAME", "FoodDelivery-Users")
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


def _get_user(user_id: str):
    res = table.get_item(Key={"user_id": user_id})
    return res.get("Item")


def handle_get_me(sub: str):
    if not sub:
        return response(401, {"error": "Unauthorized", "message": "Missing authenticated user"})
    item = _get_user(sub)
    if not item:
        return response(404, {"error": "NotFound", "message": "User profile not found"})
    return response(200, {"user": item})


def handle_get_user(target_id: str, requester_sub: str):
    if not requester_sub:
        return response(401, {"error": "Unauthorized", "message": "Authentication required"})
    # TODO: allow admin/service roles to read arbitrary users
    if requester_sub != target_id:
        return response(403, {"error": "Forbidden", "message": "Cannot access another user's profile"})
    item = _get_user(target_id)
    if not item:
        return response(404, {"error": "NotFound", "message": "User not found"})
    return response(200, {"user": item})


def handle_put_user(target_id: str, requester_sub: str, body: dict):
    if not requester_sub or requester_sub != target_id:
        return response(403, {"error": "Forbidden", "message": "Cannot update another user's profile"})
    # TODO: validate schema (email format, allowed fields, etc.)
    allowed = {"email", "full_name", "phone", "address", "preferences", "role", "location"}
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
        res = table.update_item(
            Key={"user_id": target_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_vals,
            ConditionExpression="attribute_exists(user_id)",
            ReturnValues="ALL_NEW",
        )
        return response(200, {"user": res.get("Attributes", {})})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return response(404, {"error": "NotFound", "message": "User not found"})
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

        if http_method == "GET" and path == "/users/me":
            return handle_get_me(user_id)

        m = re.match(r"^/users/([^/]+)$", path)
        if m:
            uid = m.group(1)
            if uid == "me":
                return handle_get_me(user_id)
            if http_method == "GET":
                return handle_get_user(uid, user_id)
            if http_method == "PUT":
                return handle_put_user(uid, user_id, body)

        return response(404, {"error": "NotFound", "message": "No route matched"})
    except json.JSONDecodeError:
        return response(400, {"error": "BadRequest", "message": "Invalid JSON body"})
    except ClientError as e:
        return response(502, {"error": "DynamoDBError", "message": str(e)})
    except Exception as e:
        return response(500, {"error": "InternalError", "message": str(e)})
