# Testing & Development Tools

## Setting a Preferred Driver for Order Assignment

During testing and development, you can configure the system to assign all orders to a specific driver.

### Steps:

#### 1. Get Your Driver ID

1. Log in as a driver at: https://d2kw29kzxm9il.cloudfront.net
2. Navigate to **Profile** page
3. Fill out your driver information:
   - Name
   - Phone
   - Vehicle Type
   - License Plate
   - Driver's License Number
4. Click **Save Profile** (this creates your driver record)
5. Open your browser's developer console (press F12)
6. Look for the log message: `User ID (for driver assignment): <your-driver-id>`
7. Copy the driver ID

#### 2. Set the Preferred Driver

Run the script with your driver ID:

```bash
cd tools
./set_preferred_driver.sh <your-driver-id>
```

**Example:**
```bash
./set_preferred_driver.sh 12345678-1234-1234-1234-123456789abc
```

#### 3. Verify Setup

Make sure:
- ✅ Driver profile is saved with all required fields
- ✅ Driver has WebSocket connected (shows "Connected" in driver dashboard)
- ✅ Driver status is "available"

#### 4. Test Order Assignment

1. Log in as a customer (or create a new customer account)
2. Browse restaurants and add items to cart
3. Place an order
4. The driver should receive an offer pop-up within a few seconds

### Clearing the Preferred Driver

To go back to normal driver assignment:

```bash
./set_preferred_driver.sh clear
```

## Troubleshooting

**Driver not receiving offers?**
- Check that WebSocket is connected (visible in driver dashboard)
- Verify driver status is "available" in FoodDelivery-Drivers DynamoDB table
- Check CloudWatch logs for driver-service and websocket-broadcaster

**"Driver not found" errors?**
- Make sure you saved the driver profile at least once
- Check that the driver exists in FoodDelivery-Drivers table
- Verify the driver_id matches your user_id from Cognito

**Want to check DynamoDB directly?**
```bash
# List all drivers
aws dynamodb scan --table-name FoodDelivery-Drivers --region us-west-1

# Get specific driver
aws dynamodb get-item --table-name FoodDelivery-Drivers \
  --key '{"driver_id":{"S":"<your-driver-id>"}}' \
  --region us-west-1
```
