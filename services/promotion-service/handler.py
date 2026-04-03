import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr

dynamodb = boto3.resource("dynamodb")

PROMOTIONS_TABLE = os.environ.get("PROMOTIONS_TABLE", "FoodDelivery-Promotions")
table = dynamodb.Table(PROMOTIONS_TABLE)


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_body(event):
    body = event.get("body") or "{}"
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return body if isinstance(body, dict) else {}


def _method_path(event):
    rc = event.get("requestContext") or {}
    if "http" in rc:
        return rc["http"]["method"], event.get("rawPath") or event.get("path", "")
    return event.get("httpMethod", "GET"), event.get("path", "")


def _path_segments(path):
    return [s for s in (path or "").rstrip("/").split("/") if s]


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def lambda_handler(event, context):
    try:
        method, path = _method_path(event)
        segs = _path_segments(path)
        try:
            pidx = segs.index("promotions")
        except ValueError:
            return response(404, {"error": "Not found", "path": path})

        if method == "GET" and pidx == len(segs) - 1:
            return _list_active_promotions()
        if method == "POST" and pidx == len(segs) - 1:
            return _create_promotion(event)
        if (
            method == "POST"
            and pidx < len(segs) - 1
            and segs[pidx + 1] == "validate"
        ):
            return _validate_promotion(event)

        return response(404, {"error": "Not found", "path": path, "method": method})
    except Exception as e:
        return response(500, {"error": str(e)})


def _list_active_promotions():
    try:
        scan = table.scan(
            FilterExpression=Attr("active").eq(True),
            Limit=100,
        )
        items = scan.get("Items", [])
        return response(200, {"promotions": items, "count": len(items)})
    except Exception as e:
        return response(500, {"error": str(e)})


def _create_promotion(event):
    data = _parse_body(event)
    code = (data.get("code") or "").strip().upper()
    if not code:
        code = str(uuid.uuid4())[:8].upper()
    discount = data.get("discount_percent")
    if discount is None:
        return response(400, {"error": "discount_percent is required"})
    try:
        discount_f = float(discount)
    except (TypeError, ValueError):
        return response(400, {"error": "discount_percent must be a number"})

    promotion_id = str(uuid.uuid4())
    now = _utc_now_iso()
    item = {
        "code": code,
        "promotion_id": promotion_id,
        "discount_percent": discount_f,
        "active": bool(data.get("active", True)),
        "title": data.get("title", ""),
        "expires_at": data.get("expires_at", ""),
        "created_at": now,
        "updated_at": now,
    }
    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(code)",
        )
        return response(201, item)
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return response(409, {"error": "Promotion code already exists"})
    except Exception as e:
        return response(500, {"error": str(e)})


def _validate_promotion(event):
    data = _parse_body(event)
    code = (data.get("code") or "").strip().upper()
    if not code:
        return response(400, {"error": "code is required"})
    try:
        res = table.get_item(Key={"code": code})
        item = res.get("Item")
        if not item:
            return response(200, {"valid": False, "reason": "not_found"})
        if not item.get("active", False):
            return response(200, {"valid": False, "reason": "inactive"})
        return response(
            200,
            {
                "valid": True,
                "code": code,
                "discount_percent": item.get("discount_percent"),
                "title": item.get("title"),
            },
        )
    except Exception as e:
        return response(500, {"error": str(e)})
