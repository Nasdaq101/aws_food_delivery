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
        # Check if this is a Step Functions invocation
        if "action" in event:
            action = event.get("action")
            if action == "find_available":
                return _find_available_drivers(event)
            else:
                return {"error": "Unknown action", "action": action}

        # Otherwise, handle HTTP API calls
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

    # Check if driver exists first
    try:
        res = table.get_item(Key={"driver_id": driver_id})
        driver_exists = "Item" in res
    except Exception as e:
        return response(500, {"error": f"Failed to check driver existence: {str(e)}"})

    # If driver doesn't exist, create it (upsert pattern)
    if not driver_exists:
        now = _utc_now_iso()
        item = {
            "driver_id": driver_id,
            "name": data.get("name", ""),
            "phone": data.get("phone", ""),
            "status": data.get("status", "available"),
            "location": data.get("location") or {},
            "vehicle_type": data.get("vehicle_type", ""),
            "license_plate": data.get("license_plate", ""),
            "license_number": data.get("license_number", ""),
            "created_at": now,
            "updated_at": now,
        }
        try:
            table.put_item(Item=item)
            return response(200, item)
        except Exception as e:
            return response(500, {"error": f"Failed to create driver: {str(e)}"})

    # Driver exists, update it
    expr_parts = []
    names = {}
    values = {}
    idx = 0
    # Extended list of allowed fields for drivers
    for key in ("status", "location", "name", "phone", "vehicle_type", "license_plate", "license_number"):
        if key not in data:
            continue
        nk = f"#k{idx}"
        vk = f":v{idx}"
        names[nk] = key
        values[vk] = data[key]
        expr_parts.append(f"{nk} = {vk}")
        idx += 1
    if not expr_parts:
        return response(400, {"error": "No updatable fields provided"})
    names["#ua"] = "updated_at"
    values[":ua"] = _utc_now_iso()
    expr_parts.append("#ua = :ua")
    try:
        table.update_item(
            Key={"driver_id": driver_id},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        res = table.get_item(Key={"driver_id": driver_id})
        return response(200, res.get("Item", {}))
    except Exception as e:
        return response(500, {"error": str(e)})


def _find_available_drivers(event):
    """Called by Step Functions to find available drivers near a location"""
    restaurant_location = event.get("restaurant_location") or {}

    # Check for preferred driver (for testing/development)
    preferred_driver_id = os.environ.get("PREFERRED_DRIVER_ID")

    try:
        # If a preferred driver is set, try to find them first
        if preferred_driver_id:
            try:
                driver_result = table.get_item(Key={"driver_id": preferred_driver_id})
                preferred_driver = driver_result.get("Item")

                if preferred_driver and preferred_driver.get("status") == "available":
                    print(f"Using preferred driver: {preferred_driver_id}")
                    return {
                        "available": True,
                        "driver_count": 1,
                        "drivers": [preferred_driver],
                        "best_driver": {
                            "driver_id": preferred_driver.get("driver_id"),
                            "name": preferred_driver.get("name"),
                            "vehicle_type": preferred_driver.get("vehicle_type", ""),
                            "rating": float(preferred_driver.get("rating", 4.5)),
                            "eta": 15
                        }
                    }
            except Exception as e:
                print(f"Error fetching preferred driver: {str(e)}")
                # Fall through to normal driver search

        # Scan for available drivers
        scan_result = table.scan(
            FilterExpression="#s = :status",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": "available"},
            Limit=20
        )

        drivers = scan_result.get("Items", [])

        # In a real application, you would:
        # 1. Filter drivers by proximity to restaurant_location
        # 2. Sort by distance or rating
        # 3. Consider driver capacity and current load

        if not drivers:
            return {
                "available": False,
                "driver_count": 0,
                "drivers": []
            }

        # Select the best driver (for now, just pick the first one)
        # In production, this would be based on proximity, rating, etc.
        best_driver = drivers[0]

        return {
            "available": True,
            "driver_count": len(drivers),
            "drivers": drivers[:10],  # Return top 10 drivers
            "best_driver": {
                "driver_id": best_driver.get("driver_id"),
                "name": best_driver.get("name"),
                "vehicle_type": best_driver.get("vehicle_type", ""),
                "rating": float(best_driver.get("rating", 4.5)),
                "eta": 15  # Estimated time in minutes (placeholder)
            }
        }
    except Exception as e:
        print(f"Error finding drivers: {str(e)}")
        return {"error": f"Failed to find drivers: {str(e)}", "available": False}
