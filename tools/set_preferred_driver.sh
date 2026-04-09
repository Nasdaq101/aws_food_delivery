#!/bin/bash

# Script to set the preferred driver for order assignment (testing/development)
# Usage: ./set_preferred_driver.sh <driver_id>

if [ -z "$1" ]; then
    echo "Usage: ./set_preferred_driver.sh <driver_id>"
    echo ""
    echo "To find your driver ID:"
    echo "1. Log in as the driver at https://d2kw29kzxm9il.cloudfront.net"
    echo "2. Go to Profile page"
    echo "3. Open browser console (F12)"
    echo "4. Look for: 'User ID (for driver assignment): <your-id>'"
    echo ""
    echo "To clear the preferred driver:"
    echo "./set_preferred_driver.sh clear"
    exit 1
fi

DRIVER_ID="$1"
FUNCTION_NAME="FoodDelivery-driver-service"

if [ "$DRIVER_ID" = "clear" ]; then
    echo "Clearing preferred driver..."
    aws lambda update-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --environment "Variables={DRIVERS_TABLE=FoodDelivery-Drivers}" \
        --region us-west-1
    echo "Preferred driver cleared. Orders will be assigned to any available driver."
else
    echo "Setting preferred driver to: $DRIVER_ID"
    aws lambda update-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --environment "Variables={DRIVERS_TABLE=FoodDelivery-Drivers,PREFERRED_DRIVER_ID=$DRIVER_ID}" \
        --region us-west-1
    echo "Preferred driver set. All orders will be assigned to this driver."
    echo ""
    echo "Make sure the driver:"
    echo "1. Has filled out their profile (vehicle type, license, etc.)"
    echo "2. Is marked as 'available' in the Drivers table"
    echo "3. Has WebSocket connected (visible in driver dashboard)"
fi
