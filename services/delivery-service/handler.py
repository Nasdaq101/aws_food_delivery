import json
import os
import uuid
from datetime import datetime, timezone

import boto3

dynamodb = boto3.resource("dynamodb")
events_client = boto3.client("events")

DELIVERIES_TABLE = os.environ.get("DELIVERIES_TABLE", "FoodDelivery-Deliveries")
DRIVERS_TABLE = os.environ.get("DRIVERS_TABLE", "FoodDelivery-Drivers")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")

deliveries_table = dynamodb.Table(DELIVERIES_TABLE)
drivers_table = dynamodb.Table(DRIVERS_TABLE)


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _method_path(event):
    rc = event.get("requestContext") or {}
    if "http" in rc:
        return rc["http"]["method"], event.get("rawPath") or event.get("path", "")
    return event.get("httpMethod", "GET"), event.get("path", "")


def _path_segments(path):
    return [s for s in (path or "").rstrip("/").split("/") if s]


def _emit_delivery_event(detail_type, detail):
    try:
        events_client.put_events(
            Entries=[
                {
                    "Source": "fooddelivery.delivery",
                    "DetailType": detail_type,
                    "Detail": json.dumps(detail, default=str),
                    "EventBusName": EVENT_BUS_NAME,
                }
            ]
        )
    except Exception:
        pass


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
            if action == "create":
                return _create_delivery(event)
            elif action == "assign":
                return _assign_driver(event)
            else:
                return {"error": "Unknown action", "action": action}

        # Otherwise, handle HTTP API calls
        method, path = _method_path(event)
        if method != "GET":
            return response(405, {"error": "Method not allowed"})

        segs = _path_segments(path)
        try:
            idx = segs.index("deliveries")
        except ValueError:
            return response(404, {"error": "Not found", "path": path})

        if idx == len(segs) - 1:
            return _list_deliveries()
        if idx == len(segs) - 2:
            return _get_delivery(segs[idx + 1], event)

        return response(404, {"error": "Not found", "path": path})
    except Exception as e:
        return response(500, {"error": str(e)})


def _list_deliveries():
    try:
        scan = deliveries_table.scan(Limit=50)
        items = scan.get("Items", [])
        return response(200, {"deliveries": items, "count": len(items)})
    except Exception as e:
        return response(500, {"error": str(e)})


def _ensure_driver_assigned(item, event):
    """If no driver, pick first available driver and update delivery."""
    if item.get("driver_id"):
        return item
    try:
        dscan = drivers_table.scan(
            FilterExpression="#s = :avail",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":avail": "available"},
            Limit=10,
        )
        drivers = dscan.get("Items") or []
        if not drivers:
            return item
        driver = drivers[0]
        driver_id = driver.get("driver_id")
        if not driver_id:
            return item
        now = _utc_now_iso()
        deliveries_table.update_item(
            Key={"delivery_id": item["delivery_id"]},
            UpdateExpression="SET driver_id = :d, #st = :st, updated_at = :u",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":d": driver_id,
                ":st": "assigned",
                ":u": now,
            },
            ReturnValues="ALL_NEW",
        )
        drivers_table.update_item(
            Key={"driver_id": driver_id},
            UpdateExpression="SET #s = :busy, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":busy": "busy", ":u": now},
        )
        _emit_delivery_event(
            "DriverAssigned",
            {"delivery_id": item["delivery_id"], "driver_id": driver_id},
        )
        res = deliveries_table.get_item(Key={"delivery_id": item["delivery_id"]})
        return res.get("Item", item)
    except Exception:
        return item


def _get_delivery(delivery_id, event):
    try:
        res = deliveries_table.get_item(Key={"delivery_id": delivery_id})
        item = res.get("Item")
        if not item:
            return response(404, {"error": "Delivery not found"})
        item = _ensure_driver_assigned(item, event)
        return response(200, item)
    except Exception as e:
        return response(500, {"error": str(e)})


def _create_delivery(event):
    """Called by Step Functions to create a delivery record"""
    order_id = event.get("order_id")
    restaurant_id = event.get("restaurant_id")
    customer_address = event.get("customer_address")

    if not order_id or not restaurant_id:
        return {"error": "order_id and restaurant_id are required"}

    delivery_id = str(uuid.uuid4())
    now = _utc_now_iso()

    item = {
        "delivery_id": delivery_id,
        "order_id": order_id,
        "restaurant_id": restaurant_id,
        "customer_address": customer_address or "Unknown address",
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }

    try:
        deliveries_table.put_item(Item=item)
        return {
            "success": True,
            "delivery_id": delivery_id,
            "status": "pending"
        }
    except Exception as e:
        return {"error": f"Failed to create delivery: {str(e)}"}


def _assign_driver(event):
    """Called by Step Functions to assign a driver to a delivery"""
    delivery_id = event.get("delivery_id")
    drivers = event.get("drivers", [])

    if not delivery_id:
        return {"error": "delivery_id is required"}

    if not drivers or len(drivers) == 0:
        return {"error": "No available drivers", "assigned": False}

    # Pick the first available driver
    driver = drivers[0]
    driver_id = driver.get("driver_id")

    if not driver_id:
        return {"error": "Invalid driver data", "assigned": False}

    now = _utc_now_iso()

    try:
        # Update delivery with driver assignment
        deliveries_table.update_item(
            Key={"delivery_id": delivery_id},
            UpdateExpression="SET driver_id = :d, #st = :st, updated_at = :u",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":d": driver_id,
                ":st": "assigned",
                ":u": now,
            },
        )

        # Update driver status to busy
        drivers_table.update_item(
            Key={"driver_id": driver_id},
            UpdateExpression="SET #s = :busy, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":busy": "busy", ":u": now},
        )

        _emit_delivery_event(
            "DriverAssigned",
            {
                "delivery_id": delivery_id,
                "driver_id": driver_id,
                "driver_name": driver.get("name")
            }
        )

        return {
            "success": True,
            "assigned": True,
            "driver_id": driver_id,
            "driver_name": driver.get("name")
        }
    except Exception as e:
        return {"error": f"Failed to assign driver: {str(e)}", "assigned": False}
