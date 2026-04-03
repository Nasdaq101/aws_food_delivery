import base64
import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

# websocket api handler for real-time tracking of the delivery, you can finish websocket based on this code.

dynamodb = boto3.resource("dynamodb")

TRACKING_TABLE = os.environ.get("TRACKING_TABLE", "FoodDelivery-TrackingConnections")
table = dynamodb.Table(TRACKING_TABLE)


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_body(event):
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded") and isinstance(body, str):
        try:
            body = base64.b64decode(body).decode("utf-8")
        except Exception:
            body = "{}"
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return body if isinstance(body, dict) else {}


def _mgmt_client(event):
    rc = event.get("requestContext") or {}
    domain = rc.get("domainName")
    stage = rc.get("stage", "prod")
    region = os.environ.get("AWS_REGION", "us-east-1")
    if not domain:
        raise ValueError("missing domainName in requestContext")
    endpoint = f"https://{domain}/{stage}"
    return boto3.client(
        "apigatewaymanagementapi",
        endpoint_url=endpoint,
        region_name=region,
    )


def ws_response(status_code, body_dict=None):
    out = {"statusCode": status_code}
    if body_dict is not None:
        out["body"] = json.dumps(body_dict, default=str)
    return out


def lambda_handler(event, context):
    try:
        rc = event.get("requestContext") or {}
        route = rc.get("routeKey", "")
        connection_id = rc.get("connectionId")

        if route == "$connect":
            return _on_connect(connection_id)
        if route == "$disconnect":
            return _on_disconnect(connection_id)
        if route == "subscribe":
            return _on_subscribe(connection_id, event)
        if route == "sendLocation":
            return _on_send_location(connection_id, event)

        return ws_response(400, {"error": "Unknown route", "routeKey": route})
    except Exception as e:
        return ws_response(500, {"error": str(e)})


def _on_connect(connection_id):
    if not connection_id:
        return ws_response(400, {"error": "missing connectionId"})
    now = _utc_now_iso()
    try:
        table.put_item(
            Item={
                "connection_id": connection_id,
                "subscribed_delivery_id": None,
                "connected_at": now,
                "updated_at": now,
            }
        )
        return ws_response(200, {"ok": True})
    except Exception as e:
        return ws_response(500, {"error": str(e)})


def _on_disconnect(connection_id):
    if not connection_id:
        return ws_response(200, {"ok": True})
    try:
        table.delete_item(Key={"connection_id": connection_id})
        return ws_response(200, {"ok": True})
    except Exception as e:
        return ws_response(500, {"error": str(e)})


def _on_subscribe(connection_id, event):
    if not connection_id:
        return ws_response(400, {"error": "missing connectionId"})
    data = _parse_body(event)
    delivery_id = data.get("delivery_id")
    if not delivery_id:
        return ws_response(400, {"error": "delivery_id required"})
    now = _utc_now_iso()
    try:
        table.update_item(
            Key={"connection_id": connection_id},
            UpdateExpression="SET subscribed_delivery_id = :d, updated_at = :u",
            ExpressionAttributeValues={":d": delivery_id, ":u": now},
            ConditionExpression="attribute_exists(connection_id)",
        )
        return ws_response(200, {"ok": True, "subscribed_delivery_id": delivery_id})
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return ws_response(404, {"error": "connection not registered; connect first"})
    except Exception as e:
        return ws_response(500, {"error": str(e)})


def _on_send_location(connection_id, event):
    data = _parse_body(event)
    delivery_id = data.get("delivery_id")
    lat = data.get("lat")
    lng = data.get("lng")
    if not delivery_id or lat is None or lng is None:
        return ws_response(
            400, {"error": "delivery_id, lat, lng required"}
        )

    payload = {
        "action": "locationUpdate",
        "delivery_id": delivery_id,
        "lat": lat,
        "lng": lng,
        "sent_at": _utc_now_iso(),
        "from_connection": connection_id,
    }

    try:
        scan = table.scan(
            FilterExpression=Attr("subscribed_delivery_id").eq(delivery_id),
            ProjectionExpression="connection_id",
        )
        targets = [i["connection_id"] for i in scan.get("Items", []) if i.get("connection_id")]
    except Exception as e:
        return ws_response(500, {"error": str(e)})

    client = _mgmt_client(event)
    data_bytes = json.dumps(payload, default=str).encode("utf-8")
    sent = 0
    errors = []
    for cid in targets:
        if cid == connection_id:
            continue
        try:
            client.post_to_connection(ConnectionId=cid, Data=data_bytes)
            sent += 1
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "GoneException":
                try:
                    table.delete_item(Key={"connection_id": cid})
                except Exception:
                    pass
            errors.append({"connection_id": cid, "error": str(e)})

    return ws_response(
        200,
        {"broadcast": True, "sent": sent, "subscribers": len(targets), "errors": errors},
    )
