import json
import boto3
import os
from datetime import datetime, timezone
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")

ANALYTICS_TABLE = os.environ.get("ANALYTICS_TABLE_NAME", "FoodDelivery-Analytics")


def _api_event(event):
    return "httpMethod" in event or event.get("requestContext", {}).get("http")


def _get_method(event):
    return event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")


def _get_path(event):
    return event.get("path") or event.get("rawPath") or ""


def _query_params(event):
    return event.get("queryStringParameters") or {}


def _handle_http(event):
    if _get_method(event) != "GET":
        return response(405, {"error": "Method not allowed"})

    qs = _query_params(event)
    metric = (qs.get("metric") or "").strip()
    start = (qs.get("start") or "").strip()
    end = (qs.get("end") or "").strip()

    if not metric or not start or not end:
        return response(
            400,
            {"error": "Missing required query parameters: metric, start, end (ISO 8601)"},
        )

    valid_metrics = (
        "order_count",
        "revenue",
        "avg_delivery_time",
        "popular_restaurants",
    )
    if metric not in valid_metrics:
        return response(
            400,
            {"error": f"metric must be one of: {', '.join(valid_metrics)}"},
        )

    table = dynamodb.Table(ANALYTICS_TABLE)
    items = []
    last_key = None

    while True:
        kwargs = {
            "KeyConditionExpression": "metric_type = :m AND #ts BETWEEN :s AND :e",
            "ExpressionAttributeNames": {"#ts": "timestamp"},
            "ExpressionAttributeValues": {
                ":m": metric,
                ":s": start,
                ":e": end,
            },
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        page = table.query(**kwargs)
        items.extend(page.get("Items", []))
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break

    return response(
        200,
        {
            "metric": metric,
            "start": start,
            "end": end,
            "count": len(items),
            "data": _serialize_items(items),
        },
    )


def _serialize_items(items):
    out = []
    for it in items:
        row = {}
        for k, v in it.items():
            if isinstance(v, Decimal):
                row[k] = float(v) if v % 1 else int(v)
            else:
                row[k] = v
        out.append(row)
    return out


def _handle_eventbridge(event):
    """Record metrics from EventBridge (e.g. order placed, delivery completed)."""
    detail = event.get("detail") or {}
    detail_type = event.get("detail-type", "")

    metric_type = detail.get("metric_type")
    if not metric_type:
        if "order" in detail_type.lower():
            metric_type = "order_count"
        else:
            metric_type = "order_count"

    ts = detail.get("timestamp") or datetime.now(timezone.utc).isoformat()
    item = {
        "metric_type": metric_type,
        "timestamp": ts,
        "source": event.get("source", ""),
        "detail_type": detail_type,
        "order_count": Decimal(str(detail.get("order_count", 1 if metric_type == "order_count" else 0))),
        "revenue": Decimal(str(detail.get("revenue", 0))),
        "avg_delivery_time": Decimal(str(detail.get("avg_delivery_time", 0))),
        "detail_json": json.dumps(detail, default=str),
    }
    pr = detail.get("popular_restaurants")
    if pr is not None:
        if isinstance(pr, list) and all(isinstance(x, str) for x in pr):
            item["popular_restaurants"] = pr
        elif isinstance(pr, str):
            item["popular_restaurants"] = [pr]

    table = dynamodb.Table(ANALYTICS_TABLE)
    table.put_item(Item=item)

    return response(
        200,
        {"recorded": True, "metric_type": metric_type, "timestamp": ts},
    )


def lambda_handler(event, context):
    try:
        if _api_event(event):
            return _handle_http(event)
        if event.get("source") and "detail" in event:
            return _handle_eventbridge(event)
        return response(400, {"error": "Unsupported event source"})
    except Exception as e:
        return response(500, {"error": str(e)})


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }
