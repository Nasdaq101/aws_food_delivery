import json
import os
import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

# ddb is case-sensitive, so we need to normalize the fields for robust search.

dynamodb = boto3.resource("dynamodb")

RESTAURANTS_TABLE = os.environ.get("RESTAURANTS_TABLE_NAME", "FoodDelivery-Restaurants")
MENUS_TABLE = os.environ.get("MENUS_TABLE_NAME", "FoodDelivery-Menus")

restaurants_table = dynamodb.Table(RESTAURANTS_TABLE)
menus_table = dynamodb.Table(MENUS_TABLE)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def _scan_all_pages(table, **kwargs):
    items = []
    scan_kwargs = dict(kwargs)
    while True:
        res = table.scan(**scan_kwargs)
        items.extend(res.get("Items", []))
        lek = res.get("LastEvaluatedKey")
        if not lek:
            break
        scan_kwargs["ExclusiveStartKey"] = lek
    return items


def _query_city_restaurants(city: str):
    items = []
    kwargs = {
        "IndexName": "city-rating-index",
        "KeyConditionExpression": Key("city").eq(city),
        "ScanIndexForward": False,
    }
    while True:
        res = restaurants_table.query(**kwargs)
        items.extend(res.get("Items", []))
        lek = res.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def handle_search(query_params: dict):
    q = (query_params.get("q") or "").strip()
    city = (query_params.get("city") or "").strip()
    cuisine = (query_params.get("cuisine") or "").strip()

    try:
        if city:
            restaurant_hits = _query_city_restaurants(city)
        else:
            scan_kw = {}
            if cuisine:
                scan_kw["FilterExpression"] = Attr("cuisine").eq(cuisine)
            restaurant_hits = _scan_all_pages(restaurants_table, **scan_kw)

        if city and cuisine:
            restaurant_hits = [
                r for r in restaurant_hits if (r.get("cuisine") or "").lower() == cuisine.lower()
            ]

        if q:
            ql = q.lower()

            def rest_matches(r):
                blob = " ".join(
                    [
                        str(r.get("name") or ""),
                        str(r.get("description") or ""),
                        str(r.get("cuisine") or ""),
                    ]
                ).lower()
                return ql in blob

            restaurant_hits = [r for r in restaurant_hits if rest_matches(r)]

        rid_set = {r["restaurant_id"] for r in restaurant_hits}

        menu_scan = {}
        if q:
            # TODO: DynamoDB contains() is case-sensitive; use OpenSearch or normalize fields for robust search
            menu_scan["FilterExpression"] = Attr("name").contains(q) | Attr("description").contains(q)

        menu_hits = _scan_all_pages(menus_table, **menu_scan)

        if city or cuisine:
            menu_hits = [m for m in menu_hits if m.get("restaurant_id") in rid_set]

    except ClientError:
        raise

    return response(
        200,
        {
            "query": {"q": q, "city": city, "cuisine": cuisine},
            "restaurants": restaurant_hits,
            "menu_items": menu_hits,
        },
    )


def lambda_handler(event, context):
    try:
        http_method = event.get("httpMethod", "")
        path = (event.get("path") or "").rstrip("/") or "/"
        query_params = event.get("queryStringParameters") or {}

        if http_method == "GET" and path == "/search":
            return handle_search(query_params)

        return response(404, {"error": "NotFound", "message": "No route matched"})
    except ClientError as e:
        return response(502, {"error": "DynamoDBError", "message": str(e)})
    except Exception as e:
        return response(500, {"error": "InternalError", "message": str(e)})
