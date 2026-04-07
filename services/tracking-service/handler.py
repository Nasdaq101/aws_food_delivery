import base64
import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

# websocket api handler for real-time tracking of the delivery, you can finish websocket based on this code.

dynamodb = boto3.resource("dynamodb")

TRACKING_TABLE = os.environ.get("TRACKING_CONNECTIONS_TABLE_NAME", "FoodDelivery-TrackingConnections")
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
        print(f"Tracking service received event: {json.dumps(event, default=str)}")
        rc = event.get("requestContext") or {}
        route = rc.get("routeKey", "")
        connection_id = rc.get("connectionId")

        print(f"Route: {route}, ConnectionId: {connection_id}")

        if route == "$connect":
            return _on_connect(connection_id, event)
        if route == "$disconnect":
            return _on_disconnect(connection_id)
        if route == "subscribe":
            return _on_subscribe(connection_id, event)
        if route == "sendLocation":
            return _on_send_location(connection_id, event)

        print(f"Unknown route: {route}")
        return ws_response(400, {"error": "Unknown route", "routeKey": route})
    except Exception as e:
        print(f"Error in tracking service: {str(e)}")
        return ws_response(500, {"error": str(e)})


def _on_connect(connection_id, event):
    print(f"_on_connect called with connection_id: {connection_id}")
    if not connection_id:
        print("ERROR: missing connectionId")
        return ws_response(400, {"error": "missing connectionId"})

    # Extract user context from authorizer
    request_context = event.get("requestContext", {})
    authorizer = request_context.get("authorizer", {})
    user_id = authorizer.get("user_id")
    user_role = authorizer.get("role", "customer")

    print(f"User context: user_id={user_id}, role={user_role}")

    now = _utc_now_iso()
    try:
        # Don't include subscribed_delivery_id if it's None (DynamoDB GSI doesn't allow NULL)
        item = {
            "connection_id": connection_id,
            "user_id": user_id,
            "user_role": user_role,
            "connected_at": now,
            "updated_at": now,
        }

        table.put_item(Item=item)
        print(f"Connection stored successfully for user {user_id}")
        return ws_response(200, {"ok": True, "user_id": user_id})
    except Exception as e:
        print(f"Error storing connection: {str(e)}")
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
        print(f"Found {len(targets)} subscribers for delivery {delivery_id}: {targets}")
    except Exception as e:
        print(f"Error scanning for subscribers: {str(e)}")
        return ws_response(500, {"error": str(e)})

    client = _mgmt_client(event)
    data_bytes = json.dumps(payload, default=str).encode("utf-8")
    sent = 0
    errors = []
    for cid in targets:
        # Removed the skip - now sender also receives the broadcast
        # This allows the sender to see their own location updates
        try:
            print(f"Broadcasting to connection {cid}")
            client.post_to_connection(ConnectionId=cid, Data=data_bytes)
            sent += 1
            print(f"Successfully sent to connection {cid}")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            print(f"Error sending to {cid}: {code} - {str(e)}")
            if code == "GoneException":
                try:
                    table.delete_item(Key={"connection_id": cid})
                    print(f"Deleted stale connection {cid}")
                except Exception:
                    pass
            errors.append({"connection_id": cid, "error": str(e)})

    result = {"broadcast": True, "sent": sent, "subscribers": len(targets), "errors": errors}
    print(f"Broadcast complete: {result}")
    return ws_response(200, result)
