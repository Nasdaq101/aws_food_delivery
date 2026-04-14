"""
WebSocket Broadcaster Lambda

Listens to EventBridge events and broadcasts to WebSocket clients.
Handles:
1. OrderStatusChanged - Broadcast order status updates
2. DriverLocationUpdate - Broadcast driver location and ETA
"""

import os
import json
import boto3
from decimal import Decimal
from math import radians, cos, sin, sqrt, atan2
from datetime import datetime, timezone

# AWS clients
dynamodb = boto3.resource("dynamodb")
apigatewayv2 = boto3.client("apigatewayv2")
apigateway_management = None  # Initialized per-request with endpoint

# Environment variables
ORDERS_TABLE_NAME = os.environ["ORDERS_TABLE_NAME"]
DELIVERIES_TABLE_NAME = os.environ["DELIVERIES_TABLE_NAME"]
TRACKING_CONNECTIONS_TABLE_NAME = os.environ["TRACKING_CONNECTIONS_TABLE_NAME"]
REGION = os.environ.get("AWS_REGION", "us-west-1")

# Cache for WebSocket API endpoint (discovered at runtime)
_websocket_endpoint_cache = None

# DynamoDB tables
orders_table = dynamodb.Table(ORDERS_TABLE_NAME)
deliveries_table = dynamodb.Table(DELIVERIES_TABLE_NAME)
tracking_table = dynamodb.Table(TRACKING_CONNECTIONS_TABLE_NAME)


def get_websocket_endpoint():
    """
    Get WebSocket API endpoint by discovering the API dynamically.
    Uses caching to avoid repeated API calls.
    """
    global _websocket_endpoint_cache

    if _websocket_endpoint_cache:
        return _websocket_endpoint_cache

    try:
        # Find the WebSocket API by name
        response = apigatewayv2.get_apis()
        for api in response.get("Items", []):
            if api.get("Name") == "FoodDelivery-TrackingWS" and api.get("ProtocolType") == "WEBSOCKET":
                api_id = api["ApiId"]
                # Construct WebSocket URL
                _websocket_endpoint_cache = f"wss://{api_id}.execute-api.{REGION}.amazonaws.com/prod"
                print(f"Discovered WebSocket endpoint: {_websocket_endpoint_cache}")
                return _websocket_endpoint_cache

        print("WebSocket API not found")
        return None
    except Exception as e:
        print(f"Error discovering WebSocket endpoint: {str(e)}")
        return None


def lambda_handler(event, context):
    """
    Handle EventBridge events and broadcast to WebSocket clients.

    Event structure:
    {
        'detail-type': 'OrderStatusChanged' | 'DriverLocationUpdate',
        'detail': {...}
    }
    """
    print(f"Broadcaster received event: {json.dumps(event, default=str)}")

    try:
        detail_type = event.get("detail-type")
        detail = event.get("detail")

        if not detail_type or not detail:
            print("Invalid event structure - missing detail-type or detail")
            return

        if detail_type == "OrderStatusChanged":
            handle_order_status_changed(detail)
        elif detail_type == "DriverLocationUpdate":
            handle_driver_location_update(detail)
        elif detail_type == "DriverOfferCreated":
            handle_driver_offer_created(detail)
        elif detail_type == "DeliveryPickedUp":
            handle_delivery_picked_up(detail)
        elif detail_type == "DeliveryCompleted":
            handle_delivery_completed(detail)
        else:
            print(f"Unknown detail-type: {detail_type}")

    except Exception as e:
        print(f"Error in broadcaster: {str(e)}")
        raise


def handle_order_status_changed(detail):
    """
    Broadcast order status update to subscribed clients.

    Detail structure:
    {
        'order_id': '...',
        'status': 'CONFIRMED',
        'user_id': '...',
        'timestamp': '...'
    }
    """
    order_id = detail.get("order_id")
    status = detail.get("status")
    timestamp = detail.get("timestamp")
    user_id = detail.get("user_id")

    print(f"Broadcasting order status: order_id={order_id}, status={status}, user_id={user_id}")

    # Get order details to find delivery_id
    try:
        order_response = orders_table.get_item(Key={"order_id": order_id})
        order = order_response.get("Item")

        if not order:
            print(f"Order {order_id} not found")
            return

        # Use user_id from detail if not provided, fall back to order
        if not user_id:
            user_id = order.get("user_id")

        delivery_id = order.get("delivery_id")

        # Collect all connection IDs to broadcast to
        all_connection_ids = set()

        # Method 1: Find connections subscribed to this delivery (if delivery_id exists)
        if delivery_id:
            connections = get_subscribed_connections(delivery_id)
            all_connection_ids.update(connections)
            print(f"Found {len(connections)} connections subscribed to delivery {delivery_id}")

        # Method 2: Find connections for this specific user (customer)
        if user_id:
            from boto3.dynamodb.conditions import Attr
            response = tracking_table.scan(
                FilterExpression=Attr("user_id").eq(user_id),
                ProjectionExpression="connection_id",
            )
            user_connections = [conn["connection_id"] for conn in response.get("Items", [])]
            all_connection_ids.update(user_connections)
            print(f"Found {len(user_connections)} connections for user {user_id}")

        if not all_connection_ids:
            print(f"No connections found for order {order_id} (tried delivery_id and user_id)")
            return

        # Broadcast to all connections
        message = {
            "type": "status",
            "order_id": order_id,
            "status": status,
            "delivery_id": delivery_id,  # Include delivery_id in the message
            "timestamp": timestamp,
        }

        broadcast_to_connections(list(all_connection_ids), message)
        print(f"Broadcast to {len(all_connection_ids)} total connections")

    except Exception as e:
        print(f"Error handling order status changed: {str(e)}")
        raise


def handle_driver_location_update(detail):
    """
    Broadcast driver location and ETA to subscribed clients.

    Detail structure:
    {
        'delivery_id': '...',
        'driver_id': '...',
        'lat': 37.7749,
        'lng': -122.4194,
        'eta': '15 mins',  # Optional - calculated if not provided
        'timestamp': '...'
    }
    """
    delivery_id = detail.get("delivery_id")
    lat = detail.get("lat")
    lng = detail.get("lng")
    eta = detail.get("eta")
    timestamp = detail.get("timestamp")

    print(f"Broadcasting location: delivery_id={delivery_id}, lat={lat}, lng={lng}")

    # Calculate ETA if not provided
    if not eta:
        eta = calculate_eta(delivery_id, lat, lng)

    # Find all connections subscribed to this delivery
    connections = get_subscribed_connections(delivery_id)

    if not connections:
        print(f"No connections subscribed to delivery {delivery_id}")
        return

    # Broadcast to all connections
    message = {
        "type": "location",
        "delivery_id": delivery_id,
        "lat": float(lat) if isinstance(lat, Decimal) else lat,
        "lng": float(lng) if isinstance(lng, Decimal) else lng,
        "eta": eta,
        "timestamp": timestamp,
    }

    broadcast_to_connections(connections, message)


def handle_driver_offer_created(detail):
    """
    Broadcast driver offer to the specific driver's WebSocket connection.

    Detail structure:
    {
        'offer_id': '...',
        'driver_id': '...',
        'delivery_id': '...',
        'order_id': '...',
        'offer_details': {...},
        'expires_at': '...'
    }
    """
    driver_id = detail.get("driver_id")
    offer_id = detail.get("offer_id")

    print(f"Broadcasting driver offer {offer_id} to driver {driver_id}")

    # Find driver's WebSocket connection
    try:
        # Query tracking_connections by user_id (driver_id) and role=driver
        from boto3.dynamodb.conditions import Attr

        response = tracking_table.scan(
            FilterExpression=Attr("user_id").eq(driver_id) & Attr("user_role").eq("driver"),
            ProjectionExpression="connection_id",
        )

        connections = response.get("Items", [])
        connection_ids = [conn["connection_id"] for conn in connections]

        if not connection_ids:
            print(f"No active connections for driver {driver_id}")
            return

        # Convert Decimals to floats for proper JSON serialization
        offer_details = detail.get("offer_details", {})
        if "estimated_distance_km" in offer_details and isinstance(offer_details["estimated_distance_km"], Decimal):
            offer_details["estimated_distance_km"] = float(offer_details["estimated_distance_km"])
        if "estimated_payout" in offer_details and isinstance(offer_details["estimated_payout"], Decimal):
            offer_details["estimated_payout"] = float(offer_details["estimated_payout"])

        # Broadcast offer
        message = {
            "type": "driver_offer",
            "offer_id": offer_id,
            "delivery_id": detail.get("delivery_id"),
            "order_id": detail.get("order_id"),
            "offer_details": offer_details,
            "expires_at": detail.get("expires_at"),
        }

        broadcast_to_connections(connection_ids, message)

    except Exception as e:
        print(f"Error broadcasting driver offer: {str(e)}")
        raise


def get_subscribed_connections(delivery_id):
    """
    Query connections subscribed to a specific delivery using GSI.

    Returns list of connection_id strings.
    """
    try:
        response = tracking_table.query(
            IndexName="delivery-connections-index",
            KeyConditionExpression="subscribed_delivery_id = :did",
            ExpressionAttributeValues={":did": delivery_id},
        )

        connections = response.get("Items", [])
        connection_ids = [conn["connection_id"] for conn in connections]

        print(f"Found {len(connection_ids)} connections for delivery {delivery_id}")
        return connection_ids

    except Exception as e:
        print(f"Error querying subscribed connections: {str(e)}")
        return []


def broadcast_to_connections(connection_ids, message):
    """
    Send message to multiple WebSocket connections.

    Args:
        connection_ids: List of connection_id strings
        message: Dict to send as JSON
    """
    websocket_endpoint = get_websocket_endpoint()
    if not websocket_endpoint:
        print("ERROR: WebSocket API endpoint not configured")
        return

    # Initialize API Gateway Management API client
    global apigateway_management
    if not apigateway_management:
        apigateway_management = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=websocket_endpoint.replace("wss://", "https://"),
        )

    message_json = json.dumps(message, default=str)
    message_bytes = message_json.encode("utf-8")

    successful = 0
    failed = 0

    for connection_id in connection_ids:
        try:
            apigateway_management.post_to_connection(
                ConnectionId=connection_id,
                Data=message_bytes,
            )
            successful += 1
            print(f"Sent to connection {connection_id}")

        except apigateway_management.exceptions.GoneException:
            # Connection is stale - remove from database
            print(f"Connection {connection_id} is gone - removing")
            remove_stale_connection(connection_id)
            failed += 1

        except Exception as e:
            print(f"Error sending to connection {connection_id}: {str(e)}")
            failed += 1

    print(f"Broadcast complete: {successful} successful, {failed} failed")


def remove_stale_connection(connection_id):
    """Remove stale connection from DynamoDB."""
    try:
        tracking_table.delete_item(Key={"connection_id": connection_id})
        print(f"Removed stale connection {connection_id}")
    except Exception as e:
        print(f"Error removing stale connection: {str(e)}")


def calculate_eta(delivery_id, current_lat, current_lng):
    """
    Calculate ETA based on distance to delivery destination.

    Returns string like "15 mins" or None if calculation fails.
    """
    try:
        # Get delivery details to find destination
        delivery_response = deliveries_table.get_item(Key={"delivery_id": delivery_id})
        delivery = delivery_response.get("Item")

        if not delivery:
            print(f"Delivery {delivery_id} not found for ETA calculation")
            return None

        # Get destination coordinates (customer location)
        # Note: Hardcoded for now - should come from delivery record
        dest_lat = 37.7749
        dest_lng = -122.4194

        # Calculate distance using Haversine formula
        distance_km = haversine(
            float(current_lat), float(current_lng),
            float(dest_lat), float(dest_lng)
        )

        # Estimate time at average speed of 30 km/h (0.5 km/min)
        eta_minutes = int(distance_km * 2)

        if eta_minutes < 1:
            return "< 1 min"
        elif eta_minutes == 1:
            return "1 min"
        else:
            return f"{eta_minutes} mins"

    except Exception as e:
        print(f"Error calculating ETA: {str(e)}")
        return None


def haversine(lat1, lon1, lat2, lon2):
    """
    Calculate distance between two points on Earth using Haversine formula.

    Returns distance in kilometers.
    """
    R = 6371  # Earth radius in kilometers

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


def handle_delivery_picked_up(detail):
    """
    Broadcast delivery picked up status to customer's WebSocket connection.

    Detail structure:
    {
        'delivery_id': '...',
        'order_id': '...',
        'status': 'PICKED_UP',
        'pickup_time': '...'
    }
    """
    order_id = detail.get("order_id")

    print(f"Broadcasting pickup status for order {order_id}")

    # Get order to find user_id (customer)
    try:
        order_response = orders_table.get_item(Key={"order_id": order_id})
        order = order_response.get("Item")

        if not order:
            print(f"Order {order_id} not found")
            return

        user_id = order.get("user_id")

        if not user_id:
            print(f"Order {order_id} has no user_id")
            return

        # Find customer's WebSocket connections
        from boto3.dynamodb.conditions import Attr
        response = tracking_table.scan(
            FilterExpression=Attr("user_id").eq(user_id),
            ProjectionExpression="connection_id",
        )

        connections = response.get("Items", [])
        connection_ids = [conn["connection_id"] for conn in connections]

        if not connection_ids:
            print(f"No active connections for customer {user_id}")
            return

        # Broadcast status update
        message = {
            "type": "status",
            "action": "status",
            "order_id": order_id,
            "delivery_id": detail.get("delivery_id"),
            "status": "PICKED_UP",
            "pickup_time": detail.get("pickup_time"),
        }

        broadcast_to_connections(connection_ids, message)
        print(f"Pickup status broadcast to {len(connection_ids)} connections")

    except Exception as e:
        print(f"Error broadcasting pickup status: {str(e)}")


def handle_delivery_completed(detail):
    """
    Broadcast delivery completed status to customer's WebSocket connection.

    Detail structure:
    {
        'delivery_id': '...',
        'order_id': '...',
        'status': 'DELIVERED',
        'delivery_time': '...'
    }
    """
    order_id = detail.get("order_id")

    print(f"Broadcasting completion status for order {order_id}")

    # Get order to find user_id (customer)
    try:
        order_response = orders_table.get_item(Key={"order_id": order_id})
        order = order_response.get("Item")

        if not order:
            print(f"Order {order_id} not found")
            return

        user_id = order.get("user_id")

        if not user_id:
            print(f"Order {order_id} has no user_id")
            return

        # Find customer's WebSocket connections
        from boto3.dynamodb.conditions import Attr
        response = tracking_table.scan(
            FilterExpression=Attr("user_id").eq(user_id),
            ProjectionExpression="connection_id",
        )

        connections = response.get("Items", [])
        connection_ids = [conn["connection_id"] for conn in connections]

        if not connection_ids:
            print(f"No active connections for customer {user_id}")
            return

        # Broadcast status update
        message = {
            "type": "status",
            "action": "status",
            "order_id": order_id,
            "delivery_id": detail.get("delivery_id"),
            "status": "DELIVERED",
            "delivery_time": detail.get("delivery_time"),
        }

        broadcast_to_connections(connection_ids, message)
        print(f"Completion status broadcast to {len(connection_ids)} connections")

    except Exception as e:
        print(f"Error broadcasting completion status: {str(e)}")
