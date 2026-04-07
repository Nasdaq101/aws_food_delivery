#!/usr/bin/env python3
"""
WebSocket Driver Simulator for Real-Time Tracking Demo

This script simulates a driver moving from restaurant to customer,
sending location updates via WebSocket for real-time map tracking.

Usage:
    python websocket-driver-simulator.py --order-id <order-id> --delivery-id <delivery-id>

Requirements:
    pip install websocket-client boto3 requests

Example:
    python websocket-driver-simulator.py --order-id ed1392a9 --delivery-id ef67af3c
"""

import argparse
import time
import json
import boto3
import websocket
import requests
from math import radians, cos, sin, sqrt, atan2
from datetime import datetime

# AWS Configuration
REGION = "us-west-1"

# Get WebSocket URL from CloudFormation
def get_websocket_url():
    """Get WebSocket URL from CloudFormation exports"""
    cfn = boto3.client('cloudformation', region_name=REGION)
    try:
        exports = cfn.list_exports()
        for export in exports.get('Exports', []):
            if export['Name'] == 'FoodDeliveryWebSocketUrl':
                return export['Value']
        raise Exception("WebSocket URL export not found")
    except Exception as e:
        print(f"Error getting WebSocket URL: {e}")
        print("Using default URL...")
        return "wss://5rgwaj8yp2.execute-api.us-west-1.amazonaws.com/prod"

# Get auth token (you'll need to provide this)
def get_auth_token():
    """
    Get authentication token.
    You need to login first and copy the token from browser localStorage.
    """
    print("\n" + "="*60)
    print("AUTHENTICATION REQUIRED")
    print("="*60)
    print("1. Open your browser and login to the food delivery app")
    print("2. Open Developer Console (F12)")
    print("3. Go to Application/Storage -> Local Storage")
    print("4. Find and copy your auth token")
    print("5. Paste it here:")
    print("="*60 + "\n")

    token = input("Enter your auth token: ").strip()
    if not token:
        raise Exception("No auth token provided")
    return token

# Default locations (San Francisco area) - used as fallback
DEFAULT_RESTAURANT_LAT = 37.7849
DEFAULT_RESTAURANT_LNG = -122.4094
DEFAULT_CUSTOMER_LAT = 37.7749
DEFAULT_CUSTOMER_LNG = -122.4194


def get_order_locations(order_id, auth_token):
    """
    Fetch order details from API and extract restaurant and customer locations.
    Returns: (restaurant_lat, restaurant_lng, customer_lat, customer_lng)
    """
    # Get API URL from CloudFormation
    cfn = boto3.client('cloudformation', region_name=REGION)
    api_url = None
    try:
        exports = cfn.list_exports()
        for export in exports.get('Exports', []):
            if 'ApiUrl' in export['Name'] or 'ApiEndpoint' in export['Name']:
                api_url = export['Value'].rstrip('/')
                break
    except Exception as e:
        print(f"Warning: Could not get API URL from CloudFormation: {e}")

    if not api_url:
        api_url = "https://mhzg95of4f.execute-api.us-west-1.amazonaws.com/prod"
        print(f"Using default API URL: {api_url}")

    # Fetch order details
    try:
        print(f"Fetching order details from API for order: {order_id}")
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json"
        }
        response = requests.get(f"{api_url}/orders/{order_id}", headers=headers)
        response.raise_for_status()

        order = response.json().get('order', {})

        # Parse restaurant location (format: "lat,lng")
        restaurant_loc = order.get('restaurant_location', '')
        if restaurant_loc and ',' in restaurant_loc:
            parts = restaurant_loc.split(',')
            restaurant_lat = float(parts[0].strip())
            restaurant_lng = float(parts[1].strip())
        else:
            print(f"Warning: Invalid restaurant_location: {restaurant_loc}, using defaults")
            restaurant_lat = DEFAULT_RESTAURANT_LAT
            restaurant_lng = DEFAULT_RESTAURANT_LNG

        # Parse delivery address (format: "lat,lng")
        delivery_addr = order.get('delivery_address', '')
        if delivery_addr and ',' in delivery_addr:
            parts = delivery_addr.split(',')
            customer_lat = float(parts[0].strip())
            customer_lng = float(parts[1].strip())
        else:
            print(f"Warning: Invalid delivery_address: {delivery_addr}, using defaults")
            customer_lat = DEFAULT_CUSTOMER_LAT
            customer_lng = DEFAULT_CUSTOMER_LNG

        print(f"✓ Restaurant: ({restaurant_lat:.6f}, {restaurant_lng:.6f})")
        print(f"✓ Customer: ({customer_lat:.6f}, {customer_lng:.6f})")

        return restaurant_lat, restaurant_lng, customer_lat, customer_lng

    except Exception as e:
        print(f"Error fetching order details: {e}")
        print(f"Using default locations")
        return DEFAULT_RESTAURANT_LAT, DEFAULT_RESTAURANT_LNG, DEFAULT_CUSTOMER_LAT, DEFAULT_CUSTOMER_LNG


def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance between two points using Haversine formula (km)"""
    R = 6371  # Earth radius in kilometers
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def calculate_eta(current_lat, current_lng, dest_lat, dest_lng):
    """Calculate ETA based on distance (assumes 30 km/h average speed)"""
    distance_km = haversine(current_lat, current_lng, dest_lat, dest_lng)
    eta_minutes = int(distance_km * 2)  # 30 km/h = 0.5 km/min

    if eta_minutes < 1:
        return "< 1"
    else:
        return str(eta_minutes)


def simulate_driver(delivery_id, order_id, auth_token):
    """
    Simulate driver movement from restaurant to customer.
    Sends location updates via WebSocket every 5 seconds.
    """
    ws_url = get_websocket_url()

    # Fetch actual restaurant and customer locations from order
    restaurant_lat, restaurant_lng, customer_lat, customer_lng = get_order_locations(order_id, auth_token)

    # Driver route: Restaurant (pickup) → Customer (delivery)
    start_lat, start_lng = restaurant_lat, restaurant_lng
    end_lat, end_lng = customer_lat, customer_lng

    # Add auth token to WebSocket URL
    ws_url_with_auth = f"{ws_url}?token={auth_token}"

    print(f"\n{'=' * 60}")
    print(f"WebSocket Driver Simulator")
    print(f"{'=' * 60}")
    print(f"Order ID: {order_id}")
    print(f"Delivery ID: {delivery_id}")
    print(f"WebSocket URL: {ws_url}")
    print(f"Route: Restaurant ({start_lat:.4f}, {start_lng:.4f})")
    print(f"    -> Customer ({end_lat:.4f}, {end_lng:.4f})")
    print(f"{'=' * 60}\n")

    # Connect to WebSocket
    print("Connecting to WebSocket...")
    import ssl
    ws = websocket.create_connection(
        ws_url_with_auth,
        sslopt={"cert_reqs": ssl.CERT_NONE}  # Disable SSL verification for testing
    )
    print("✓ Connected!\n")

    # Simulate 10 steps from restaurant to customer
    steps = 10
    delay_seconds = 5  # 5 seconds between updates

    try:
        for i in range(steps + 1):
            # Linear interpolation between start and end
            progress = i / steps
            current_lat = start_lat + (end_lat - start_lat) * progress
            current_lng = start_lng + (end_lng - start_lng) * progress

            # Calculate ETA (distance remaining to end point)
            eta = calculate_eta(current_lat, current_lng, end_lat, end_lng)

            # Send location update via WebSocket
            message = {
                "action": "sendLocation",
                "delivery_id": delivery_id,
                "lat": round(current_lat, 6),
                "lng": round(current_lng, 6),
                "eta": eta
            }

            ws.send(json.dumps(message))

            # Print progress
            print(f"📍 Step {i+1}/{steps+1}: Progress={progress*100:.0f}%, "
                  f"Location=({current_lat:.4f}, {current_lng:.4f}), "
                  f"ETA={eta} min")

            # Wait before next update (except for last step)
            if i < steps:
                time.sleep(delay_seconds)

        print(f"\n{'=' * 60}")
        print(f"🎉 Delivery Complete!")
        print(f"{'=' * 60}\n")

    except KeyboardInterrupt:
        print("\n\nSimulation interrupted by user")
    finally:
        ws.close()
        print("WebSocket closed")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="WebSocket Driver Simulator for real-time tracking demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python websocket-driver-simulator.py --order-id ed1392a9 --delivery-id ef67af3c
  python websocket-driver-simulator.py -o ed1392a9 -d ef67af3c --token YOUR_TOKEN

Note:
  - You need to be logged in and provide your auth token.
  - The script will automatically fetch restaurant and customer locations from the order.
  - Use the short form of delivery_id (first 8 characters).
        """
    )
    parser.add_argument(
        "--order-id", "-o",
        required=True,
        help="Order ID to simulate (used to fetch restaurant and customer locations)"
    )
    parser.add_argument(
        "--delivery-id", "-d",
        required=True,
        help="Delivery ID to simulate (short form, e.g., ef67af3c)"
    )
    parser.add_argument(
        "--token", "-t",
        help="Authentication token (will prompt if not provided)"
    )

    args = parser.parse_args()

    try:
        # Get auth token
        auth_token = args.token or get_auth_token()

        # Run simulation
        simulate_driver(args.delivery_id, args.order_id, auth_token)

    except KeyboardInterrupt:
        print("\n\nSimulation interrupted by user")
        return 1
    except Exception as e:
        print(f"\n\nError: {str(e)}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
