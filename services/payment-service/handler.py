import json
import os
import uuid
from datetime import datetime, timezone

import boto3

dynamodb = boto3.resource("dynamodb")
events_client = boto3.client("events")

PAYMENTS_TABLE = os.environ.get("PAYMENTS_TABLE", "FoodDelivery-Payments")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")

table = dynamodb.Table(PAYMENTS_TABLE)


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


def _emit_payment_event(detail_type, detail):
    try:
        events_client.put_events(
            Entries=[
                {
                    "Source": "fooddelivery.payment",
                    "DetailType": detail_type,
                    "Detail": json.dumps(detail, default=str),
                    "EventBusName": EVENT_BUS_NAME,
                }
            ]
        )
    except Exception:
        # event publish failure should not mask payment record state; log via raise in strict mode
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
        method, path = _method_path(event)
        segs = _path_segments(path)
        try:
            pidx = segs.index("payments")
        except ValueError:
            return response(404, {"error": "Not found", "path": path, "method": method})

        if method == "POST" and pidx == len(segs) - 1:
            return _post_payment(event)
        if method == "GET" and pidx < len(segs) - 1:
            return _get_payment(segs[pidx + 1])

        return response(404, {"error": "Not found", "path": path, "method": method})
    except Exception as e:
        return response(500, {"error": str(e)})


def _post_payment(event):
    data = _parse_body(event)
    order_id = data.get("order_id")
    amount = data.get("amount")
    if not order_id or amount is None:
        return response(400, {"error": "order_id and amount are required"})

    try:
        amount_f = float(amount)
    except (TypeError, ValueError):
        return response(400, {"error": "amount must be a number"})

    payment_id = str(uuid.uuid4())
    now = _utc_now_iso()
    item = {
        "payment_id": payment_id,
        "order_id": order_id,
        "amount": amount_f,
        "currency": data.get("currency", "USD"),
        "status": "succeeded",
        "created_at": now,
        "updated_at": now,
    }

    try:
        table.put_item(Item=item)
        _emit_payment_event(
            "PaymentSucceeded",
            {"payment_id": payment_id, "order_id": order_id, "amount": amount_f},
        )
        return response(201, item)
    except Exception as e:
        fail_id = str(uuid.uuid4())
        fail_item = {
            "payment_id": fail_id,
            "order_id": order_id,
            "amount": amount_f,
            "currency": data.get("currency", "USD"),
            "status": "failed",
            "error": str(e),
            "created_at": now,
            "updated_at": now,
        }
        try:
            table.put_item(Item=fail_item)
        except Exception:
            pass
        _emit_payment_event(
            "PaymentFailed",
            {"payment_id": fail_id, "order_id": order_id, "reason": str(e)},
        )
        return response(502, {"error": "Payment processing failed", "detail": str(e)})


def _get_payment(payment_id):
    try:
        res = table.get_item(Key={"payment_id": payment_id})
        item = res.get("Item")
        if not item:
            return response(404, {"error": "Payment not found"})
        return response(200, item)
    except Exception as e:
        return response(500, {"error": str(e)})
