import json
import boto3
import os
import math
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")

RESTAURANTS_TABLE = os.environ.get("RESTAURANTS_TABLE_NAME", "FoodDelivery-Restaurants")
EARTH_RADIUS_KM = float(os.environ.get("EARTH_RADIUS_KM", "6371.0"))
AVG_SPEED_KMH = float(os.environ.get("DELIVERY_AVG_SPEED_KMH", "25"))


def _get_method(event):
    return event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")


def _parse_body(event):
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {}


def haversine_km(lat1, lon1, lat2, lon2):
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(min(1.0, math.sqrt(a)))
    return EARTH_RADIUS_KM * c


def _to_float(v, default=None):
    if v is None:
        return default
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _nearby_restaurants(lat, lon, radius_km, limit=50):
    table = dynamodb.Table(RESTAURANTS_TABLE)
    results = []
    exclusive_start_key = None

    while True:
        kwargs = {"Limit": 200}
        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key
        page = table.scan(**kwargs)
        for item in page.get("Items", []):
            rlat = _to_float(item.get("latitude") or item.get("lat"))
            rlon = _to_float(item.get("longitude") or item.get("lng") or item.get("lon"))
            if rlat is None or rlon is None:
                continue
            dist = haversine_km(lat, lon, rlat, rlon)
            if dist <= radius_km:
                results.append(
                    {
                        "restaurant_id": item.get("restaurant_id"),
                        "name": item.get("name"),
                        "distance_km": round(dist, 3),
                        "latitude": rlat,
                        "longitude": rlon,
                    }
                )
        exclusive_start_key = page.get("LastEvaluatedKey")
        if not exclusive_start_key or len(results) >= limit * 3:
            break

    results.sort(key=lambda x: x["distance_km"])
    return results[:limit]


def _estimate_delivery_minutes(distance_km):
    if distance_km <= 0:
        return 15
    travel_hours = distance_km / AVG_SPEED_KMH
    prep_buffer = 10
    return max(15, int(round(travel_hours * 60 + prep_buffer)))


def _handle_direct_invoke(payload):
    action = payload.get("action", "distance")

    if action == "distance":
        a = payload.get("from") or {}
        b = payload.get("to") or {}
        lat1 = _to_float(a.get("lat"))
        lon1 = _to_float(a.get("lon") or a.get("lng"))
        lat2 = _to_float(b.get("lat"))
        lon2 = _to_float(b.get("lon") or b.get("lng"))
        if None in (lat1, lon1, lat2, lon2):
            return response(400, {"error": "from/to with lat, lon required"})
        dist = haversine_km(lat1, lon1, lat2, lon2)
        return response(
            200,
            {
                "distance_km": round(dist, 3),
                "estimated_delivery_minutes": _estimate_delivery_minutes(dist),
            },
        )

    if action == "nearby":
        lat = _to_float(payload.get("lat"))
        lon = _to_float(payload.get("lon") or payload.get("lng"))
        radius = _to_float(payload.get("radius_km"), 5.0)
        if lat is None or lon is None:
            return response(400, {"error": "lat and lon required"})
        nearby = _nearby_restaurants(lat, lon, radius)
        for n in nearby:
            n["estimated_delivery_minutes"] = _estimate_delivery_minutes(n["distance_km"])
        return response(200, {"restaurants": nearby})

    if action == "eta":
        dist = _to_float(payload.get("distance_km"))
        if dist is None:
            return response(400, {"error": "distance_km required"})
        return response(
            200,
            {"estimated_delivery_minutes": _estimate_delivery_minutes(dist)},
        )

    return response(400, {"error": "unknown action", "allowed": ["distance", "nearby", "eta"]})


def lambda_handler(event, context):
    try:
        if "httpMethod" in event or event.get("requestContext", {}).get("http"):
            method = _get_method(event)
            if method == "OPTIONS":
                return response(200, {})

            body = _parse_body(event)
            qs = event.get("queryStringParameters") or {}

            if method == "GET":
                lat = _to_float(qs.get("lat"))
                lon = _to_float(qs.get("lon") or qs.get("lng"))
                lat2 = _to_float(qs.get("lat2"))
                lon2 = _to_float(qs.get("lon2") or qs.get("lng2"))
                radius = _to_float(qs.get("radius_km"), 5.0)

                path = event.get("path") or event.get("rawPath") or ""
                if "nearby" in path or qs.get("mode") == "nearby":
                    if lat is None or lon is None:
                        return response(400, {"error": "lat and lon query params required"})
                    nearby = _nearby_restaurants(lat, lon, radius)
                    for n in nearby:
                        n["estimated_delivery_minutes"] = _estimate_delivery_minutes(
                            n["distance_km"]
                        )
                    return response(200, {"restaurants": nearby})

                if lat is not None and lon is not None and lat2 is not None and lon2 is not None:
                    dist = haversine_km(lat, lon, lat2, lon2)
                    return response(
                        200,
                        {
                            "distance_km": round(dist, 3),
                            "estimated_delivery_minutes": _estimate_delivery_minutes(dist),
                        },
                    )
                return response(
                    400,
                    {"error": "Provide lat,lon and lat2,lon2 for distance, or lat,lon and mode=nearby"},
                )

            if method == "POST":
                return _handle_direct_invoke(body)

            return response(405, {"error": "Method not allowed"})

        return _handle_direct_invoke(event if isinstance(event, dict) else {})
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
