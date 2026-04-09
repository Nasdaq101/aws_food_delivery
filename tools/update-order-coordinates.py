#!/usr/bin/env python3
"""
Quick script to geocode and update existing order with coordinates
"""
import json
import boto3
from urllib.parse import quote
from urllib.request import Request, urlopen
import time
import ssl

REGION = "us-west-1"
dynamodb = boto3.resource("dynamodb", region_name=REGION)

orders_table = dynamodb.Table("FoodDelivery-Orders")
restaurants_table = dynamodb.Table("FoodDelivery-Restaurants")


def geocode_address(address):
    """Geocode address using OpenStreetMap Nominatim"""
    try:
        time.sleep(1)  # Rate limiting
        encoded_address = quote(address)
        url = f"https://nominatim.openstreetmap.org/search?q={encoded_address}&format=json&limit=1"
        req = Request(url, headers={'User-Agent': 'FoodDeliveryApp/1.0'})

        # Disable SSL verification (for development only)
        context = ssl._create_unverified_context()
        with urlopen(req, timeout=5, context=context) as response:
            data = json.loads(response.read().decode())
            if data and len(data) > 0:
                lat = data[0].get('lat')
                lng = data[0].get('lon')
                if lat and lng:
                    return f"{lat},{lng}"
        return None
    except Exception as e:
        print(f"Geocoding error: {e}")
        return None


def main():
    order_id = "ed1392a9-ca6e-49e8-a5b7-ac47dbfedeee"
    restaurant_id = "rest_003"

    # Get restaurant address
    print(f"Fetching restaurant {restaurant_id}...")
    rest = restaurants_table.get_item(Key={"restaurant_id": restaurant_id})["Item"]
    rest_address = rest.get("address")

    if not rest_address:
        print("ERROR: Restaurant has no address")
        return 1

    print(f"Restaurant address: {rest_address}")

    # Geocode restaurant
    print("Geocoding restaurant address...")
    rest_coords = geocode_address(rest_address)
    if not rest_coords:
        print("ERROR: Failed to geocode restaurant")
        return 1

    print(f"Restaurant coordinates: {rest_coords}")

    # Use a default customer location in SF
    customer_coords = "37.7749,-122.4194"
    print(f"Customer coordinates (default): {customer_coords}")

    # Update the order
    print(f"\nUpdating order {order_id}...")
    orders_table.update_item(
        Key={"order_id": order_id},
        UpdateExpression="SET restaurant_location = :rl, delivery_address = :da",
        ExpressionAttributeValues={
            ":rl": rest_coords,
            ":da": customer_coords
        }
    )

    # Also cache the restaurant coordinates
    print(f"Caching coordinates in restaurant table...")
    restaurants_table.update_item(
        Key={"restaurant_id": restaurant_id},
        UpdateExpression="SET #loc = :loc",
        ExpressionAttributeNames={"#loc": "location"},
        ExpressionAttributeValues={":loc": rest_coords}
    )

    print("\n✅ Order updated successfully!")
    print(f"   Restaurant: {rest_coords}")
    print(f"   Customer: {customer_coords}")
    return 0


if __name__ == "__main__":
    exit(main())
