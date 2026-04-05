#!/bin/bash

# Script to update local frontend config for local testing
# Run this if you need to update your local config.js after redeployment

set -e

REGION=${AWS_REGION:-us-west-1}
FRONTEND_CONFIG="../frontend/js/config.js"

echo "Fetching configuration from AWS..."

# Get API URL
API_URL=$(aws cloudformation describe-stacks \
    --stack-name FoodDelivery-Api \
    --region $REGION \
    --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
    --output text)

# Get Cognito User Pool ID
USER_POOL_ID=$(aws cloudformation describe-stacks \
    --stack-name FoodDelivery-Auth \
    --region $REGION \
    --query "Stacks[0].Outputs[?contains(ExportName, 'UserPool') && contains(OutputValue, 'us-west')].OutputValue | [0]" \
    --output text)

# Get Cognito Client ID
CLIENT_ID=$(aws cloudformation describe-stacks \
    --stack-name FoodDelivery-Auth \
    --region $REGION \
    --query "Stacks[0].Outputs[?contains(ExportName, 'WebClient')].OutputValue | [0]" \
    --output text)

# Remove trailing slash from API URL
API_URL=${API_URL%/}

echo "API URL: $API_URL"
echo "User Pool ID: $USER_POOL_ID"
echo "Client ID: $CLIENT_ID"
echo ""
echo "Updating local config..."

# Update the config.js file
cat > $FRONTEND_CONFIG << EOF
// Auto-generated configuration file for local testing
// This file is automatically deployed to S3 by CDK
// For local testing, this contains your current deployment values
// Last updated: $(date)

window.APP_CONFIG = {
    API_BASE_URL: "$API_URL",
    COGNITO_USER_POOL_ID: "$USER_POOL_ID",
    COGNITO_CLIENT_ID: "$CLIENT_ID",
    COGNITO_REGION: "$REGION"
};
EOF

echo "Local config updated successfully!"
echo "You can now test the frontend locally."
