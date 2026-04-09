import json
import os
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from math import radians, cos, sin, sqrt, atan2
from urllib.request import Request, urlopen
from urllib.parse import quote

import boto3

dynamodb = boto3.resource("dynamodb")
events_client = boto3.client("events")
stepfunctions = boto3.client("stepfunctions")
lambda_client = boto3.client("lambda")

DELIVERIES_TABLE = os.environ.get("DELIVERIES_TABLE", "FoodDelivery-Deliveries")
DRIVERS_TABLE = os.environ.get("DRIVERS_TABLE", "FoodDelivery-Drivers")
DRIVER_OFFERS_TABLE = os.environ.get("DRIVER_OFFERS_TABLE", "FoodDelivery-DriverOffers")
ORDERS_TABLE = os.environ.get("ORDERS_TABLE", "FoodDelivery-Orders")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")

deliveries_table = dynamodb.Table(DELIVERIES_TABLE)
drivers_table = dynamodb.Table(DRIVERS_TABLE)
driver_offers_table = dynamodb.Table(DRIVER_OFFERS_TABLE)
orders_table = dynamodb.Table(ORDERS_TABLE)


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _method_path(event):
    rc = event.get("requestContext") or {}
    if "http" in rc:
        return rc["http"]["method"], event.get("rawPath") or event.get("path", "")
    return event.get("httpMethod", "GET"), event.get("path", "")


def _path_segments(path):
    return [s for s in (path or "").rstrip("/").split("/") if s]


def _emit_delivery_event(detail_type, detail):
    try:
        events_client.put_events(
            Entries=[
                {
                    "Source": "fooddelivery.delivery",
                    "DetailType": detail_type,
                    "Detail": json.dumps(detail, default=str),
                    "EventBusName": EVENT_BUS_NAME,
                }
            ]
        )
    except Exception:
        pass


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def lambda_handler(event, context):
    try:
        # Check if this is a Step Functions invocation
        if "action" in event:
            action = event.get("action")
            if action == "create":
                return _create_delivery(event)
            elif action == "assign":
                return _assign_driver(event)
            elif action == "create_offer":
                return _create_driver_offer(event)
            elif action == "finalize_assignment":
                return _finalize_assignment(event)
            else:
                return {"error": "Unknown action", "action": action}

        # Otherwise, handle HTTP API calls
        method, path = _method_path(event)

        # POST /deliveries/offers/{offer_id}/respond
        offer_match = re.match(r"^/deliveries/offers/([^/]+)/respond$", path)
        if offer_match and method == "POST":
            offer_id = offer_match.group(1)
            driver_id = event.get("requestContext", {}).get("authorizer", {}).get("claims", {}).get("sub")
            body = json.loads(event.get("body", "{}"))
            result = _handle_driver_response({
                "offer_id": offer_id,
                "driver_id": driver_id,
                "action": body.get("action"),
            })
            return response(result.get("status", 200), result)

        # PATCH /deliveries/{delivery_id}/pickup
        pickup_match = re.match(r"^/deliveries/([^/]+)/pickup$", path)
        if pickup_match and method == "PATCH":
            delivery_id = pickup_match.group(1)
            driver_id = event.get("requestContext", {}).get("authorizer", {}).get("claims", {}).get("sub")
            return _handle_pickup({
                "delivery_id": delivery_id,
                "driver_id": driver_id,
            })

        # PATCH /deliveries/{delivery_id}/complete
        complete_match = re.match(r"^/deliveries/([^/]+)/complete$", path)
        if complete_match and method == "PATCH":
            delivery_id = complete_match.group(1)
            driver_id = event.get("requestContext", {}).get("authorizer", {}).get("claims", {}).get("sub")
            return _handle_complete({
                "delivery_id": delivery_id,
                "driver_id": driver_id,
            })

        # GET endpoints only from here
        if method != "GET":
            return response(405, {"error": "Method not allowed"})

        segs = _path_segments(path)
        try:
            idx = segs.index("deliveries")
        except ValueError:
            return response(404, {"error": "Not found", "path": path})

        if idx == len(segs) - 1:
            return _list_deliveries()
        if idx == len(segs) - 2:
            return _get_delivery(segs[idx + 1], event)

        return response(404, {"error": "Not found", "path": path})
    except Exception as e:
        return response(500, {"error": str(e)})


def _list_deliveries():
    try:
        scan = deliveries_table.scan(Limit=50)
        items = scan.get("Items", [])
        return response(200, {"deliveries": items, "count": len(items)})
    except Exception as e:
        return response(500, {"error": str(e)})


def _ensure_driver_assigned(item, event):
    """If no driver, pick first available driver and update delivery."""
    if item.get("driver_id"):
        return item
    try:
        dscan = drivers_table.scan(
            FilterExpression="#s = :avail",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":avail": "available"},
            Limit=10,
        )
        drivers = dscan.get("Items") or []
        if not drivers:
            return item
        driver = drivers[0]
        driver_id = driver.get("driver_id")
        if not driver_id:
            return item
        now = _utc_now_iso()
        deliveries_table.update_item(
            Key={"delivery_id": item["delivery_id"]},
            UpdateExpression="SET driver_id = :d, #st = :st, updated_at = :u",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":d": driver_id,
                ":st": "assigned",
                ":u": now,
            },
            ReturnValues="ALL_NEW",
        )
        drivers_table.update_item(
            Key={"driver_id": driver_id},
            UpdateExpression="SET #s = :busy, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":busy": "busy", ":u": now},
        )
        _emit_delivery_event(
            "DriverAssigned",
            {"delivery_id": item["delivery_id"], "driver_id": driver_id},
        )
        res = deliveries_table.get_item(Key={"delivery_id": item["delivery_id"]})
        return res.get("Item", item)
    except Exception:
        return item


def _get_delivery(delivery_id, event):
    try:
        res = deliveries_table.get_item(Key={"delivery_id": delivery_id})
        item = res.get("Item")
        if not item:
            return response(404, {"error": "Delivery not found"})
        item = _ensure_driver_assigned(item, event)
        return response(200, item)
    except Exception as e:
        return response(500, {"error": str(e)})


def _create_delivery(event):
    """Called by Step Functions to create a delivery record"""
    order_id = event.get("order_id")
    restaurant_id = event.get("restaurant_id")
    customer_address = event.get("customer_address")

    if not order_id or not restaurant_id:
        return {"error": "order_id and restaurant_id are required"}

    delivery_id = str(uuid.uuid4())
    now = _utc_now_iso()

    item = {
        "delivery_id": delivery_id,
        "order_id": order_id,
        "restaurant_id": restaurant_id,
        "customer_address": customer_address or "Unknown address",
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }

    try:
        deliveries_table.put_item(Item=item)
        return {
            "success": True,
            "delivery_id": delivery_id,
            "status": "pending"
        }
    except Exception as e:
        return {"error": f"Failed to create delivery: {str(e)}"}


def _assign_driver(event):
    """Called by Step Functions to assign a driver to a delivery"""
    delivery_id = event.get("delivery_id")
    drivers = event.get("drivers", [])

    if not delivery_id:
        return {"error": "delivery_id is required"}

    if not drivers or len(drivers) == 0:
        return {"error": "No available drivers", "assigned": False}

    # Pick the first available driver
    driver = drivers[0]
    driver_id = driver.get("driver_id")

    if not driver_id:
        return {"error": "Invalid driver data", "assigned": False}

    now = _utc_now_iso()

    try:
        # Update delivery with driver assignment
        deliveries_table.update_item(
            Key={"delivery_id": delivery_id},
            UpdateExpression="SET driver_id = :d, #st = :st, updated_at = :u",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":d": driver_id,
                ":st": "assigned",
                ":u": now,
            },
        )

        # Update driver status to busy
        drivers_table.update_item(
            Key={"driver_id": driver_id},
            UpdateExpression="SET #s = :busy, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":busy": "busy", ":u": now},
        )

        _emit_delivery_event(
            "DriverAssigned",
            {
                "delivery_id": delivery_id,
                "driver_id": driver_id,
                "driver_name": driver.get("name")
            }
        )

        return {
            "success": True,
            "assigned": True,
            "driver_id": driver_id,
            "driver_name": driver.get("name")
        }
    except Exception as e:
        return {"error": f"Failed to assign driver: {str(e)}", "assigned": False}


def _create_driver_offer(event):
    """
    Called by Step Functions to create a driver offer and notify driver.
    CRITICAL: Stores the task token for later callback.
    This function does NOT return - Step Functions waits for callback.
    """
    delivery_id = event.get("delivery_id")
    order_id = event.get("order_id")
    driver = event.get("driver", {})
    driver_id = driver.get("driver_id")
    task_token = event.get("task_token")
    restaurant_location = event.get("restaurant_location", {})
    delivery_address = event.get("delivery_address", {})

    if not all([delivery_id, driver_id, task_token]):
        return {"error": "Missing required fields", "status": "error"}

    offer_id = str(uuid.uuid4())
    now = _utc_now_iso()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)
    ttl = int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp())

    # Get order total to calculate payout
    order_total = Decimal("0.00")
    if order_id:
        try:
            orders_table = dynamodb.Table(os.environ.get("ORDERS_TABLE", "FoodDelivery-Orders"))
            order_response = orders_table.get_item(Key={"order_id": order_id})
            order = order_response.get("Item")
            if order and "total" in order:
                order_total = Decimal(str(order["total"]))
                print(f"Order total: ${order_total}")
        except Exception as e:
            print(f"Error fetching order total: {str(e)}")

    # Calculate payout estimate (25% of order total + $3 base fee)
    distance_km = Decimal(str(_calculate_distance(restaurant_location, delivery_address)))
    base_fee = Decimal("3.00")
    percentage_fee = order_total * Decimal("0.25")  # 25% of order total
    estimated_payout = base_fee + percentage_fee

    print(f"Payout calculation: base=${base_fee}, 25% of ${order_total}=${percentage_fee}, total=${estimated_payout}")

    # Parse restaurant location coordinates and address
    restaurant_lat, restaurant_lng, restaurant_addr = None, None, None
    if isinstance(restaurant_location, dict):
        restaurant_lat = restaurant_location.get("lat")
        restaurant_lng = restaurant_location.get("lng")
        restaurant_addr = restaurant_location.get("address")

        # If address is just coordinates, try to reverse geocode
        if restaurant_addr and "," in restaurant_addr and restaurant_addr.count(",") == 1:
            try:
                float(restaurant_addr.split(",")[0])
                # It's just coordinates, try reverse geocoding
                reverse_addr = _reverse_geocode(restaurant_lat, restaurant_lng)
                if reverse_addr:
                    restaurant_addr = reverse_addr
                else:
                    restaurant_addr = f"{restaurant_lat},{restaurant_lng}"
            except:
                pass
    elif isinstance(restaurant_location, str):
        if "," in restaurant_location:
            try:
                restaurant_lat, restaurant_lng = restaurant_location.split(',')
                # Try reverse geocoding for coordinate strings
                reverse_addr = _reverse_geocode(float(restaurant_lat), float(restaurant_lng))
                restaurant_addr = reverse_addr if reverse_addr else restaurant_location
            except:
                restaurant_addr = restaurant_location

    # Parse delivery address coordinates and address
    delivery_lat, delivery_lng, delivery_addr = None, None, None
    if isinstance(delivery_address, dict):
        delivery_lat = delivery_address.get("lat")
        delivery_lng = delivery_address.get("lng")
        delivery_addr = delivery_address.get("address")

        # If address is just coordinates, try to reverse geocode
        if delivery_addr and "," in delivery_addr and delivery_addr.count(",") == 1:
            try:
                float(delivery_addr.split(",")[0])
                # It's just coordinates, try reverse geocoding
                reverse_addr = _reverse_geocode(delivery_lat, delivery_lng)
                if reverse_addr:
                    delivery_addr = reverse_addr
                else:
                    delivery_addr = f"{delivery_lat},{delivery_lng}"
            except:
                pass
    elif isinstance(delivery_address, str):
        if "," in delivery_address:
            try:
                delivery_lat, delivery_lng = delivery_address.split(',')
                # Try reverse geocoding for coordinate strings
                reverse_addr = _reverse_geocode(float(delivery_lat), float(delivery_lng))
                delivery_addr = reverse_addr if reverse_addr else delivery_address
            except:
                delivery_addr = delivery_address
        else:
            delivery_addr = delivery_address

    offer = {
        "offer_id": offer_id,
        "delivery_id": delivery_id,
        "order_id": order_id,
        "driver_id": driver_id,
        "task_token": task_token,
        "status": "pending",
        "offer_details": {
            "restaurant_name": "Restaurant",
            "pickup_address": restaurant_addr or "Unknown",
            "delivery_address": delivery_addr or "Unknown",
            "restaurant_lat": str(restaurant_lat) if restaurant_lat else None,
            "restaurant_lng": str(restaurant_lng) if restaurant_lng else None,
            "delivery_lat": str(delivery_lat) if delivery_lat else None,
            "delivery_lng": str(delivery_lng) if delivery_lng else None,
            "estimated_distance_km": distance_km,  # Keep as Decimal for DynamoDB
            "estimated_payout": estimated_payout,  # Keep as Decimal for DynamoDB
        },
        "created_at": now,
        "expires_at": expires_at.isoformat(),
        "ttl": ttl,
    }

    try:
        # Store offer in DynamoDB
        driver_offers_table.put_item(Item=offer)

        # Emit EventBridge event for WebSocket broadcast
        _emit_driver_offer_event(offer)

        # Return immediately - Step Functions waits for callback via task token
        # The workflow will resume when driver accepts/rejects via HTTP endpoint
        return {
            "status": "offer_sent",
            "offer_id": offer_id,
            "driver_id": driver_id,
        }

    except Exception as e:
        print(f"Error creating driver offer: {str(e)}")
        return {"error": str(e), "status": "error"}


def _handle_driver_response(event):
    """
    HTTP endpoint: POST /deliveries/offers/{offer_id}/respond
    Body: { "action": "accept" | "reject" }

    This function:
    1. Validates the offer exists and is pending
    2. Updates offer status
    3. Calls Step Functions SendTaskSuccess/SendTaskFailure with task token
    4. Returns immediate response to driver
    """
    offer_id = event.get("offer_id")
    driver_id = event.get("driver_id")
    action = event.get("action")

    if action not in ["accept", "reject"]:
        return {"error": "Invalid action", "status": 400}

    try:
        # Get offer
        offer_response = driver_offers_table.get_item(Key={"offer_id": offer_id})
        offer = offer_response.get("Item")

        if not offer:
            return {"error": "Offer not found", "status": 404}

        # Validate driver owns this offer
        if offer.get("driver_id") != driver_id:
            return {"error": "Unauthorized", "status": 403}

        # Check offer is still pending
        if offer.get("status") != "pending":
            return {"error": f"Offer already {offer.get('status')}", "status": 409}

        # Check not expired
        expires_at = datetime.fromisoformat(offer["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            return {"error": "Offer expired", "status": 410}

        # Update offer status
        new_status = "accepted" if action == "accept" else "rejected"
        now = _utc_now_iso()

        driver_offers_table.update_item(
            Key={"offer_id": offer_id},
            UpdateExpression="SET #s = :status, responded_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": new_status, ":now": now},
        )

        # Send callback to Step Functions
        task_token = offer["task_token"]

        if action == "accept":
            # Get delivery_id from offer
            delivery_id = offer.get("delivery_id")

            # Update driver status to busy
            drivers_table.update_item(
                Key={"driver_id": driver_id},
                UpdateExpression="SET #s = :busy, updated_at = :now",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":busy": "busy", ":now": now},
            )

            # IMPORTANT: Update delivery with driver_id immediately (before Step Functions callback)
            # This allows the driver to access the order right away
            if delivery_id:
                try:
                    deliveries_table.update_item(
                        Key={"delivery_id": delivery_id},
                        UpdateExpression="SET driver_id = :d, offer_id = :o, #st = :st, updated_at = :u",
                        ExpressionAttributeNames={"#st": "status"},
                        ExpressionAttributeValues={
                            ":d": driver_id,
                            ":o": offer_id,
                            ":st": "assigned",
                            ":u": now,
                        },
                    )
                    print(f"Updated delivery {delivery_id} with driver {driver_id}")
                except Exception as e:
                    print(f"Error updating delivery: {str(e)}")

            # Send success to Step Functions
            stepfunctions.send_task_success(
                taskToken=task_token,
                output=json.dumps({
                    "status": "accepted",
                    "offer_id": offer_id,
                    "driver_id": driver_id,
                    "responded_at": now,
                }, default=str)
            )
        else:
            # Send failure to Step Functions
            stepfunctions.send_task_failure(
                taskToken=task_token,
                error="DriverRejected",
                cause=f"Driver {driver_id} rejected the offer"
            )

        return {
            "success": True,
            "status": 200,
            "offer_id": offer_id,
            "action": new_status,
        }

    except Exception as e:
        print(f"Error handling driver response: {str(e)}")
        return {"error": str(e), "status": 500}


def _finalize_assignment(event):
    """
    Called by Step Functions after driver accepts offer.
    Updates delivery record with driver assignment.
    """
    delivery_id = event.get("delivery_id")
    offer_id = event.get("offer_id")
    driver_id = event.get("driver_id")

    if not all([delivery_id, driver_id]):
        return {"error": "Missing required fields"}

    now = _utc_now_iso()

    try:
        # Update delivery with driver assignment
        deliveries_table.update_item(
            Key={"delivery_id": delivery_id},
            UpdateExpression="SET driver_id = :d, offer_id = :o, #st = :st, updated_at = :u",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":d": driver_id,
                ":o": offer_id,
                ":st": "assigned",
                ":u": now,
            },
        )

        # Emit event for downstream services
        _emit_delivery_event(
            "DriverAssigned",
            {
                "delivery_id": delivery_id,
                "driver_id": driver_id,
                "offer_id": offer_id,
                "timestamp": now,
            }
        )

        return {
            "success": True,
            "delivery_id": delivery_id,
            "driver_id": driver_id,
            "status": "assigned",
        }

    except Exception as e:
        return {"error": f"Failed to finalize assignment: {str(e)}"}


def _calculate_distance(loc1, loc2):
    """Calculate distance between two locations in km using Haversine formula"""
    try:
        # Parse coordinates from various formats
        if isinstance(loc1, dict):
            lat1 = float(loc1.get("lat", 0))
            lng1 = float(loc1.get("lng", 0))
        elif isinstance(loc1, str) and "," in loc1:
            lat1, lng1 = map(float, loc1.split(','))
        else:
            return 5.0  # Default fallback (returns float, caller converts to Decimal)

        if isinstance(loc2, dict):
            lat2 = float(loc2.get("lat", 0))
            lng2 = float(loc2.get("lng", 0))
        elif isinstance(loc2, str) and "," in loc2:
            lat2, lng2 = map(float, loc2.split(','))
        elif isinstance(loc2, dict) and "address" in loc2:
            # delivery_address has nested structure
            lat2 = float(loc2.get("lat", 0))
            lng2 = float(loc2.get("lng", 0))
            if lat2 == 0 and lng2 == 0:
                # Try parsing from address string
                addr = loc2.get("address", "")
                if isinstance(addr, str) and "," in addr:
                    try:
                        lat2, lng2 = map(float, addr.split(','))
                    except:
                        return 5.0
                else:
                    return 5.0
        else:
            return 5.0

        # Haversine formula
        R = 6371  # Earth radius in km
        lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
        dlat = lat2 - lat1
        dlng = lng2 - lng1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlng/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        return R * c
    except Exception as e:
        print(f"Error calculating distance: {str(e)}")
        return 5.0  # Default fallback


def _emit_driver_offer_event(offer):
    """Emit EventBridge event to trigger WebSocket notification"""
    try:
        events_client.put_events(
            Entries=[{
                "Source": "fooddelivery.delivery",
                "DetailType": "DriverOfferCreated",
                "Detail": json.dumps({
                    "offer_id": offer["offer_id"],
                    "driver_id": offer["driver_id"],
                    "delivery_id": offer["delivery_id"],
                    "order_id": offer["order_id"],
                    "offer_details": offer["offer_details"],
                    "expires_at": offer["expires_at"],
                }, default=str),
                "EventBusName": EVENT_BUS_NAME,
            }]
        )
    except Exception as e:
        print(f"Error emitting driver offer event: {str(e)}")


def _handle_pickup(event):
    """Handle driver marking delivery as picked up"""
    delivery_id = event.get("delivery_id")
    driver_id = event.get("driver_id")

    if not delivery_id or not driver_id:
        return response(400, {"error": "Missing delivery_id or driver_id"})

    try:
        # Get delivery to verify driver
        delivery_response = deliveries_table.get_item(Key={"delivery_id": delivery_id})
        delivery = delivery_response.get("Item")

        if not delivery:
            return response(404, {"error": "Delivery not found"})

        if delivery.get("driver_id") != driver_id:
            return response(403, {"error": "Not authorized for this delivery"})

        # Get restaurant location to update driver position
        restaurant_location = delivery.get("restaurant_location")

        # Update driver location to restaurant location
        if restaurant_location and ',' in restaurant_location:
            try:
                lat, lng = restaurant_location.split(',')
                drivers_table.update_item(
                    Key={"driver_id": driver_id},
                    UpdateExpression="SET #loc = :loc, updated_at = :updated",
                    ExpressionAttributeNames={"#loc": "location"},
                    ExpressionAttributeValues={
                        ":loc": {"lat": lat.strip(), "lng": lng.strip()},
                        ":updated": _utc_now_iso(),
                    },
                )
                print(f"Updated driver location to restaurant: {restaurant_location}")

                # Emit DriverLocationUpdate event for WebSocket
                events_client.put_events(
                    Entries=[{
                        "Source": "fooddelivery.delivery",
                        "DetailType": "DriverLocationUpdate",
                        "Detail": json.dumps({
                            "delivery_id": delivery_id,
                            "driver_id": driver_id,
                            "lat": lat.strip(),
                            "lng": lng.strip(),
                            "timestamp": _utc_now_iso(),
                        }, default=str),
                        "EventBusName": EVENT_BUS_NAME,
                    }]
                )
            except Exception as e:
                print(f"Failed to update driver location: {str(e)}")

        # Update delivery status
        now = _utc_now_iso()
        deliveries_table.update_item(
            Key={"delivery_id": delivery_id},
            UpdateExpression="SET #s = :status, pickup_time = :time, updated_at = :updated",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": "PICKED_UP",
                ":time": now,
                ":updated": now,
            },
        )

        # Update order status to PICKED_UP
        order_id = delivery.get("order_id")
        user_id = None
        if order_id:
            try:
                # Get user_id from order for WebSocket notification
                order_response = orders_table.get_item(Key={"order_id": order_id})
                order = order_response.get("Item")
                user_id = order.get("user_id") if order else None

                orders_table.update_item(
                    Key={"order_id": order_id},
                    UpdateExpression="SET #s = :status, updated_at = :updated",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":status": "PICKED_UP",
                        ":updated": now,
                    },
                )

                # Emit OrderStatusChanged event for WebSocket notification
                event_detail = {
                    "order_id": order_id,
                    "status": "PICKED_UP",
                    "timestamp": now,
                }
                if user_id:
                    event_detail["user_id"] = user_id

                events_client.put_events(
                    Entries=[{
                        "Source": "fooddelivery.orders",
                        "DetailType": "OrderStatusChanged",
                        "Detail": json.dumps(event_detail, default=str),
                        "EventBusName": EVENT_BUS_NAME,
                    }]
                )
            except Exception as e:
                print(f"Failed to update order status: {str(e)}")

        # Emit event for WebSocket notification to customer (legacy)
        events_client.put_events(
            Entries=[{
                "Source": "fooddelivery.delivery",
                "DetailType": "DeliveryPickedUp",
                "Detail": json.dumps({
                    "delivery_id": delivery_id,
                    "order_id": order_id,
                    "status": "PICKED_UP",
                    "pickup_time": now,
                }, default=str),
                "EventBusName": EVENT_BUS_NAME,
            }]
        )

        # Wait a few seconds, then automatically transition to DELIVERING
        try:
            print(f"[PICKUP] Waiting 3 seconds before transitioning to DELIVERING...")
            time.sleep(3)
            print(f"[PICKUP] 3 seconds elapsed, now updating to DELIVERING")

            # Update to DELIVERING status
            now_delivering = _utc_now_iso()

            # Update delivery status
            deliveries_table.update_item(
                Key={"delivery_id": delivery_id},
                UpdateExpression="SET #s = :status, updated_at = :updated",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":status": "DELIVERING",
                    ":updated": now_delivering,
                },
            )
            print(f"[PICKUP] Delivery status updated to DELIVERING")

            # Update order status to DELIVERING
            if order_id:
                try:
                    orders_table.update_item(
                        Key={"order_id": order_id},
                        UpdateExpression="SET #s = :status, updated_at = :updated",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={
                            ":status": "DELIVERING",
                            ":updated": now_delivering,
                        },
                    )
                    print(f"[PICKUP] Order status updated to DELIVERING")

                    # Emit OrderStatusChanged event for WebSocket notification
                    event_detail = {
                        "order_id": order_id,
                        "status": "DELIVERING",
                        "timestamp": now_delivering,
                    }
                    if user_id:
                        event_detail["user_id"] = user_id

                    events_client.put_events(
                        Entries=[{
                            "Source": "fooddelivery.orders",
                            "DetailType": "OrderStatusChanged",
                            "Detail": json.dumps(event_detail, default=str),
                            "EventBusName": EVENT_BUS_NAME,
                        }]
                    )
                    print(f"[PICKUP] OrderStatusChanged event emitted for DELIVERING")
                except Exception as e:
                    print(f"[PICKUP ERROR] Failed to update order status to DELIVERING: {str(e)}")
                    import traceback
                    traceback.print_exc()
        except Exception as e:
            print(f"[PICKUP ERROR] Failed during auto-transition to DELIVERING: {str(e)}")
            import traceback
            traceback.print_exc()

        return response(200, {
            "message": "Delivery marked as picked up and now delivering",
            "delivery_id": delivery_id,
            "status": "DELIVERING",
        })
    except Exception as e:
        print(f"Error handling pickup: {str(e)}")
        return response(500, {"error": str(e)})


def _handle_complete(event):
    """Handle driver marking delivery as completed"""
    delivery_id = event.get("delivery_id")
    driver_id = event.get("driver_id")

    if not delivery_id or not driver_id:
        return response(400, {"error": "Missing delivery_id or driver_id"})

    try:
        # Get delivery to verify driver
        delivery_response = deliveries_table.get_item(Key={"delivery_id": delivery_id})
        delivery = delivery_response.get("Item")

        if not delivery:
            return response(404, {"error": "Delivery not found"})

        if delivery.get("driver_id") != driver_id:
            return response(403, {"error": "Not authorized for this delivery"})

        # Get delivery address to update driver position
        delivery_address = delivery.get("delivery_address")

        # Update driver location to delivery address
        if delivery_address and ',' in delivery_address:
            try:
                lat, lng = delivery_address.split(',')
                drivers_table.update_item(
                    Key={"driver_id": driver_id},
                    UpdateExpression="SET #loc = :loc, updated_at = :updated",
                    ExpressionAttributeNames={"#loc": "location"},
                    ExpressionAttributeValues={
                        ":loc": {"lat": lat.strip(), "lng": lng.strip()},
                        ":updated": _utc_now_iso(),
                    },
                )
                print(f"Updated driver location to delivery address: {delivery_address}")

                # Emit DriverLocationUpdate event for WebSocket
                events_client.put_events(
                    Entries=[{
                        "Source": "fooddelivery.delivery",
                        "DetailType": "DriverLocationUpdate",
                        "Detail": json.dumps({
                            "delivery_id": delivery_id,
                            "driver_id": driver_id,
                            "lat": lat.strip(),
                            "lng": lng.strip(),
                            "timestamp": _utc_now_iso(),
                        }, default=str),
                        "EventBusName": EVENT_BUS_NAME,
                    }]
                )
            except Exception as e:
                print(f"Failed to update driver location: {str(e)}")

        # Update delivery status
        now = _utc_now_iso()
        deliveries_table.update_item(
            Key={"delivery_id": delivery_id},
            UpdateExpression="SET #s = :status, delivery_time = :time, updated_at = :updated",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": "DELIVERED",
                ":time": now,
                ":updated": now,
            },
        )

        # Update order status to DELIVERED
        order_id = delivery.get("order_id")
        user_id = None
        if order_id:
            try:
                # Get user_id from order for WebSocket notification
                order_response = orders_table.get_item(Key={"order_id": order_id})
                order = order_response.get("Item")
                user_id = order.get("user_id") if order else None

                orders_table.update_item(
                    Key={"order_id": order_id},
                    UpdateExpression="SET #s = :status, updated_at = :updated",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":status": "DELIVERED",
                        ":updated": now,
                    },
                )

                # Emit OrderStatusChanged event for WebSocket notification
                event_detail = {
                    "order_id": order_id,
                    "status": "DELIVERED",
                    "timestamp": now,
                }
                if user_id:
                    event_detail["user_id"] = user_id

                events_client.put_events(
                    Entries=[{
                        "Source": "fooddelivery.orders",
                        "DetailType": "OrderStatusChanged",
                        "Detail": json.dumps(event_detail, default=str),
                        "EventBusName": EVENT_BUS_NAME,
                    }]
                )
            except Exception as e:
                print(f"Failed to update order status: {str(e)}")

        # Update driver status back to available
        drivers_table = dynamodb.Table(os.environ.get("DRIVERS_TABLE", "FoodDelivery-Drivers"))
        try:
            drivers_table.update_item(
                Key={"driver_id": driver_id},
                UpdateExpression="SET #s = :status",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":status": "available"},
            )
        except Exception as e:
            print(f"Warning: Could not update driver status: {str(e)}")

        # Emit event for WebSocket notification to customer (legacy)
        events_client.put_events(
            Entries=[{
                "Source": "fooddelivery.delivery",
                "DetailType": "DeliveryCompleted",
                "Detail": json.dumps({
                    "delivery_id": delivery_id,
                    "order_id": order_id,
                    "status": "DELIVERED",
                    "delivery_time": now,
                }, default=str),
                "EventBusName": EVENT_BUS_NAME,
            }]
        )

        return response(200, {
            "message": "Delivery marked as completed",
            "delivery_id": delivery_id,
            "status": "DELIVERED",
        })
    except Exception as e:
        print(f"Error handling completion: {str(e)}")
        return response(500, {"error": str(e)})
