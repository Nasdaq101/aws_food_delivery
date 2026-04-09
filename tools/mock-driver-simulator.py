#!/usr/bin/env python3
"""
Mock Driver Simulator for Demo

This script simulates a driver accepting an order and moving from
the restaurant to the customer location, publishing location updates
via EventBridge for real-time tracking.

Usage:
    python mock-driver-simulator.py --delivery-id <delivery-id> --driver-id <driver-id>

Requirements:
    boto3

Example:
    python mock-driver-simulator.py --delivery-id abc123 --driver-id driver1
"""

import argparse
import time
import json
import boto3
from datetime import datetime, timezone
from math import radians, cos, sin, sqrt, atan2

# AWS Configuration
REGION = "us-west-1"
EVENT_BUS_NAME = "FoodDeliveryEventBus"

# Initialize boto3 clients
events = boto3.client("events", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)

# Restaurant location (hardcoded - matches order-service)
RESTAURANT_LAT = 37.7849
RESTAURANT_LNG = -122.4094

# Customer location (hardcoded - matches order-service)
CUSTOMER_LAT = 37.7749
CUSTOMER_LNG = -122.4194


def haversine(lat1, lon1, lat2, lon2):
    """
    Calculate distance between two points on Earth using Haversine formula.

    Args:
        lat1, lon1: Latitude and longitude of point 1
        lat2, lon2: Latitude and longitude of point 2

    Returns:
        Distance in kilometers
    """
    R = 6371  # Earth radius in kilometers

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


def calculate_eta(current_lat, current_lng, dest_lat, dest_lng):
    """
    Calculate ETA based on distance.

    Assumes average speed of 30 km/h (0.5 km/min).

    Returns:
        String like "15 mins" or "< 1 min"
    """
    distance_km = haversine(current_lat, current_lng, dest_lat, dest_lng)
    eta_minutes = int(distance_km * 2)  # 30 km/h = 0.5 km/min

    if eta_minutes < 1:
        return "< 1 min"
    elif eta_minutes == 1:
        return "1 min"
    else:
        return f"{eta_minutes} mins"


def publish_location_update(delivery_id, driver_id, lat, lng, eta):
    """
    Publish driver location update to EventBridge.

    Args:
        delivery_id: Delivery ID
        driver_id: Driver ID
        lat: Current latitude
        lng: Current longitude
        eta: Estimated time of arrival string
    """
    try:
        events.put_events(
            Entries=[
                {
                    "Source": "fooddelivery.driver",
                    "DetailType": "DriverLocationUpdate",
                    "Detail": json.dumps(
                        {
                            "delivery_id": delivery_id,
                            "driver_id": driver_id,
                            "lat": lat,
                            "lng": lng,
                            "eta": eta,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                    "EventBusName": EVENT_BUS_NAME,
                }
            ]
        )
        print(f"Published location update: lat={lat:.4f}, lng={lng:.4f}, eta={eta}")
    except Exception as e:
        print(f"Error publishing location update: {str(e)}")
        raise


def update_delivery_status(delivery_id, status):
    """
    Update delivery status in DynamoDB.

    Args:
        delivery_id: Delivery ID
        status: New status (e.g., "completed")
    """
    try:
        deliveries_table = dynamodb.Table("FoodDelivery-Deliveries")
        deliveries_table.update_item(
            Key={"delivery_id": delivery_id},
            UpdateExpression="SET #st = :st, updated_at = :u",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":st": status,
                ":u": datetime.now(timezone.utc).isoformat(),
            },
        )
        print(f"Updated delivery {delivery_id} status to {status}")
    except Exception as e:
        print(f"Error updating delivery status: {str(e)}")
        # Don't raise - this is not critical for demo


def simulate_delivery(delivery_id, driver_id):
    """
    Simulate driver movement from restaurant to customer.

    Publishes 10 location updates over 2 minutes (12 seconds between updates).

    Args:
        delivery_id: Delivery ID
        driver_id: Driver ID
    """
    print(f"\n{'=' * 60}")
    print(f"Mock Driver Simulator")
    print(f"{'=' * 60}")
    print(f"Delivery ID: {delivery_id}")
    print(f"Driver ID: {driver_id}")
    print(f"Route: Restaurant ({RESTAURANT_LAT}, {RESTAURANT_LNG}) -> Customer ({CUSTOMER_LAT}, {CUSTOMER_LNG})")
    print(f"{'=' * 60}\n")

    steps = 10
    delay_seconds = 12  # 12 seconds between updates = 2 minutes total

    for i in range(steps + 1):
        # Linear interpolation between restaurant and customer
        progress = i / steps
        current_lat = RESTAURANT_LAT + (CUSTOMER_LAT - RESTAURANT_LAT) * progress
        current_lng = RESTAURANT_LNG + (CUSTOMER_LNG - RESTAURANT_LNG) * progress

        # Calculate ETA
        eta = calculate_eta(current_lat, current_lng, CUSTOMER_LAT, CUSTOMER_LNG)

        # Publish location update
        publish_location_update(delivery_id, driver_id, current_lat, current_lng, eta)

        # Print progress
        print(f"Step {i}/{steps}: Progress={progress*100:.0f}%")

        # Wait before next update (except for last step)
        if i < steps:
            time.sleep(delay_seconds)

    # Mark delivery as completed
    print("\nDelivery completed! Updating status...")
    update_delivery_status(delivery_id, "completed")

    print(f"\n{'=' * 60}")
    print(f"Simulation Complete!")
    print(f"{'=' * 60}\n")


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Mock Driver Simulator for food delivery demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mock-driver-simulator.py --delivery-id abc123 --driver-id driver1
  python mock-driver-simulator.py -d abc123 -r driver1
        """,
    )
    parser.add_argument(
        "--delivery-id",
        "-d",
        required=True,
        help="Delivery ID to simulate",
    )
    parser.add_argument(
        "--driver-id",
        "-r",
        required=True,
        help="Driver ID simulating the delivery",
    )

    args = parser.parse_args()

    try:
        simulate_delivery(args.delivery_id, args.driver_id)
    except KeyboardInterrupt:
        print("\n\nSimulation interrupted by user")
    except Exception as e:
        print(f"\n\nError: {str(e)}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
