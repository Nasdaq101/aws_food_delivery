import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote
from urllib.request import Request, urlopen
import time

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

# todos are left, you can decide to implement it or not.

dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")
sqs = boto3.client("sqs")
stepfunctions = boto3.client("stepfunctions")
ssm = boto3.client("ssm")

ORDERS_TABLE = os.environ.get("ORDERS_TABLE_NAME", "FoodDelivery-Orders")
CARTS_TABLE = os.environ.get("CARTS_TABLE_NAME", "FoodDelivery-Carts")
USERS_TABLE = os.environ.get("USERS_TABLE_NAME", "FoodDelivery-Users")
RESTAURANTS_TABLE = os.environ.get("RESTAURANTS_TABLE_NAME", "FoodDelivery-Restaurants")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "FoodDeliveryEventBus")
ORDER_QUEUE_URL = os.environ.get("ORDER_QUEUE_URL", "")
ORDER_WORKFLOW_PARAM = "/fooddelivery/stepfunctions/order-workflow-arn"

# Cache for Step Functions ARN (loaded on first invocation)
_order_workflow_arn = None

orders_table = dynamodb.Table(ORDERS_TABLE)
carts_table = dynamodb.Table(CARTS_TABLE)
users_table = dynamodb.Table(USERS_TABLE)
restaurants_table = dynamodb.Table(RESTAURANTS_TABLE)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get_order_workflow_arn():
    """Get Step Functions workflow ARN from SSM Parameter Store (cached)"""
    global _order_workflow_arn
    if _order_workflow_arn:
        return _order_workflow_arn

    try:
        response = ssm.get_parameter(Name=ORDER_WORKFLOW_PARAM)
        _order_workflow_arn = response["Parameter"]["Value"]
        return _order_workflow_arn
    except ClientError as e:
        print(f"Failed to get Step Functions ARN from SSM: {e}")
        return None


def _get_user_role(user_id: str):
    """Get user's role from the users table"""
    try:
        res = users_table.get_item(Key={"user_id": user_id})
        user = res.get("Item")
        if not user:
            return None
        return user.get("role", "customer")  # Default to customer if role not set
    except ClientError as e:
        print(f"Failed to get user role: {e}")
        return None


def _geocode_address(address: str):
    """
    Convert address to lat,lng coordinates using OpenStreetMap Nominatim.
    Returns: dict with {coords: "lat,lng", display_name: "formatted address"} or None if geocoding fails
    """
    if not address or address.strip() == "":
        return None

    try:
        # Use Nominatim API (free, no API key needed)
        # Nominatim usage policy: max 1 request/second
        time.sleep(1)  # Rate limiting

        encoded_address = quote(address)
        url = f"https://nominatim.openstreetmap.org/search?q={encoded_address}&format=json&limit=1"

        # Add User-Agent header (required by Nominatim)
        req = Request(url, headers={'User-Agent': 'FoodDeliveryApp/1.0'})

        with urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())

            if data and len(data) > 0:
                lat = data[0].get('lat')
                lng = data[0].get('lon')
                display_name = data[0].get('display_name', address)

                if lat and lng:
                    result = {
                        "coords": f"{lat},{lng}",
                        "display_name": display_name
                    }
                    print(f"Geocoded '{address}' -> {result['coords']}, {result['display_name']}")
                    return result

        print(f"Could not geocode address: {address}")
        return None

    except Exception as e:
        print(f"Geocoding error for '{address}': {e}")
        return None


def _get_restaurant_location(restaurant_id: str):
    """Get restaurant's location coordinates from restaurants table, geocoding address if needed"""
    try:
        res = restaurants_table.get_item(Key={"restaurant_id": restaurant_id})
        restaurant = res.get("Item")
        if not restaurant:
            print(f"Restaurant {restaurant_id} not found")
            return None

        # Check if location coordinates already exist
        location = restaurant.get("location")
        address = restaurant.get("address")
        address_display = restaurant.get("address_display")

        if location:
            print(f"Found cached restaurant location: {location}")
            # Return dict with coords and display name
            display = address_display or address or location
            return {
                "coords": location,
                "display_name": display
            }

        # Try to geocode the address
        address = restaurant.get("address")
        if address:
            print(f"Geocoding restaurant address: {address}")
            geocode_result = _geocode_address(address)

            # Cache the geocoded location in the database for future use
            if geocode_result:
                coords = geocode_result["coords"]
                display_name = geocode_result["display_name"]
                try:
                    restaurants_table.update_item(
                        Key={"restaurant_id": restaurant_id},
                        UpdateExpression="SET #loc = :loc, address_display = :display",
                        ExpressionAttributeNames={"#loc": "location"},
                        ExpressionAttributeValues={
                            ":loc": coords,
                            ":display": display_name
                        }
                    )
                    print(f"Cached geocoded location for restaurant {restaurant_id}")
                except Exception as e:
                    print(f"Failed to cache location: {e}")

            return geocode_result

        print(f"Restaurant {restaurant_id} has no address or location data")
        return None
    except ClientError as e:
        print(f"Failed to get restaurant location: {e}")
        return None


def _get_user_address(user_id: str):
    """Get user's delivery address coordinates, geocoding if needed"""
    try:
        res = users_table.get_item(Key={"user_id": user_id})
        user = res.get("Item")
        if not user:
            return None

        # Check if user has pre-stored location coordinates
        location = user.get("location")
        address = user.get("address")
        address_display = user.get("address_display")

        if location:
            print(f"Found cached user location: {location}")
            # Return dict with coords and display name
            display = address_display or address or location
            return {
                "coords": location,
                "display_name": display
            }

        # Try to geocode the address
        address = user.get("address")
        if address:
            print(f"Geocoding user address: {address}")
            geocode_result = _geocode_address(address)

            # Cache the geocoded location (optional - user locations change more frequently)
            if geocode_result:
                coords = geocode_result["coords"]
                display_name = geocode_result["display_name"]
                try:
                    users_table.update_item(
                        Key={"user_id": user_id},
                        UpdateExpression="SET #loc = :loc, address_display = :display",
                        ExpressionAttributeNames={"#loc": "location"},
                        ExpressionAttributeValues={
                            ":loc": coords,
                            ":display": display_name
                        }
                    )
                    print(f"Cached geocoded location for user {user_id}")
                except Exception as e:
                    print(f"Failed to cache user location: {e}")

            return geocode_result

        print(f"User {user_id} has no address data")
        return None
    except ClientError as e:
        print(f"Failed to get user address: {e}")
        return None


def handle_list_orders(user_id: str):
    print(f"Listing orders for user_id: {user_id}")

    if not user_id:
        print("Missing user_id for list orders")
        return response(401, {"error": "Unauthorized", "message": "Missing authenticated user"})
    try:
        res = orders_table.query(
            IndexName="user-orders-index",
            KeyConditionExpression=Key("user_id").eq(user_id),
            ScanIndexForward=False,
        )
        items = res.get("Items", [])
        while "LastEvaluatedKey" in res:
            res = orders_table.query(
                IndexName="user-orders-index",
                KeyConditionExpression=Key("user_id").eq(user_id),
                ExclusiveStartKey=res["LastEvaluatedKey"],
                ScanIndexForward=False,
            )
            items.extend(res.get("Items", []))

        print(f"Found {len(items)} orders for user {user_id}")
        return response(200, {"orders": items})
    except ClientError as e:
        print(f"Error querying orders: {str(e)}")
        raise


def handle_create_order(user_id: str, body: dict):
    print(f"Creating order for user_id: {user_id}, body: {body}")

    if not user_id:
        print("Missing user_id")
        return response(401, {"error": "Unauthorized", "message": "Missing authenticated user"})

    # Check user role - only customers can place orders
    user_role = _get_user_role(user_id)
    print(f"User role: {user_role}")

    if not user_role:
        print("User profile not found")
        return response(404, {"error": "NotFound", "message": "User profile not found"})

    if user_role != "customer":
        print(f"User role '{user_role}' is not allowed to place orders")
        return response(403, {
            "error": "Forbidden",
            "message": f"Only customers can place orders. Your role is '{user_role}'"
        })

    cart_res = carts_table.get_item(Key={"user_id": user_id})
    cart = cart_res.get("Item") or {"user_id": user_id, "items": []}
    lines = cart.get("items") or []
    print(f"Cart has {len(lines)} items")

    if not lines:
        print("Cart is empty")
        return response(400, {"error": "BadRequest", "message": "Cart is empty"})
    # TODO: enforce single-restaurant carts, re-fetch menu prices, payment pre-auth
    restaurant_id = body.get("restaurant_id") or lines[0].get("restaurant_id")
    if not restaurant_id:
        return response(400, {"error": "BadRequest", "message": "Unable to determine restaurant_id"})
    order_id = str(uuid.uuid4())
    created_at = _now_iso()

    # Calculate total amount
    total_amount = sum(item.get("quantity", 0) * item.get("unit_price_cents", 0) for item in lines) / 100
    total_amount_decimal = Decimal(str(total_amount))

    # Get restaurant location (will geocode if needed)
    restaurant_location_result = _get_restaurant_location(restaurant_id)
    restaurant_location = None
    restaurant_address_display = None
    if restaurant_location_result:
        if isinstance(restaurant_location_result, dict):
            restaurant_location = restaurant_location_result.get("coords")
            restaurant_address_display = restaurant_location_result.get("display_name")
        else:
            # Backward compatibility: old format is just a string
            restaurant_location = restaurant_location_result

    # Get delivery address - try from request body first, then user profile
    delivery_address_input = body.get("delivery_address")
    delivery_address = None
    delivery_address_display = None

    if delivery_address_input:
        # Check if it's already coordinates (format: "lat,lng")
        if ',' in delivery_address_input and len(delivery_address_input.split(',')) == 2:
            try:
                parts = delivery_address_input.split(',')
                float(parts[0].strip())
                float(parts[1].strip())
                delivery_address = delivery_address_input  # Already coordinates
                delivery_address_display = delivery_address_input  # Use coords as display
                print(f"Using provided coordinates: {delivery_address}")
            except ValueError:
                # Not coordinates, geocode it
                print(f"Geocoding provided address: {delivery_address_input}")
                geocode_result = _geocode_address(delivery_address_input)
                if geocode_result:
                    delivery_address = geocode_result["coords"]
                    delivery_address_display = geocode_result["display_name"]
        else:
            # Plain text address, geocode it
            print(f"Geocoding provided address: {delivery_address_input}")
            geocode_result = _geocode_address(delivery_address_input)
            if geocode_result:
                delivery_address = geocode_result["coords"]
                delivery_address_display = geocode_result["display_name"]
    else:
        # Use user's default address from profile
        user_address_result = _get_user_address(user_id)
        if user_address_result:
            if isinstance(user_address_result, dict):
                delivery_address = user_address_result.get("coords")
                delivery_address_display = user_address_result.get("display_name")
            else:
                # Backward compatibility
                delivery_address = user_address_result

    # If still no delivery address, use a default SF location
    if not delivery_address:
        delivery_address = "37.7749,-122.4194"  # Default SF coordinates
        delivery_address_display = "San Francisco, CA (default)"
        print("Warning: No delivery address found, using default")

    order = {
        "order_id": order_id,
        "user_id": user_id,
        "restaurant_id": restaurant_id,
        "items": lines,
        "total": total_amount_decimal,
        "status": "PLACED",
        "created_at": created_at,
        "updated_at": created_at,
        "notes": body.get("notes") or "",
    }

    # Add location data if available
    if restaurant_location:
        order["restaurant_location"] = restaurant_location
        if restaurant_address_display:
            order["restaurant_address_display"] = restaurant_address_display
    if delivery_address:
        order["delivery_address"] = delivery_address
        if delivery_address_display:
            order["delivery_address_display"] = delivery_address_display

    print(f"Creating order: {order_id} for ${total_amount}")
    print(f"Restaurant: {restaurant_address_display or restaurant_location}")
    print(f"Delivery: {delivery_address_display or delivery_address}")
    orders_table.put_item(Item=order)
    carts_table.put_item(Item={"user_id": user_id, "items": []})
    print("Order saved to DynamoDB")

    # ── Trigger Step Functions Workflow ──
    workflow_arn = _get_order_workflow_arn()
    if workflow_arn:
        try:
            # Parse delivery_address if it's in "lat,lng" format
            delivery_dict = {
                "address": delivery_address_display or delivery_address or "Unknown"
            }
            if delivery_address and ',' in delivery_address:
                try:
                    parts = delivery_address.split(',')
                    delivery_dict["lat"] = float(parts[0].strip())
                    delivery_dict["lng"] = float(parts[1].strip())
                except (ValueError, IndexError):
                    pass  # Keep as string if parsing fails

            # Prepare restaurant location dict
            restaurant_dict = {
                "address": restaurant_address_display or restaurant_location or "Unknown"
            }
            if restaurant_location and ',' in restaurant_location:
                try:
                    parts = restaurant_location.split(',')
                    restaurant_dict["lat"] = float(parts[0].strip())
                    restaurant_dict["lng"] = float(parts[1].strip())
                except (ValueError, IndexError):
                    pass

            stepfunctions.start_execution(
                stateMachineArn=workflow_arn,
                name=f"order-{order_id}",
                input=json.dumps({
                    "order_id": order_id,
                    "user_id": user_id,
                    "restaurant_id": restaurant_id,
                    "total_amount": float(total_amount),  # Convert Decimal to float for JSON
                    "items": lines,
                    "restaurant_location": restaurant_dict,  # Required for driver assignment
                    "delivery_address": delivery_dict,
                }, default=str)  # Handle any remaining Decimal types
            )
            print(f"Started Step Functions workflow for order {order_id}")
            print(f"Workflow input - restaurant: {restaurant_dict}, delivery: {delivery_dict}")
        except ClientError as e:
            print(f"Failed to start Step Functions workflow: {e}")
            # Don't fail the order creation if workflow fails to start
        except Exception as e:
            print(f"Unexpected error starting workflow: {e}")
            # Don't fail the order creation if workflow fails to start
    else:
        print("Step Functions workflow ARN not found, using fallback")

        # Fallback to legacy SQS method if Step Functions not configured
        if ORDER_QUEUE_URL:
            try:
                sqs.send_message(
                    QueueUrl=ORDER_QUEUE_URL,
                    MessageBody=json.dumps({"order_id": order_id, "event": "ORDER_PLACED"}, default=str),
                )
            except ClientError:
                # TODO: dead-letter / retry policy — order already persisted
                raise

    # TODO: emit ORDER_PLACED on EventBridge if downstream needs it
    print(f"Order {order_id} created successfully, returning 201")
    return response(201, {"order": order})


def handle_get_order(order_id: str, user_id: str):
    if not user_id:
        return response(401, {"error": "Unauthorized", "message": "Authentication required"})
    res = orders_table.get_item(Key={"order_id": order_id})
    item = res.get("Item")
    if not item:
        return response(404, {"error": "NotFound", "message": "Order not found"})

    # Allow access if:
    # 1. User is the customer who placed the order
    # 2. User is the assigned driver (check delivery record)
    # 3. User is the restaurant owner (TODO: implement later)
    is_customer = item.get("user_id") == user_id

    # Check if user is the assigned driver
    is_driver = False

    # Method 1: Check if order has delivery_id and user is the assigned driver
    delivery_id = item.get("delivery_id")
    if delivery_id:
        try:
            deliveries_table = dynamodb.Table(os.environ.get("DELIVERIES_TABLE", "FoodDelivery-Deliveries"))
            delivery_res = deliveries_table.get_item(Key={"delivery_id": delivery_id})
            delivery = delivery_res.get("Item")
            if delivery and delivery.get("driver_id") == user_id:
                is_driver = True
        except Exception as e:
            print(f"Error checking driver access via delivery_id: {str(e)}")

    # Method 2: If order doesn't have delivery_id yet, scan deliveries for this order_id
    # This handles the timing issue where driver accepts but order hasn't been updated yet
    if not is_driver:
        try:
            deliveries_table = dynamodb.Table(os.environ.get("DELIVERIES_TABLE", "FoodDelivery-Deliveries"))
            from boto3.dynamodb.conditions import Attr
            delivery_scan = deliveries_table.scan(
                FilterExpression=Attr("order_id").eq(order_id) & Attr("driver_id").eq(user_id),
                Limit=1
            )
            if delivery_scan.get("Items"):
                is_driver = True
                print(f"Driver {user_id} granted access to order {order_id} via delivery scan")
        except Exception as e:
            print(f"Error checking driver access via order_id scan: {str(e)}")

    # Method 3: Check driver offers table for accepted offers
    # This is the most immediate check - offers are updated instantly when driver accepts
    if not is_driver:
        try:
            driver_offers_table = dynamodb.Table(os.environ.get("DRIVER_OFFERS_TABLE", "FoodDelivery-DriverOffers"))
            from boto3.dynamodb.conditions import Attr
            offer_scan = driver_offers_table.scan(
                FilterExpression=Attr("order_id").eq(order_id) & Attr("driver_id").eq(user_id) & Attr("status").eq("accepted"),
                Limit=1
            )
            if offer_scan.get("Items"):
                is_driver = True
                print(f"Driver {user_id} granted access to order {order_id} via accepted offer")
        except Exception as e:
            print(f"Error checking driver access via offers table: {str(e)}")

    if not (is_customer or is_driver):
        print(f"Access denied for user {user_id} to order {order_id}: is_customer={is_customer}, is_driver={is_driver}")
        return response(403, {"error": "Forbidden", "message": "Cannot access this order"})

    return response(200, {"order": item})


def handle_update_status(order_id: str, user_id: str, body: dict):
    # TODO: authorize status transitions by role (restaurant, driver, admin)
    new_status = body.get("status")
    if not new_status:
        return response(400, {"error": "BadRequest", "message": "status is required"})
    res = orders_table.get_item(Key={"order_id": order_id})
    item = res.get("Item")
    if not item:
        return response(404, {"error": "NotFound", "message": "Order not found"})
    old_status = item.get("status")
    updated_at = _now_iso()
    try:
        out = orders_table.update_item(
            Key={"order_id": order_id},
            UpdateExpression="SET #s = :st, #u = :u",
            ExpressionAttributeNames={"#s": "status", "#u": "updated_at"},
            ExpressionAttributeValues={":st": new_status, ":u": updated_at},
            ConditionExpression="attribute_exists(order_id)",
            ReturnValues="ALL_NEW",
        )
        new_item = out.get("Attributes", {})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return response(404, {"error": "NotFound", "message": "Order not found"})
        raise

    detail = {
        "order_id": order_id,
        "old_status": old_status,
        "new_status": new_status,
        "user_id": item.get("user_id"),
        "restaurant_id": item.get("restaurant_id"),
        "updated_at": updated_at,
    }
    try:
        events.put_events(
            Entries=[
                {
                    "Source": "fooddelivery.orders",
                    "DetailType": "OrderStatusChanged",
                    "EventBusName": EVENT_BUS_NAME,
                    "Detail": json.dumps(detail, default=str),
                }
            ]
        )
    except ClientError:
        # TODO: outbox pattern — do not silently ignore in production
        raise

    return response(200, {"order": new_item})


def handle_validate_order(order_id: str):
    """Called by Step Functions to validate an order"""
    res = orders_table.get_item(Key={"order_id": order_id})
    order = res.get("Item")
    if not order:
        return {"valid": False, "error": "Order not found"}

    # Validate order has items
    if not order.get("items"):
        return {"valid": False, "error": "Order has no items"}

    # Calculate total
    total_amount = order.get("total", 0)

    # Get actual restaurant location from order (geocoded during order creation)
    restaurant_location = order.get("restaurant_location")
    restaurant_address_display = order.get("restaurant_address_display")  # NEW: Get display address

    if not restaurant_location:
        # Fallback: try to get from restaurant record
        restaurant_id = order.get("restaurant_id")
        if restaurant_id:
            restaurant_location = _get_restaurant_location(restaurant_id)

    # Parse restaurant_location into dict format with lat/lng/address
    restaurant_dict = {"address": "Unknown", "lat": 37.7849, "lng": -122.4094}
    if restaurant_location:
        if isinstance(restaurant_location, str) and ',' in restaurant_location:
            # Format: "lat,lng" or "lat,lng (Address)"
            parts = restaurant_location.split('(')
            coords = parts[0].strip()
            try:
                lat_str, lng_str = coords.split(',')
                restaurant_dict["lat"] = float(lat_str.strip())
                restaurant_dict["lng"] = float(lng_str.strip())
                # Use display address if available, otherwise parse from string
                if restaurant_address_display:
                    restaurant_dict["address"] = restaurant_address_display
                else:
                    restaurant_dict["address"] = parts[1].rstrip(')').strip() if len(parts) > 1 else coords
            except (ValueError, IndexError):
                restaurant_dict["address"] = restaurant_address_display or restaurant_location
        elif isinstance(restaurant_location, dict):
            restaurant_dict = restaurant_location
            # Override with display address if available
            if restaurant_address_display:
                restaurant_dict["address"] = restaurant_address_display
        else:
            restaurant_dict["address"] = restaurant_address_display or str(restaurant_location)

    # Get actual delivery address from order (geocoded during order creation)
    delivery_address = order.get("delivery_address")
    delivery_address_display = order.get("delivery_address_display")  # NEW: Get display address

    # Parse delivery_address into dict format with lat/lng/address
    delivery_dict = {"address": "Unknown", "lat": 37.7749, "lng": -122.4194}
    if delivery_address:
        if isinstance(delivery_address, str) and ',' in delivery_address:
            # Format: "lat,lng" or "lat,lng (Address)"
            parts = delivery_address.split('(')
            coords = parts[0].strip()
            try:
                lat_str, lng_str = coords.split(',')
                delivery_dict["lat"] = float(lat_str.strip())
                delivery_dict["lng"] = float(lng_str.strip())
                # Use display address if available, otherwise parse from string
                if delivery_address_display:
                    delivery_dict["address"] = delivery_address_display
                else:
                    delivery_dict["address"] = parts[1].rstrip(')').strip() if len(parts) > 1 else coords
            except (ValueError, IndexError):
                delivery_dict["address"] = delivery_address_display or delivery_address
        elif isinstance(delivery_address, dict):
            delivery_dict = delivery_address
            # Override with display address if available
            if delivery_address_display:
                delivery_dict["address"] = delivery_address_display
        else:
            delivery_dict["address"] = delivery_address_display or str(delivery_address)

    print(f"Validation - restaurant_location: {restaurant_dict}, delivery_address: {delivery_dict}")

    return {
        "valid": True,
        "order_id": order_id,
        "restaurant_id": order.get("restaurant_id"),
        "total_amount": total_amount,
        "restaurant_location": restaurant_dict,
        "delivery_address": delivery_dict,
        "items": order.get("items", []),
    }


def handle_update_order_status(order_id: str, status: str, error: str = None, delivery_id: str = None):
    """Called by Step Functions to update order status"""
    try:
        now = _now_iso()
        update_expr = "SET #s = :st, #u = :u"
        expr_names = {"#s": "status", "#u": "updated_at"}
        expr_values = {":st": status, ":u": now}

        if error:
            update_expr += ", #e = :e"
            expr_names["#e"] = "error_message"
            expr_values[":e"] = error

        if delivery_id:
            update_expr += ", #d = :did"
            expr_names["#d"] = "delivery_id"
            expr_values[":did"] = delivery_id

        out = orders_table.update_item(
            Key={"order_id": order_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ConditionExpression="attribute_exists(order_id)",
            ReturnValues="ALL_NEW",
        )

        updated_order = out.get("Attributes", {})

        # Emit OrderStatusChanged event for WebSocket notification
        try:
            events.put_events(
                Entries=[
                    {
                        "Source": "fooddelivery.orders",
                        "DetailType": "OrderStatusChanged",
                        "EventBusName": EVENT_BUS_NAME,
                        "Detail": json.dumps({
                            "order_id": order_id,
                            "status": status,
                            "user_id": updated_order.get("user_id"),
                            "restaurant_id": updated_order.get("restaurant_id"),
                            "timestamp": now,
                        }, default=str),
                    }
                ]
            )
            print(f"Emitted OrderStatusChanged event: order_id={order_id}, status={status}")
        except Exception as e:
            print(f"Warning: Failed to emit OrderStatusChanged event: {str(e)}")
            # Don't fail the status update if event emission fails

        return {"success": True, "order": updated_order}
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {"success": False, "error": "Order not found"}
        raise


def lambda_handler(event, context):
    try:
        # Check if this is a Step Functions invocation (has 'action' in body)
        if "action" in event:
            action = event.get("action")
            if action == "validate":
                return handle_validate_order(event.get("order_id"))
            elif action == "update_status":
                return handle_update_order_status(
                    event.get("order_id"),
                    event.get("status"),
                    event.get("error"),
                    event.get("delivery_id")
                )

        # Otherwise, it's an HTTP API call
        http_method = event.get("httpMethod", "")
        path = (event.get("path") or "").rstrip("/") or "/"
        body = json.loads(event.get("body") or "{}")
        user_id = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("claims", {})
            .get("sub", "")
        )

        print(f"Order Service - {http_method} {path} - User: {user_id}")

        if http_method == "GET" and path == "/orders":
            return handle_list_orders(user_id)
        if http_method == "POST" and path == "/orders":
            return handle_create_order(user_id, body)

        m = re.match(r"^/orders/([^/]+)$", path)
        if m:
            oid = m.group(1)
            if http_method == "GET":
                return handle_get_order(oid, user_id)
            if http_method == "PUT":
                return handle_update_status(oid, user_id, body)

        return response(404, {"error": "NotFound", "message": "No route matched"})
    except json.JSONDecodeError as e:
        print(f"JSON Decode Error: {str(e)}")
        return response(400, {"error": "BadRequest", "message": "Invalid JSON body"})
    except ClientError as e:
        print(f"AWS Client Error: {str(e)}")
        return response(502, {"error": "AWSError", "message": str(e)})
    except Exception as e:
        print(f"Unexpected Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return response(500, {"error": "InternalError", "message": str(e)})
