import json
import os
import uuid
from datetime import datetime, timezone

import boto3

dynamodb = boto3.resource("dynamodb")

DRIVERS_TABLE = os.environ.get("DRIVERS_TABLE", "FoodDelivery-Drivers")
table = dynamodb.Table(DRIVERS_TABLE)


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
            didx = segs.index("drivers")
        except ValueError:
            return response(404, {"error": "Not found", "path": path})

        if method == "POST" and didx == len(segs) - 1:
            return _register_driver(event)
        if didx < len(segs) - 1:
            driver_id = segs[didx + 1]
            if method == "GET":
                return _get_driver(driver_id)
            if method == "PUT":
                return _update_driver(driver_id, event)

        return response(404, {"error": "Not found", "path": path, "method": method})
    except Exception as e:
        return response(500, {"error": str(e)})


def _register_driver(event):
    data = _parse_body(event)
    name = data.get("name")
    if not name:
        return response(400, {"error": "name is required"})
    driver_id = str(uuid.uuid4())
    now = _utc_now_iso()
    item = {
        "driver_id": driver_id,
        "name": name,
        "phone": data.get("phone", ""),
        "status": data.get("status", "available"),
        "location": data.get("location") or {},
        "created_at": now,
        "updated_at": now,
    }
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(driver_id)")
        return response(201, item)
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return response(409, {"error": "Driver already exists"})
    except Exception as e:
        return response(500, {"error": str(e)})


def _get_driver(driver_id):
    try:
        res = table.get_item(Key={"driver_id": driver_id})
        item = res.get("Item")
        if not item:
            return response(404, {"error": "Driver not found"})
        return response(200, item)
    except Exception as e:
        return response(500, {"error": str(e)})


def _update_driver(driver_id, event):
    data = _parse_body(event)
    if not data:
        return response(400, {"error": "JSON body required"})
    expr_parts = []
    names = {}
    values = {}
    idx = 0
    for key in ("status", "location", "name", "phone"):
        if key not in data:
            continue
        nk = f"#k{idx}"
        vk = f":v{idx}"
        names[nk] = key
        values[vk] = data[key]
        expr_parts.append(f"{nk} = {vk}")
        idx += 1
    if not expr_parts:
        return response(400, {"error": "No updatable fields (status, location, name, phone)"})
    names["#ua"] = "updated_at"
    values[":ua"] = _utc_now_iso()
    expr_parts.append("#ua = :ua")
    try:
        table.update_item(
            Key={"driver_id": driver_id},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(driver_id)",
            ReturnValues="ALL_NEW",
        )
        res = table.get_item(Key={"driver_id": driver_id})
        return response(200, res.get("Item", {}))
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return response(404, {"error": "Driver not found"})
    except Exception as e:
        return response(500, {"error": str(e)})
