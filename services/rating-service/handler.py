import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")

RATINGS_TABLE = os.environ.get("RATINGS_TABLE", "FoodDelivery-Ratings")
table = dynamodb.Table(RATINGS_TABLE)


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
            ridx = segs.index("ratings")
        except ValueError:
            return response(404, {"error": "Not found", "path": path})

        if method == "POST" and ridx == len(segs) - 1:
            return _submit_rating(event)
        if method == "GET" and ridx < len(segs) - 1:
            return _get_ratings_for_target(segs[ridx + 1])

        return response(404, {"error": "Not found", "path": path, "method": method})
    except Exception as e:
        return response(500, {"error": str(e)})


def _submit_rating(event):
    data = _parse_body(event)
    target_id = data.get("target_id")
    target_type = data.get("target_type", "restaurant")
    stars = data.get("stars")
    if not target_id or stars is None:
        return response(400, {"error": "target_id and stars are required"})
    try:
        stars_i = int(stars)
        if stars_i < 1 or stars_i > 5:
            raise ValueError("stars out of range")
    except (TypeError, ValueError) as e:
        return response(400, {"error": f"stars must be integer 1-5: {e}"})

    rating_id = str(uuid.uuid4())
    now = _utc_now_iso()
    item = {
        "target_id": target_id,
        "rating_id": rating_id,
        "target_type": target_type,
        "stars": stars_i,
        "comment": data.get("comment", ""),
        "order_id": data.get("order_id", ""),
        "created_at": now,
    }
    try:
        table.put_item(Item=item)
        return response(201, item)
    except Exception as e:
        return response(500, {"error": str(e)})


def _get_ratings_for_target(target_id):
    try:
        q = table.query(KeyConditionExpression=Key("target_id").eq(target_id))
        items = q.get("Items", [])
        return response(200, {"target_id": target_id, "ratings": items, "count": len(items)})
    except Exception as e:
        return response(500, {"error": str(e)})
