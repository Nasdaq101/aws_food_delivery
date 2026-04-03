import json
import boto3
import os
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")

USERS_TABLE = os.environ.get("USERS_TABLE_NAME", "FoodDelivery-Users")
ORDERS_TABLE = os.environ.get("ORDERS_TABLE_NAME", "FoodDelivery-Orders")
RESTAURANTS_TABLE = os.environ.get("RESTAURANTS_TABLE_NAME", "FoodDelivery-Restaurants")
DRIVERS_TABLE = os.environ.get("DRIVERS_TABLE_NAME", "FoodDelivery-Drivers")


def _get_method(event):
    return event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")


def _path(event):
    return event.get("path") or event.get("rawPath") or ""


def _query_params(event):
    return event.get("queryStringParameters") or {}


def _serialize_item(item):
    out = {}
    for k, v in item.items():
        if isinstance(v, Decimal):
            out[k] = float(v) if v % 1 else int(v)
        else:
            out[k] = v
    return out


def _count_table(table_name, max_pages=50):
    table = dynamodb.Table(table_name)
    total = 0
    start_key = None
    pages = 0
    while pages < max_pages:
        kwargs = {"Select": "COUNT"}
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        page = table.scan(**kwargs)
        total += page.get("Count", 0)
        start_key = page.get("LastEvaluatedKey")
        pages += 1
        if not start_key:
            break
    return total, bool(start_key)


def _scan_with_filters(table_name, filters, limit=100):
    table = dynamodb.Table(table_name)
    items = []
    start_key = None
    status_filter = (filters.get("status") or "").strip()
    city_filter = (filters.get("city") or "").strip()

    while len(items) < limit:
        kwargs = {"Limit": min(500, limit * 2)}
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        page = table.scan(**kwargs)
        for it in page.get("Items", []):
            if status_filter and str(it.get("status", "")).lower() != status_filter.lower():
                continue
            if city_filter and str(it.get("city", "")).lower() != city_filter.lower():
                continue
            items.append(_serialize_item(it))
            if len(items) >= limit:
                break
        start_key = page.get("LastEvaluatedKey")
        if not start_key:
            break
    return items


def _dashboard_stats():
    orders_tbl = dynamodb.Table(ORDERS_TABLE)
    revenue = Decimal("0")
    scanned = 0
    start_key = None
    while scanned < 5000:
        kwargs = {}
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        page = orders_tbl.scan(**kwargs)
        for it in page.get("Items", []):
            amt = it.get("total") or it.get("total_amount") or it.get("amount")
            if amt is not None:
                revenue += Decimal(str(amt))
        scanned += len(page.get("Items", []))
        start_key = page.get("LastEvaluatedKey")
        if not start_key:
            break

    total_orders, orders_truncated = _count_table(ORDERS_TABLE)
    total_users, users_truncated = _count_table(USERS_TABLE)
    total_restaurants, restaurants_truncated = _count_table(RESTAURANTS_TABLE)
    active_drivers = 0
    drivers_tbl = dynamodb.Table(DRIVERS_TABLE)
    dkey = None
    for _ in range(100):
        dk = {}
        if dkey:
            dk["ExclusiveStartKey"] = dkey
        dpage = drivers_tbl.scan(**dk)
        for d in dpage.get("Items", []):
            if str(d.get("status", "")).lower() in ("active", "online", "delivering"):
                active_drivers += 1
        dkey = dpage.get("LastEvaluatedKey")
        if not dkey:
            break

    rev_float = float(revenue) if revenue % 1 else int(revenue)

    return {
        "total_orders": total_orders,
        "total_users": total_users,
        "total_restaurants": total_restaurants,
        "revenue_sample_sum": rev_float,
        "revenue_note": "sum from up to 5000 scanned order items",
        "active_drivers": active_drivers,
        "truncated": {
            "orders_count": orders_truncated,
            "users_count": users_truncated,
            "restaurants_count": restaurants_truncated,
        },
    }


def lambda_handler(event, context):
    try:
        if _get_method(event) != "GET":
            return response(405, {"error": "Method not allowed"})

        path = _path(event)
        qs = _query_params(event)

        if path.endswith("/admin/dashboard") or path.rstrip("/").endswith("dashboard"):
            stats = _dashboard_stats()
            return response(200, stats)

        if "/admin/users" in path:
            items = _scan_with_filters(USERS_TABLE, qs, limit=int(qs.get("limit") or 100))
            return response(200, {"users": items, "count": len(items)})

        if "/admin/orders" in path:
            items = _scan_with_filters(ORDERS_TABLE, qs, limit=int(qs.get("limit") or 100))
            return response(200, {"orders": items, "count": len(items)})

        return response(
            404,
            {"error": "Not found", "path": path, "hint": "/admin/dashboard, /admin/users, /admin/orders"},
        )
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
